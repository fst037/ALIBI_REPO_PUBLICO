"""`explain_result` — turn an executed result into a sentence for the user.

Where this sits: after the SQL guardrail and after the query has actually run
against the read-only data plane. It is a step of the orchestrator, not of
Module A: the Text-to-SQL agent returns `sql_query` + `result_table` and stops
there, because it is a model fine-tuned on SQL and prose is not its job. The
narrative is the orchestrator's contribution — the modules supply the technical
capabilities, the orchestrator supplies the system's coherence (objective OE6).

It is a reactive node: input → output, one model call, no tool use and no
multi-step loop of its own. There is nothing here for an agent to decide; the
query already ran. **The loop that decides how many queries a question needs
belongs one level up** — `orchestrator/harness.py`, whose answers are written by
`nodes/synthesize.py`. This node now narrates the hand-written-SQL path and the
synchronous `execute`.

    ExplainInput ─► prompt ─► model ─► JSON ─► schema ─► Reflection ─► Explanation
                                                  │           │
                                                  └── retry ◄─┘ once, with the critique
                                                              │
                                                     still bad ▼
                                                        NarrativeFailed

Two properties hold on every path out of this function:

* **Nothing unverified is returned.** Every narrative that leaves here has passed
  `validation.review` — grounded figures, no technical vocabulary, at most three
  sentences.
* **The model never sees the SQL.** It is logged beside the narrative for
  traceability (rule 9) and deliberately kept out of the prompt: the reliable way
  to stop a model mentioning a JOIN is never to show it one.

What is *not* a property any more: this used to always return, degrading to a
deterministic template when the provider was unreachable or every attempt failed
validation. It now raises `NarrativeFailed`. A canned sentence under a real
results table reads exactly like a real answer, and the user has no way to tell
which one they got — which defeats the grounding validator sitting right above
it. Reporting the failure is the honest option, and the caller turns it into a
503.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings
from app.orchestrator import client, formatting, validation
from app.orchestrator.contracts import ExplainInput, Explanation

logger = logging.getLogger("alibi.orchestrator.explain")

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "explain_result_system.md"


class NarrativeFailed(Exception):
    """No verified narrative could be produced for this result."""


def _system_prompt() -> str:
    """Read from disk on every call, not cached at import.

    The prompt is the part of this node that gets tuned, and reading it live means
    editing the Markdown and re-asking a question is the whole iteration loop —
    no restart. It is a local file read, next to a network call that costs
    hundreds of milliseconds.
    """
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


#: Kept as a module-level name because this node's tests and Module C's both
#: reach for it; the implementation moved next to the transport so the two nodes
#: cannot drift apart in how tolerant they are of a fenced reply.
_extract_json = client.extract_json


def _data_block(payload: ExplainInput, max_rows: int) -> str:
    """The rows as the model sees them: business names, no identifiers.

    Column keys are replaced with their business labels here rather than left raw.
    It is the cheapest available defence for rule 2 — a model shown a column
    called `fch_vcto` will reach for it when it needs a noun, and no amount of
    instruction reliably beats the name sitting in front of it. Falling back to
    `humanize` means even an un-enriched result arrives as "Total revenue" rather
    than `total_revenue`.
    """
    sample = payload.rows[:max_rows]
    renamed = [
        {
            formatting.humanize(column, payload.column_labels): formatting.display(
                row.get(column), payload.locale
            )
            for column in payload.columns
        }
        for row in sample
    ]
    lines = []
    if payload.question.strip():
        lines.append(f"The user asked: {payload.question}")
    else:
        # The hand-edited-SQL path: the user wrote a query instead of a
        # question, and the query itself is deliberately withheld (see the
        # module docstring). Describing the result on its own terms is the
        # honest option; inventing a question for it would put words in the
        # user's mouth that the narrative then answers.
        lines.append("The user ran a report of their own. Describe what came back.")
    lines.append(f"Entries found: {payload.row_count}")
    if payload.source_label:
        lines.append(f"This is about: {payload.source_label}")
    if payload.truncated or len(sample) < payload.row_count:
        lines.append(f"You are seeing the first {len(sample)} of them; there are more.")
    lines.append("")
    lines.append("DATA:")
    lines.append(json.dumps(renamed, ensure_ascii=False, indent=None))
    return "\n".join(lines)


def _internal_names(payload: ExplainInput) -> list[str]:
    """Identifiers that must not surface in the narrative.

    The raw result-column names, plus the tables the query touched. These are
    jargon by definition, and no fixed word list could ever contain them — they
    are whatever this user happened to upload.
    """
    names = list(payload.columns)
    try:
        import sqlglot
        from sqlglot import exp

        statement = sqlglot.parse_one(payload.sql_query, read="duckdb")
        names += [t.name for t in statement.find_all(exp.Table) if t.name]
    except Exception:  # noqa: BLE001 - best-effort; the column names still apply
        pass
    return [n for n in dict.fromkeys(names) if n]


def _validated(
    text: str, payload: ExplainInput, *, source: str
) -> tuple[Explanation | None, validation.Critique]:
    """Parse, schema-check and review one completion.

    A schema failure and a rule failure both come back as a `Critique`, because
    the retry treats them identically: the model is told what was wrong and asked
    again. Distinguishing them would only matter if the two led to different
    recovery, and they don't.
    """
    try:
        raw = _extract_json(text)
    except (ValueError, json.JSONDecodeError) as err:
        return None, validation.Critique(
            problems=[f"Your reply was not valid JSON ({err}). Reply with the JSON object only."]
        )

    raw["source"] = source
    try:
        explanation = Explanation.model_validate(raw)
    except ValidationError as err:
        return None, validation.Critique(
            problems=[f"Your reply did not match the required shape: {err.errors()[0]['msg']}."]
        )

    critique = validation.review(
        explanation.narrative,
        payload.rows,
        question=payload.question,
        row_count=payload.row_count,
        internal_names=_internal_names(payload),
    )
    return (explanation if critique.ok else None), critique


def explain_result(payload: ExplainInput) -> Explanation:
    """Narrate one executed result. Raises `NarrativeFailed` if it cannot."""
    settings = get_settings()
    config = client.config_from_settings(settings)
    if not config.is_configured:
        raise NarrativeFailed("no orchestrator model configured")

    messages: list[client.Message] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _data_block(payload, settings.narrative_max_prompt_rows)},
    ]

    problems: list[str] = ["no attempt was made"]
    for attempt, source in enumerate(("model", "model_retry")):
        try:
            completion = client.complete(
                config, messages, max_tokens=settings.narrative_max_tokens
            )
        except client.LlmUnavailable as err:
            logger.error("narrative model unavailable: %s", err)
            raise NarrativeFailed(str(err)) from err

        explanation, critique = _validated(completion.text, payload, source=source)
        if explanation is not None:
            if explanation.confidence_score < settings.narrative_confidence_threshold:
                # Rule 9's Reflection trigger. The verification it calls for has
                # already happened — `_validated` grounds every figure against the
                # rows on every attempt, unconditionally, which is stricter than
                # doing it only below the threshold. So a low score is a signal to
                # record, not a reason to discard a narrative that passed.
                logger.info(
                    "narrative self-scored %.2f (below %.2f) but passed verification",
                    explanation.confidence_score,
                    settings.narrative_confidence_threshold,
                )
            _trace(payload, explanation, tokens=(completion.prompt_tokens, completion.completion_tokens))
            return explanation

        problems = critique.problems
        logger.info("narrative attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        if attempt == 0:
            # One retry, carrying the critique — the Reflection pattern. The failed
            # answer goes back in as context so the model can see what it wrote;
            # without it, "fix the figure 34" refers to nothing.
            messages = messages + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": critique.as_prompt()},
            ]

    raise NarrativeFailed("; ".join(problems))


def _trace(payload: ExplainInput, explanation: Explanation, tokens: tuple[int, int] | None = None) -> None:
    """Rule 9: the SQL and the narrative logged together, at debug level.

    They are one record on purpose. Auditing a wrong answer means asking whether
    the sentence matches the query that produced it, and that question cannot be
    answered from two log lines written at different times in different modules.
    """
    logger.debug(
        "explain_result | source=%s | confidence=%.2f | tokens=%s | sql=%s | narrative=%s",
        explanation.source,
        explanation.confidence_score,
        f"{tokens[0]}/{tokens[1]}" if tokens else "-",
        payload.sql_query,
        explanation.narrative,
    )


__all__ = ["NarrativeFailed", "explain_result"]
