"""The agent harness: a planner loop over Module A, with results held by reference.

Approved 2026-10-05. One question may need several queries:
cheap reconnaissance first (how many rows, which values, what period), then the
real ones, then perhaps a follow-up where the data invites it. A small fast
**planner** model decides each step; it never writes SQL.

    question ─► planner ─┬─ ask_sql ──► Module A ─► guardrail ─► DuckDB ─► store (rN) ─┐
                 ▲        ├─ peek / describe ──► store ─────────────────────────────────┤
                 │        └─ finish(findings) ─► HarnessResult                          │
                 └──────────────────── observation (a digest, never the whole result) ──┘

What this module owns is the loop and its limits. What it does *not* own is
reaching the data: `ask_sql` is handed in by the service layer, which knows the
conversation, its folder and Module A. Keeping that out is what lets the suite
drive the loop with a stubbed planner over real DuckDB results, and keeps the
orchestrator package free of any dependency on `app/services`.

The synthesis (a bigger model) and the charts (Module C) come after the loop and
are not called from here — see `services/investigation.py`.

Rules that hold on every path:

* **Hard caps per mode** on planner turns, SQL calls and tokens. When one is hit
  the loop stops and the answer says what it did not reach; it never runs on.
* **One retry with the critique** when a query fails — the failure and the retry
  are two visible steps, not one quiet one.
* **Each step is labelled** `context` or `insight` by the planner (the
  multi-chart rule), and `finish` names the findings the user should see.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.orchestrator import client
from app.orchestrator.results import ResultLookupError, ResultStore, StoredResult

logger = logging.getLogger("alibi.orchestrator.harness")

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "planner_system.md"
_DEEP_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "planner_deep.md"
_PURPOSES = ("context", "insight")
#: A thought longer than this is cut at a word, with an ellipsis. The prompt asks
#: for one short sentence; this is the backstop, not the norm.
_THOUGHT_CHARS = 320
#: Planner replies in a row that could not be read before the loop gives up on it.
_MAX_UNREADABLE = 2


class PlannerUnavailable(Exception):
    """The planner model is unconfigured, unreachable, or produced nothing usable."""


class ToolFailed(Exception):
    """A query that did not produce rows — the planner may correct it.

    `code`/`message` are the same pair the synchronous path raises as an HTTP
    error (`sql_rejected`, `execution_failed`, …), so a run whose every query
    failed ends with the error the user would have seen before the harness.
    """

    def __init__(
        self, code: str, message: str, sql: str | None = None, *, user_message: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        #: For the planner (and the trail): what went wrong, technically.
        self.message = message
        self.sql = sql
        #: For the user, when it differs: the guardrail's own copy.
        self.user_message = user_message or message


@dataclass(frozen=True)
class SqlOutcome:
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    tokens: int = 0


#: `ask_sql(question, critique, hints)` -> rows, or `ToolFailed`. `critique` is
#: the previous attempt's error on the one automatic retry; `hints` is the
#: planner's BIRD-style external knowledge for Module A ("attended refers to
#: estado = 'Atendido'"), or None. Anything else it raises (Module A
#: unreachable, the folder gone) is not the planner's to fix and ends the run.
AskSql = Callable[[str, str | None, str | None], SqlOutcome]

#: The chart forms the planner may name for a finding, as Module C's training
#: questions name them (nvBench: "… in a line chart"). `none` names no form and
#: leaves Module C to decide, as it does for a single figure.
CHART_FORMS = ("line", "bar", "pie", "scatter", "none")
_HINT_CHARS = 600


@dataclass(frozen=True)
class Budget:
    max_sql: int
    max_turns: int
    max_tokens: int

    @classmethod
    def for_mode(cls, deep: bool, settings: Settings | None = None) -> Budget:
        s = settings or get_settings()
        if deep:
            return cls(s.harness_deep_max_sql, s.harness_deep_max_turns, s.harness_deep_max_tokens)
        return cls(s.harness_quick_max_sql, s.harness_quick_max_turns, s.harness_quick_max_tokens)


@dataclass
class HarnessResult:
    store: ResultStore
    findings: list[StoredResult]
    #: What the investigation could not reach, when a cap stopped it (or the
    #: planner said so at `finish`). `None` for a complete investigation.
    cut_short: str | None
    tokens: int
    #: Every query attempt and every look at a result, in order, as `step`
    #: recorded it — "How I got here".
    attempts: list[Any] = field(default_factory=list)
    #: The last query failure, for a run in which nothing succeeded.
    last_failure: ToolFailed | None = None
    #: The chart form the planner named for each finding, by handle, at
    #: `finish` — the request Module C was trained to answer.
    chart_forms: dict[str, str] = field(default_factory=dict)


def _system_prompt(deep: bool = False) -> str:
    prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    if deep:
        prompt += "\n" + _DEEP_PROMPT_PATH.read_text(encoding="utf-8")
    return prompt


def _status(budget: Budget, sql_used: int, turns: int, tokens: int) -> str:
    return (
        f"Budget left: {budget.max_sql - sql_used} questions to the data, "
        f"{budget.max_turns - turns} turns, ~{max(0, budget.max_tokens - tokens)} tokens."
    )


def _thought(action: dict[str, Any] | None) -> str | None:
    """The planner's one-sentence note on why it took this step, for the progress view."""
    raw = action.get("thought") if action else None
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    if len(text) > _THOUGHT_CHARS:
        text = text[: _THOUGHT_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text or None


def _read_action(text: str) -> dict[str, Any] | None:
    try:
        action = client.extract_json(text)
    except (ValueError, json.JSONDecodeError):
        return None
    return action if isinstance(action.get("action"), str) else None


class _Loop:
    def __init__(
        self,
        question: str,
        *,
        data_overview: str,
        ask_sql: AskSql,
        step: Callable[..., AbstractContextManager[Any]],
        budget: Budget,
        settings: Settings,
        sql_model: str | None = None,
        deep: bool = False,
    ) -> None:
        self.question = question
        self.sql_model = sql_model
        self.ask_sql = ask_sql
        self.step = step
        self.budget = budget
        self.config = client.planner_config(settings)
        self.max_tokens = settings.planner_max_tokens
        self.store = ResultStore(budget_tokens=settings.harness_observation_tokens)
        self.sql_used = 0
        self.turns = 0
        self.tokens = 0
        self.attempts: list[Any] = []
        self.last_failure: ToolFailed | None = None
        self.chart_forms: dict[str, str] = {}
        self.messages: list[client.Message] = [
            {"role": "system", "content": _system_prompt(deep)},
            {
                "role": "user",
                "content": f"The user asked: {question}\n\n{data_overview}\n\n"
                + _status(budget, 0, 0, 0),
            },
        ]

    # ---- one planner turn ------------------------------------------------------

    def _plan(self) -> dict[str, Any] | None:
        """Ask the planner for its next action. `None` if it would not say."""
        for _ in range(_MAX_UNREADABLE):
            self.turns += 1
            try:
                completion = client.complete(self.config, self.messages, max_tokens=self.max_tokens)
            except client.LlmUnavailable as err:
                raise PlannerUnavailable(str(err)) from err
            self.tokens += completion.prompt_tokens + completion.completion_tokens
            self.messages.append({"role": "assistant", "content": completion.text})
            action = _read_action(completion.text)
            if action is not None:
                return action
            self._observe(
                "Your reply was not a JSON object with an `action`. Reply with exactly one "
                "JSON object, one of: ask_sql, peek, describe, finish."
            )
            if self.turns >= self.budget.max_turns:
                break
        return None

    def _observe(self, text: str) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": f"{text}\n\n{_status(self.budget, self.sql_used, self.turns, self.tokens)}",
            }
        )

    # ---- tools -----------------------------------------------------------------

    def _attempt(
        self,
        label: str,
        purpose: str,
        question: str,
        critique: str | None,
        thought: str | None,
        hints: str | None,
    ) -> SqlOutcome:
        with self.step(
            label, purpose=purpose, thought=thought, tool="sql", model=self.sql_model, question=question
        ) as current:
            self.attempts.append(current)
            current.hints = hints
            try:
                outcome = self.ask_sql(question, critique, hints)
            except ToolFailed as failed:
                current.sql = failed.sql
                raise
            current.sql = outcome.sql
            current.rowCount = len(outcome.rows)
        self.tokens += outcome.tokens
        return outcome

    def _ask_sql(self, action: dict[str, Any]) -> str:
        question = str(action.get("question") or "").strip()
        if not question:
            return "ask_sql needs a `question`."
        if self.sql_used >= self.budget.max_sql:
            return "No questions to the data are left in the budget. Call finish."
        purpose = action.get("purpose") if action.get("purpose") in _PURPOSES else "insight"
        label = str(action.get("label") or "").strip()[:80] or "Consultando los datos"
        raw_hints = action.get("hints")
        hints = " ".join(raw_hints.split())[:_HINT_CHARS] if isinstance(raw_hints, str) else ""
        hints = hints or None
        self.sql_used += 1
        try:
            outcome = self._attempt(label, purpose, question, None, _thought(action), hints)
        except ToolFailed as first:
            logger.info("harness query failed, retrying with the critique: %s", first.message)
            try:
                outcome = self._attempt(
                    f"Reintentando: {label}",
                    purpose,
                    question,
                    first.message,
                    "La consulta que escribió el modelo de SQL falló; le pido otra versión "
                    "mostrándole el error.",
                    hints,
                )
            except ToolFailed as second:
                self.last_failure = second
                return (
                    f"That question failed twice. Last error: {second.message}\n"
                    "Ask a corrected or simpler question, or finish with what you have."
                )
        result = self.store.add(
            question=question,
            purpose=purpose,
            sql=outcome.sql,
            columns=outcome.columns,
            rows=outcome.rows,
            truncated=outcome.truncated,
        )
        return self.store.digest(result.handle)

    def _about(self, handle: Any) -> str | None:
        """The question behind a result handle, so a look at it says what it looks at."""
        try:
            return self.store.get(str(handle)).question
        except ResultLookupError:
            return None

    def _look(self, label: str, kind: str, action: dict[str, Any], read: Callable[[], str]) -> str:
        with self.step(label, thought=_thought(action), tool=kind, question=self._about(action.get("result"))) as current:
            self.attempts.append(current)
            return read()

    def _peek(self, action: dict[str, Any]) -> str:
        where = action.get("where")
        columns = action.get("columns")
        return self.store.peek(
            str(action.get("result", "")),
            columns=[str(c) for c in columns] if isinstance(columns, list) and columns else None,
            where=where if isinstance(where, dict) else None,
            order_by=str(action["order_by"]) if action.get("order_by") else None,
            descending=bool(action.get("descending", False)),
            offset=int(action.get("offset") or 0),
            limit=int(action.get("limit") or 20),
        )

    def _describe(self, action: dict[str, Any]) -> str:
        return self.store.describe(str(action.get("result", "")), str(action.get("column", "")))

    # ---- the loop ----------------------------------------------------------------

    def _over_budget(self) -> str | None:
        if self.turns >= self.budget.max_turns:
            return "the investigation used all of its steps"
        if self.tokens >= self.budget.max_tokens:
            return "the investigation used all of its effort budget"
        return None

    def _findings(self, named: Any) -> list[StoredResult]:
        """The results the planner kept, falling back to every insight result.

        A planner that names nothing, or names handles that do not exist, still
        ran queries whose results the user should see: the insight ones, or the
        last result when it labelled everything context.
        """
        chosen: list[StoredResult] = []
        for entry in named if isinstance(named, list) else []:
            # A finding is a handle, or {"result": handle, "chart": form}.
            handle = entry.get("result") if isinstance(entry, dict) else entry
            try:
                result = self.store.get(str(handle))
            except ResultLookupError:
                continue
            if result not in chosen:
                chosen.append(result)
                form = str(entry.get("chart") or "").strip().lower() if isinstance(entry, dict) else ""
                if form in CHART_FORMS and form != "none":
                    self.chart_forms[result.handle] = form
        if not chosen:
            chosen = [r for r in self.store.all() if r.purpose == "insight"]
        if not chosen and self.store.all():
            chosen = [self.store.all()[-1]]
        return chosen

    def run(self) -> HarnessResult:
        cut_short: str | None = None
        named: Any = None
        # The first plan's thought belongs to the step it leads to, which shows
        # it — repeating it here made the progress view say the same thing twice.
        with self.step("Leyendo tu pregunta y los datos"):
            action = self._plan()
        while True:
            if action is None:
                cut_short = cut_short or "the planner stopped responding"
                break
            kind = action.get("action")
            if kind == "finish":
                named = action.get("findings")
                unreached = action.get("unreached")
                cut_short = str(unreached).strip() if isinstance(unreached, str) and unreached.strip() else None
                break
            try:
                if kind == "ask_sql":
                    observation = self._ask_sql(action)
                elif kind == "peek":
                    observation = self._look(
                        "Revisando el detalle de los resultados", "peek", action, lambda: self._peek(action)
                    )
                elif kind == "describe":
                    observation = self._look(
                        f"Examinando la columna {action.get('column') or ''}".strip(),
                        "describe",
                        action,
                        lambda: self._describe(action),
                    )
                else:
                    observation = f"Unknown action {kind!r}. Use ask_sql, peek, describe or finish."
            except (ResultLookupError, ValueError, TypeError) as err:
                observation = f"That did not work: {err}"
            self._observe(observation)

            cut_short = self._over_budget()
            if cut_short:
                break
            action = self._plan()

        if action is None and not self.store.all():
            raise PlannerUnavailable("the planner produced no usable action")
        findings = self._findings(named)
        return HarnessResult(
            store=self.store,
            findings=findings,
            chart_forms=dict(self.chart_forms),
            cut_short=cut_short,
            tokens=self.tokens,
            attempts=self.attempts,
            last_failure=self.last_failure,
        )


def investigate(
    question: str,
    *,
    data_overview: str,
    ask_sql: AskSql,
    step: Callable[..., AbstractContextManager[Any]],
    deep: bool,
    settings: Settings | None = None,
    sql_model: str | None = None,
) -> HarnessResult:
    """Run the planner loop to `finish` or to a cap.

    `step(label, purpose=…, tool=…)` is the run's step recorder
    (`services/runs.py`): each query attempt and each look at a result is one
    visible step. `sql_model` names who writes the SQL, for those steps. `data_overview` describes the tables the
    planner may ask about — names, columns, types, meanings; never DDL, because
    the planner does not write SQL.

    Raises `PlannerUnavailable` when the planner is unconfigured or unreachable.
    A result with no findings means every query failed; `last_failure` says why.
    """
    settings = settings or get_settings()
    if not client.planner_config(settings).is_configured:
        raise PlannerUnavailable("no planner model configured")
    return _Loop(
        question,
        data_overview=data_overview,
        ask_sql=ask_sql,
        step=step,
        budget=Budget.for_mode(deep, settings),
        settings=settings,
        sql_model=sql_model,
        deep=deep,
    ).run()


__all__ = [
    "AskSql",
    "Budget",
    "HarnessResult",
    "PlannerUnavailable",
    "SqlOutcome",
    "ToolFailed",
    "investigate",
]
