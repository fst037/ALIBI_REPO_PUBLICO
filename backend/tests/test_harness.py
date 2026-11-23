"""The agent harness: a planner loop over Module A, then a synthesis across results.

Pinned here:

* **Results are held by reference.** A model sees a digest and pulls more with
  `peek`/`describe`, inside a budget that says when it cut something.
* **Hard caps per mode** on SQL calls, planner turns and tokens. A cap ends the
  loop and the answer is told what was not reached; nothing runs on.
* **One retry with the critique** when a query fails, and both attempts are
  visible steps.
* **Every figure is grounded in an executed result** — any step's — or the
  synthesis is retried, then refused.
* **Only findings are shown**: one finding keeps the one-result shape;
  several fill `findings`, each with its own sentence, and each can be pinned.

The planner and the synthesis are scripted here; Module A is a stub that writes
SQL from the DDL it was handed; DuckDB and the Parquet are real.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.orchestrator import client as orchestrator_client
from app.orchestrator import harness
from app.orchestrator.nodes.synthesize import SynthesisFailed, SynthesisInput, synthesize
from app.orchestrator.results import ResultLookupError, ResultStore
from app.schemas.runs import RunStep, RunStepStatus
from app.services import text2sql
from app.services.text2sql.client import Completion as SqlCompletion
from tests.conftest import default_synthesis
from tests.test_chat_execution import _dataset
from tests.test_runs import _finished, _start

ROWS = [
    {"ciudad": "Mendoza", "monto": 2500.1, "fecha": "2024-12-28"},
    {"ciudad": "Córdoba", "monto": 1234.56, "fecha": "2024-02-01"},
    {"ciudad": "Rosario", "monto": 980.0, "fecha": "2024-03-15"},
]


def _completion(payload: Any, tokens: int = 0) -> orchestrator_client.Completion:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return orchestrator_client.Completion(text=text, model="stub", prompt_tokens=tokens, completion_tokens=0)


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch):
    """Script the planner and (optionally) the synthesis on the shared transport.

    Everything else (narrative, Module C) falls through to the suite's stub.
    `state["planner"]` is a list of actions consumed in order; `state["seen"]`
    records every planner prompt so a test can read the observations.
    """
    fallback = orchestrator_client.complete
    state: dict[str, Any] = {"planner": [], "synthesis": [], "seen": [], "synthesis_seen": [], "tokens": 0}

    def _complete(config, messages, **kwargs):
        system = messages[0]["content"]
        if "You are the planner of ALIBI" in system:
            state["seen"].append(list(messages))
            action = state["planner"].pop(0) if state["planner"] else {"action": "finish", "findings": []}
            return _completion(action, state["tokens"])
        if "## What you are given" in system:
            state["synthesis_seen"].append(messages)
            if state["synthesis"]:
                return _completion(state["synthesis"].pop(0))
            return _completion(default_synthesis(messages))
        return fallback(config, messages, **kwargs)

    monkeypatch.setattr(orchestrator_client, "complete", _complete)
    return state


@pytest.fixture
def module_a(monkeypatch: pytest.MonkeyPatch):
    """Module A, writing SQL from the DDL it was given — a real table name.

    The question picks the query: "count" counts, "broken" names a column that
    does not exist (unless the retry's critique is in the prompt, when it is
    fixed), anything else ranks cities by amount.
    """
    asked: list[str] = []

    def fake_complete(config, messages, **kwargs):
        user = next(m["content"] for m in messages if m["role"] == "user")
        table = re.search(r"CREATE TABLE (\w+)", user).group(1)
        question = user.split("Question:\n", 1)[1].split("\n\nSQL:", 1)[0]
        asked.append(question)
        if "count" in question:
            sql = f"SELECT COUNT(*) AS cities FROM {table}"
        elif "broken" in question and "previous query" not in question:
            sql = f"SELECT no_such_column FROM {table}"
        elif "always fails" in question:
            sql = f"SELECT no_such_column FROM {table}"
        elif "smallest" in question:
            sql = f"SELECT ciudad, monto FROM {table} ORDER BY monto ASC LIMIT 1"
        else:
            sql = f"SELECT ciudad, monto FROM {table} ORDER BY monto DESC"
        return SqlCompletion(text=sql, model="stub", prompt_tokens=10, completion_tokens=5)

    monkeypatch.setattr(text2sql, "complete", fake_complete)
    monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "stub-sql")
    monkeypatch.setenv("ALIBI_TEXT2SQL_PROMPT_FORMAT", "generic-chat-v1")
    text2sql.get_settings.cache_clear()
    yield asked
    text2sql.get_settings.cache_clear()


def _ask(n: int, purpose: str = "insight", question: str = "amount per city", label: str = "Mirando") -> dict:
    return {"thought": f"step {n}", "action": "ask_sql", "question": question, "purpose": purpose, "label": label}


def _finish(*handles: str, unreached: str | None = None) -> dict:
    return {"thought": "done", "action": "finish", "findings": list(handles), "unreached": unreached}


# ---------------------------------------------------------------------------
# The result store
# ---------------------------------------------------------------------------


class TestResultsAreHeldByReference:
    def _store(self, budget: int = 1_500) -> tuple[ResultStore, str]:
        store = ResultStore(budget_tokens=budget)
        result = store.add(question="amount per city", purpose="insight", sql="SELECT 1",
                           columns=["ciudad", "monto", "fecha"], rows=ROWS, truncated=False)
        return store, result.handle

    def test_a_digest_profiles_the_columns_and_shows_a_sample(self) -> None:
        store, handle = self._store()
        digest = store.digest(handle)
        assert handle == "r1"
        assert "3 rows" in digest
        assert "- ciudad: nominal; 3 distinct" in digest
        assert "from 980.0 to 2500.1" in digest
        assert "Mendoza" in digest

    def test_peek_filters_sorts_and_says_how_much_it_shows(self) -> None:
        store, handle = self._store()
        peeked = store.peek(handle, columns=["ciudad"], order_by="monto", limit=2)
        assert peeked.startswith("r1: rows 1-2 of 3")
        assert json.loads(peeked.split("\n", 1)[1]) == [{"ciudad": "Rosario"}, {"ciudad": "Córdoba"}]
        filtered = store.peek(handle, where={"ciudad": "Mendoza"})
        assert "of 1 matching the filter" in filtered

    def test_describe_lists_the_top_values(self) -> None:
        store, handle = self._store()
        assert "Córdoba (1)" in store.describe(handle, "ciudad")

    def test_an_unknown_handle_or_column_is_a_correctable_error(self) -> None:
        store, handle = self._store()
        with pytest.raises(ResultLookupError, match="known: r1"):
            store.digest("r9")
        with pytest.raises(ResultLookupError, match="no column"):
            store.peek(handle, columns=["profit"])

    def test_an_observation_over_budget_is_cut_and_says_so(self) -> None:
        store, handle = self._store(budget=20)
        digest = store.digest(handle)
        assert len(digest) <= 20 * 4
        assert digest.endswith("(digest cut to fit; use describe or peek on r1)")

    def test_the_figures_it_computed_are_grounded(self) -> None:
        store, handle = self._store()
        store.digest(handle)
        assert {3.0} <= store.shown_figures()


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class _Steps:
    """A step recorder like the run's, kept in a list."""

    def __init__(self) -> None:
        self.steps: list[RunStep] = []

    @contextmanager
    def step(self, label: str, purpose: Any = None, thought: str | None = None, **fields: Any):
        current = RunStep(
            id=f"s{len(self.steps)}", label=label, purpose=purpose, thought=thought, startedAt="t", **fields
        )
        self.steps.append(current)
        try:
            yield current
        except Exception as exc:
            current.status = RunStepStatus.failed
            current.error = getattr(exc, "message", type(exc).__name__)
            raise
        else:
            current.status = RunStepStatus.done


calls_hints: list[str] = []


def _fake_sql(calls: list[tuple[str, str | None]], fail_first: int = 0):
    def ask(question: str, critique: str | None, hints: str | None = None) -> harness.SqlOutcome:
        calls.append((question, critique))
        if hints:
            calls_hints.append(hints)
        if len(calls) <= fail_first:
            raise harness.ToolFailed("execution_failed", "Binder Error: no column x", "SELECT x")
        return harness.SqlOutcome(sql="SELECT ciudad, monto", columns=["ciudad", "monto"],
                                  rows=[{k: r[k] for k in ("ciudad", "monto")} for r in ROWS],
                                  truncated=False, tokens=15)
    return ask


def _investigate(models_state, script: list[dict], *, deep: bool = False, fail_first: int = 0):
    models_state["planner"] = list(script)
    calls: list[tuple[str, str | None]] = []
    recorder = _Steps()
    result = harness.investigate("Which city sold the most?", data_overview="TABLES: ventas",
                                 ask_sql=_fake_sql(calls, fail_first), step=recorder.step, deep=deep)
    return result, calls, recorder.steps


class TestTheLoop:
    def test_reconnaissance_then_the_real_question_then_finish(self, models) -> None:
        result, calls, steps = _investigate(models, [
            _ask(1, "context", "how many cities"), _ask(2, "insight"), _finish("r2"),
        ])
        assert [r.handle for r in result.findings] == ["r2"]
        assert [s.purpose.value for s in steps if s.purpose] == ["context", "insight"]
        assert steps[1].question == "how many cities" and steps[1].rowCount == 3
        # The planner saw r1's digest before it asked its second question.
        second_turn = models["seen"][1]
        assert "r1 [context] — how many cities" in second_turn[-1]["content"]
        assert result.cut_short is None

    def test_the_planners_thought_is_on_every_step_it_drives(self, models) -> None:
        peek = {"thought": "look", "action": "peek", "result": "r1"}
        result, _, steps = _investigate(models, [_ask(1), peek, _finish("r1")])
        # The query and the peek (which runs no query) each carry the note the
        # planner gave for that turn. The opening read does not repeat the first
        # one: the progress view showed it twice in a row.
        assert [s.thought for s in steps] == [None, "step 1", "look"]
        # Each says what it called, and the peek names the result it looked at.
        assert [s.tool.value if s.tool else None for s in steps] == [None, "sql", "peek"]
        assert steps[2].question == steps[1].question
        # The look is on the trail too, not only on the live view.
        assert [a.tool.value for a in result.attempts] == ["sql", "peek"]

    def test_hints_reach_module_a_and_the_step_and_the_chart_form_is_kept(self, models) -> None:
        calls_hints.clear()
        ask = {**_ask(1), "hints": "  big refers to monto > 100 "}
        finish = {"thought": "done", "action": "finish", "findings": [{"result": "r1", "chart": "line"}]}
        result, _, steps = _investigate(models, [ask, finish])
        assert calls_hints == ["big refers to monto > 100"]
        assert steps[1].hints == "big refers to monto > 100"
        assert [r.handle for r in result.findings] == ["r1"] and result.chart_forms == {"r1": "line"}

    def test_an_unknown_or_none_chart_form_names_none(self, models) -> None:
        finish = {"thought": "done", "action": "finish",
                  "findings": [{"result": "r1", "chart": "radar"}, {"result": "r2", "chart": "none"}]}
        result, _, _ = _investigate(models, [_ask(1), _ask(2), finish])
        assert [r.handle for r in result.findings] == ["r1", "r2"] and result.chart_forms == {}

    def test_a_long_thought_is_cut_at_a_word_not_mid_word(self, models) -> None:
        long = {**_ask(1), "thought": "palabra " * 80}
        _, _, steps = _investigate(models, [long, _finish("r1")])
        thought = steps[1].thought
        assert thought.endswith("palabra…") and len(thought) <= 321

    def test_quick_stops_asking_at_two_queries(self, models) -> None:
        result, calls, _ = _investigate(models, [_ask(1), _ask(2), _ask(3), _finish("r1", "r2")])
        assert len(calls) == 2
        assert "No questions to the data are left" in models["seen"][3][-1]["content"]
        assert [r.handle for r in result.findings] == ["r1", "r2"]

    def test_deep_allows_more_queries(self, models) -> None:
        _, calls, _ = _investigate(models, [_ask(1), _ask(2), _ask(3), _finish("r3")], deep=True)
        assert len(calls) == 3

    def test_the_turn_cap_ends_the_loop_and_says_so(self, models) -> None:
        peek = {"thought": "look", "action": "peek", "result": "r1"}
        result, _, _ = _investigate(models, [_ask(1)] + [peek] * 10)
        assert result.cut_short == "the investigation used all of its steps"
        assert len(models["seen"]) == 4  # Quick's turn cap
        assert [r.handle for r in result.findings] == ["r1"]  # the insight it had

    def test_the_token_cap_ends_the_loop(self, models) -> None:
        models["tokens"] = 50_000
        result, calls, _ = _investigate(models, [_ask(1), _ask(2), _finish("r1")])
        assert len(calls) == 1
        assert result.cut_short == "the investigation used all of its effort budget"

    def test_a_failed_query_is_retried_once_with_the_critique_and_both_are_visible(self, models) -> None:
        result, calls, steps = _investigate(models, [_ask(1, label="Sumando"), _finish("r1")], fail_first=1)
        assert calls == [("amount per city", None), ("amount per city", "Binder Error: no column x")]
        queries = [s for s in steps if s.purpose]
        assert [(s.label, s.status.value) for s in queries] == [
            ("Sumando", "failed"), ("Reintentando: Sumando", "done"),
        ]
        assert queries[0].sql == "SELECT x" and queries[0].error == "Binder Error: no column x"
        assert [r.handle for r in result.findings] == ["r1"]

    def test_a_query_that_fails_twice_is_reported_to_the_planner(self, models) -> None:
        result, _, _ = _investigate(models, [_ask(1), _finish()], fail_first=2)
        assert "failed twice" in models["seen"][1][-1]["content"]
        assert result.findings == []
        assert result.last_failure is not None and result.last_failure.code == "execution_failed"

    def test_a_planner_that_never_replies_in_json_is_unavailable(self, models) -> None:
        with pytest.raises(harness.PlannerUnavailable):
            _investigate(models, ["thinking...", "still thinking"])

    def test_an_unconfigured_planner_is_unavailable(self, models, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_client, "planner_config",
                            lambda *_a, **_k: orchestrator_client.LlmConfig(model_id=""))
        with pytest.raises(harness.PlannerUnavailable):
            _investigate(models, [_ask(1)])

    def test_finish_without_findings_shows_the_insight_results(self, models) -> None:
        result, _, _ = _investigate(models, [_ask(1, "context"), _ask(2, "insight"), _finish("r7")])
        assert [r.handle for r in result.findings] == ["r2"]


# ---------------------------------------------------------------------------
# The synthesis
# ---------------------------------------------------------------------------


def _synthesis_input(**overrides: Any) -> SynthesisInput:
    store = ResultStore()
    background = store.add(question="total amount", purpose="context", sql="SELECT SUM(monto) AS total FROM t",
                           columns=["total"], rows=[{"total": 4714.66}], truncated=False)
    finding = store.add(question="amount per city", purpose="insight", sql="SELECT ciudad, monto FROM t",
                        columns=["ciudad", "monto"], rows=[{k: r[k] for k in ("ciudad", "monto")} for r in ROWS],
                        truncated=False)
    payload: dict[str, Any] = {"question": "Which city sold the most?", "findings": [finding],
                               "background": [background], "shown_figures": store.shown_figures()}
    payload.update(overrides)
    return SynthesisInput(**payload)


def _says(summary: str, findings: list[dict] | None = None) -> dict:
    return {"summary": summary, "findings": findings or [], "key_figures": [], "confidence_score": 0.8}


class TestTheSynthesisIsGroundedAcrossEveryResult:
    def test_a_figure_from_a_background_step_is_grounded(self, models) -> None:
        models["synthesis"] = [_says("Mendoza leads with 2,500.1 of the 4,714.66 total.")]
        result = synthesize(_synthesis_input())
        assert result.source == "model" and result.finding_texts == {}

    def test_an_invented_figure_is_retried_with_the_critique(self, models) -> None:
        models["synthesis"] = [_says("Mendoza leads with 53% of sales."), _says("Mendoza leads with 2,500.1.")]
        result = synthesize(_synthesis_input())
        assert result.source == "model_retry"
        assert "“53%” does not appear" in models["synthesis_seen"][1][-1]["content"]

    def test_an_answer_that_stays_ungrounded_is_refused(self, models) -> None:
        models["synthesis"] = [_says("Sales grew 34%."), _says("Sales grew 34%.")]
        with pytest.raises(SynthesisFailed):
            synthesize(_synthesis_input())

    def test_result_handles_are_internal(self, models) -> None:
        models["synthesis"] = [_says("As r2 shows, Mendoza leads."), _says("Mendoza leads.")]
        assert synthesize(_synthesis_input()).source == "model_retry"

    def test_several_findings_each_need_their_sentence(self, models) -> None:
        payload = _synthesis_input()
        two = _synthesis_input(findings=payload.findings + payload.background, background=[])
        models["synthesis"] = [
            _says("Mendoza leads.", [{"result": "r2", "text": "Mendoza is first."}]),
            _says("Mendoza leads.", [{"result": "r2", "text": "Mendoza is first."},
                                     {"result": "r1", "text": "The total is 4,714.66."}]),
        ]
        result = synthesize(two)
        assert result.source == "model_retry"
        assert result.finding_texts == {"r2": "Mendoza is first.", "r1": "The total is 4,714.66."}

    def test_the_model_never_sees_the_sql(self, models) -> None:
        models["synthesis"] = [_says("Mendoza leads.")]
        synthesize(_synthesis_input())
        prompt = models["synthesis_seen"][0][1]["content"]
        assert "SELECT" not in prompt and "about: amount per city" in prompt


# ---------------------------------------------------------------------------
# End to end: a run answers a question through the harness
# ---------------------------------------------------------------------------


class TestARunInvestigates:
    def test_one_finding_keeps_the_one_result_shape_with_its_trail(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1, "context", "count the cities", "Contando ciudades"),
                             _ask(2, "insight", "amount per city", "Comparando ciudades"), _finish("r2")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="¿Qué ciudad vendió más?")["id"])

        assert run["status"] == "done", run
        assert [s["label"] for s in run["steps"]] == [
            "Leyendo tu pregunta y los datos", "Contando ciudades", "Comparando ciudades",
            "Redactando la respuesta", "Armando el gráfico: Comparando ciudades",
        ]
        result = run["message"]["result"]
        assert result["naturalResponse"] == "Here is what the information shows."
        assert [r["ciudad"] for r in result["table"]["rows"]] == ["Mendoza", "Córdoba", "Rosario"]
        assert result["findings"] == []
        assert [(t["purpose"], t["question"], t["rowCount"]) for t in result["trail"]] == [
            ("context", "count the cities", 1), ("insight", "amount per city", 3),
            # Module C's call for the one finding is a step of its own.
            (None, "amount per city", None),
        ]
        assert [t["tool"] for t in result["trail"]] == ["sql", "sql", "chart"]
        assert result["trail"][0]["model"] and result["trail"][2]["model"]
        assert result["trail"][2]["detail"].startswith(("Gráfico de", "Sin gráfico"))
        assert module_a == ["count the cities", "amount per city"]

    def test_two_findings_are_both_shown_and_the_second_can_be_pinned(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        models["planner"] = [_ask(1, question="amount per city"), _ask(2, question="the smallest city"),
                             _finish("r1", "r2")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas por ciudad")["id"])

        result = run["message"]["result"]
        assert len(result["findings"]) == 2
        assert result["findings"][0]["sql"] == result["sql"]
        assert [r["ciudad"] for r in result["findings"][1]["table"]["rows"]] == ["Rosario"]
        assert all(f["text"] for f in result["findings"])

        pinned = client.post(f"/api/v1/datasets/{dataset['id']}/charts", headers=auth_headers,
                             json={"messageId": run["message"]["id"], "chartType": "bar", "findingIndex": 1})
        assert pinned.status_code == 201, pinned.text
        assert pinned.json()["result"]["sql"] == result["findings"][1]["sql"]
        assert pinned.json()["result"]["trail"] == []

        missing = client.post(f"/api/v1/datasets/{dataset['id']}/charts", headers=auth_headers,
                              json={"messageId": run["message"]["id"], "chartType": "bar", "findingIndex": 5})
        assert missing.status_code == 404

    def test_a_failed_query_and_its_retry_are_in_the_trail(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1, question="the broken one", label="Sumando"), _finish("r1")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert run["status"] == "done", run
        trail = run["message"]["result"]["trail"]
        assert [(t["label"], t["status"]) for t in trail] == [
            ("Sumando", "failed"), ("Reintentando: Sumando", "done"), ("Armando el gráfico: Sumando", "done"),
        ]
        assert "no_such_column" in trail[0]["sql"] and trail[0]["error"]

    def test_when_every_query_is_refused_the_run_ends_as_a_guard(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1, question="it always fails"), _finish()]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        # As on the one-query path: a refused query is a guard, with its reason.
        assert run["status"] == "done"
        assert run["guard"]["message"]
        assert run["message"] is None

    def test_an_unconfigured_planner_is_a_503_not_a_one_query_answer(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a, monkeypatch
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        monkeypatch.setattr(orchestrator_client, "planner_config",
                            lambda *_a, **_k: orchestrator_client.LlmConfig(model_id=""))

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert run["status"] == "failed"
        assert run["error"]["code"] == "planner_unavailable"
        assert module_a == []

    def test_an_ungrounded_synthesis_fails_the_run(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1), _finish("r1")]
        models["synthesis"] = [_says("Sales grew 34%."), _says("Sales grew 34%.")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert run["status"] == "failed"
        assert run["error"]["code"] == "narrative_unavailable"

    def test_deep_is_honoured_from_the_request(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r3")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas", mode="deep")["id"])

        assert run["status"] == "done", run
        assert len(module_a) == 3

    def test_a_deep_run_notifies_but_a_quick_one_does_not(
        self, client: TestClient, auth_headers: dict, conversation_over, models, module_a
    ) -> None:
        def titles() -> list[str]:
            return [n["title"] for n in client.get("/api/v1/notifications", headers=auth_headers).json()]

        conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
        models["planner"] = [_ask(1), _finish("r1")]
        _finished(client, auth_headers, conversation_id,
                  _start(client, auth_headers, conversation_id, question="ventas", mode="quick")["id"])
        assert "Tu investigación está lista" not in titles()

        models["planner"] = [_ask(1), _finish("r1")]
        _finished(client, auth_headers, conversation_id,
                  _start(client, auth_headers, conversation_id, question="ventas", mode="deep")["id"])
        latest = client.get("/api/v1/notifications", headers=auth_headers).json()[0]
        assert latest["title"] == "Tu investigación está lista"
        assert latest["action"]["href"] == f"/app?conversation={conversation_id}"


# ---------------------------------------------------------------------------
# Quick or Deep: the conversation's default, and a one-question override
# ---------------------------------------------------------------------------


class TestTheModeIsTheConversationsUnlessOneQuestionSaysOtherwise:
    def _conversation(self, client: TestClient, headers: dict, folder_of, **body) -> str:
        folder_id = folder_of(headers, [_dataset(client, headers)["id"]])
        created = client.post("/api/v1/conversations", json={"folderId": folder_id, **body}, headers=headers)
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def test_a_conversation_starts_quick_and_can_be_switched_to_deep(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of)
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()["mode"] == "quick"

        patched = client.patch(f"/api/v1/conversations/{conversation_id}", json={"mode": "deep"}, headers=auth_headers)
        assert patched.status_code == 200 and patched.json()["mode"] == "deep"
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()["mode"] == "deep"

    def test_a_run_without_a_mode_uses_the_conversations(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of, mode="deep")
        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r3")]

        started = _start(client, auth_headers, conversation_id, question="ventas")
        assert started["mode"] == "deep"
        run = _finished(client, auth_headers, conversation_id, started["id"])

        assert run["status"] == "done", run
        assert len(module_a) == 3  # Deep's budget, not Quick's two

    def test_one_question_can_override_it_without_changing_it(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of, mode="deep")
        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r1")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas", mode="quick")["id"])

        assert run["mode"] == "quick" and len(module_a) == 2
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()["mode"] == "deep"

    def test_an_unknown_mode_is_refused(self, client: TestClient, auth_headers: dict, folder_of) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of)
        refused = client.patch(f"/api/v1/conversations/{conversation_id}", json={"mode": "thorough"},
                               headers=auth_headers)
        assert refused.status_code == 422


# ---------------------------------------------------------------------------
# Deep is a Pro feature: Free answers Quick
# ---------------------------------------------------------------------------


def _switch_to(client: TestClient, headers: dict, plan_id: str) -> None:
    res = client.post("/api/v1/subscription/upgrade", headers=headers, json={"planId": plan_id, "cycle": "monthly"})
    assert res.status_code == 200, res.text


class TestDeepIsAProFeature:
    """The fixture account starts on Pro (its seed is over Free's dataset
    limit), so each test builds its conversation first and then moves to Free."""

    def _conversation(self, client: TestClient, headers: dict, folder_of, **body) -> str:
        folder_id = folder_of(headers, [_dataset(client, headers)["id"]])
        created = client.post("/api/v1/conversations", json={"folderId": folder_id, **body}, headers=headers)
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def test_the_plans_say_which_depths_they_include(self, client: TestClient) -> None:
        plans = {p["id"]: p for p in client.get("/api/v1/plans").json()}
        assert plans["free"]["limits"]["answerModes"] == ["quick"]
        assert plans["pro"]["limits"]["answerModes"] == ["quick", "deep"]

    def test_free_cannot_ask_one_question_deep(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of)
        _switch_to(client, auth_headers, "free")

        res = client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            json={"question": "ventas", "mode": "deep"},
            headers=auth_headers,
        )
        assert res.status_code == 402
        assert res.json()["error"]["code"] == "plan_upgrade_required"

    def test_free_cannot_make_deep_a_conversations_default(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of)
        _switch_to(client, auth_headers, "free")

        res = client.patch(f"/api/v1/conversations/{conversation_id}", json={"mode": "deep"}, headers=auth_headers)
        assert res.status_code == 402
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()["mode"] == "quick"

    def test_a_deep_conversation_is_answered_quick_after_a_downgrade(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of, mode="deep")
        _switch_to(client, auth_headers, "free")
        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r2")]

        started = _start(client, auth_headers, conversation_id, question="ventas")
        assert started["mode"] == "quick"
        run = _finished(client, auth_headers, conversation_id, started["id"])

        assert run["status"] == "done", run
        assert len(module_a) == 2  # Quick's budget
        # The saved default is untouched: back on Pro, it is Deep again.
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()["mode"] == "deep"

    def test_extra_queries_are_answered_quick(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        """Past the quota (overage on), Deep is refused when asked and clamped
        when it is only the conversation's default: an extra query is priced on
        a Quick answer's cost."""
        from app.services import billing_memory

        conversation_id = self._conversation(client, auth_headers, folder_of, mode="deep")
        client.put("/api/v1/billing/overage", headers=auth_headers, json={"enabled": True, "capArs": None})
        for _ in range(250):
            billing_memory.record_query("u", rows_returned=1, cost_usd=0.0, cost_ars=0.0, source_name=None)

        asked = client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            json={"question": "ventas", "mode": "deep"},
            headers=auth_headers,
        )
        assert asked.status_code == 402
        assert asked.json()["error"]["code"] == "deep_outside_quota"

        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r2")]
        started = _start(client, auth_headers, conversation_id, question="ventas")
        assert started["mode"] == "quick"
        assert _finished(client, auth_headers, conversation_id, started["id"])["status"] == "done"
        assert len(module_a) == 2


# ---------------------------------------------------------------------------
# A question answered by the planner is one query on the plan's quota
#
# ---------------------------------------------------------------------------


def _queries_used(client: TestClient, headers: dict) -> int:
    return client.get("/api/v1/subscription", headers=headers).json()["usage"]["queries"]["used"]


class TestAQuestionCountsAgainstTheQuota:
    """`test_business_model.py` pins the quota with hand-written SQL; these pin
    it on the path almost every question takes, the planner's."""

    def _conversation(self, client: TestClient, headers: dict, folder_of, **body) -> str:
        folder_id = folder_of(headers, [_dataset(client, headers)["id"]])
        created = client.post("/api/v1/conversations", json={"folderId": folder_id, **body}, headers=headers)
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def test_an_answered_question_is_one_query_whatever_it_ran(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of, mode="deep")
        before = _queries_used(client, auth_headers)
        models["planner"] = [_ask(1), _ask(2), _ask(3), _finish("r3", "r1")]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert run["status"] == "done", run
        assert len(module_a) == 3
        assert _queries_used(client, auth_headers) == before + 1

    def test_the_answer_is_billed_with_its_model_calls(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a, monkeypatch
    ) -> None:
        from app.core import usage_meter
        from app.services import billing

        recorded: list[dict] = []
        real = billing.record_query
        monkeypatch.setattr(billing, "record_query", lambda user_id, **kw: (recorded.append(kw), real(user_id, **kw)))
        # The stub replaces the client's `complete`, so it reports its tokens
        # the way the real one does (`text2sql.client._metered`).
        stub = text2sql.complete

        def metered(config, messages, **kwargs):
            completion = stub(config, messages, **kwargs)
            usage_meter.record(completion.model, completion.prompt_tokens, completion.completion_tokens)
            return completion

        monkeypatch.setattr(text2sql, "complete", metered)
        conversation_id = self._conversation(client, auth_headers, folder_of)
        models["planner"] = [_ask(1), _finish("r1")]

        _finished(client, auth_headers, conversation_id,
                  _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert len(recorded) == 1
        # Module A's one query is on the bill, not only the planner's turns.
        assert any(call.prompt_tokens == 10 and call.completion_tokens == 5 for call in recorded[0]["calls"])
        assert recorded[0]["rows_returned"] == len(ROWS)

    def test_a_question_with_no_answer_costs_nothing(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        conversation_id = self._conversation(client, auth_headers, folder_of)
        before = _queries_used(client, auth_headers)
        models["planner"] = [_finish()]

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert run["status"] == "failed", run
        assert _queries_used(client, auth_headers) == before

    def test_free_answers_its_20th_question_and_stops_at_the_21st(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        from app.services import billing_memory

        conversation_id = self._conversation(client, auth_headers, folder_of)
        _switch_to(client, auth_headers, "free")
        for _ in range(19 - _queries_used(client, auth_headers)):
            billing_memory.record_query("u", rows_returned=1, cost_usd=0.0, cost_ars=0.0, source_name=None)
        assert _queries_used(client, auth_headers) == 19

        models["planner"] = [_ask(1), _finish("r1")]
        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas")["id"])
        assert run["status"] == "done", run
        assert _queries_used(client, auth_headers) == 20

        refused = client.post(
            f"/api/v1/conversations/{conversation_id}/runs", json={"question": "ventas"}, headers=auth_headers
        )
        assert refused.status_code == 402
        assert refused.json()["error"]["code"] == "plan_limit_reached"
        assert _queries_used(client, auth_headers) == 20

    def test_a_run_queued_before_the_quota_ran_out_is_stopped(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a
    ) -> None:
        """Checked again inside the run, before any model call."""
        from app.core.errors import ApiException
        from app.services import billing_memory, investigation

        conversation_id = self._conversation(client, auth_headers, folder_of)
        _switch_to(client, auth_headers, "free")
        for _ in range(20 - _queries_used(client, auth_headers)):
            billing_memory.record_query("u", rows_returned=1, cost_usd=0.0, cost_ars=0.0, source_name=None)

        @contextmanager
        def _step(*_args, **_kwargs):
            yield RunStep(label="x", status=RunStepStatus.running)

        with pytest.raises(ApiException) as refused:
            investigation.answer_question("u", conversation_id, "ventas", deep=False, step=_step)
        assert refused.value.status_code == 402
        assert models["seen"] == [] and module_a == []

    def test_the_plans_row_limit_applies_to_the_planners_queries(
        self, client: TestClient, auth_headers: dict, folder_of, models, module_a, monkeypatch
    ) -> None:
        from app.services import investigation

        limits: list[int | None] = []
        real = investigation.query.run_sql
        monkeypatch.setattr(investigation.query, "run_sql",
                            lambda sql, tables, **kw: (limits.append(kw.get("limit")), real(sql, tables, **kw))[1])
        conversation_id = self._conversation(client, auth_headers, folder_of)
        _switch_to(client, auth_headers, "free")
        models["planner"] = [_ask(1), _finish("r1")]

        _finished(client, auth_headers, conversation_id,
                  _start(client, auth_headers, conversation_id, question="ventas")["id"])

        assert limits == [1000]


def test_module_c_is_asked_for_the_named_form_in_nvbenchs_own_words() -> None:
    from app.services.investigation import _chart_request, _first_line

    assert _chart_request("Total amount per month in 2025.", "line") == (
        "Total amount per month in 2025, in a line chart."
    )
    assert _chart_request("Total amount per month?", None) == "Total amount per month?"
    # A retry's critique keeps DuckDB's first line, not its caret diagram.
    assert _first_line("Parser Error: near WHERE\n\nLINE 1: …\n   ^") == "Parser Error: near WHERE"
