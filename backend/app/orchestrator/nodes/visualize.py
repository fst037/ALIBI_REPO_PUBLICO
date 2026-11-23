"""`visualize` — Module C: choose a chart for an executed result and specify it.

Where this sits: beside `explain_result`, not after it. Both describe the same
executed result and `execute` runs them **concurrently** — the query has already
happened, neither needs the other's output, and API Gateway's 29s ceiling does
not leave room to spend two model round-trips end to end.

    VisualizeInput ─► prompt ─► model ─► JSON ─► fields ─► schema ─► ChartSpec
                                            │         │        │
                                            └── retry ◄┴────────┘ once, with the critique
                                                      │
                                             still bad ▼
                                                    None (no chart)

**Two ways to propose, one way to accept.** `ALIBI_VISUALIZATION_PROVIDER`
picks who writes the spec:

* `bedrock` — the general model the narrative uses, with the few-shot system
  prompt in `prompts/visualize_system.md`, and one retry carrying the critique.
* `sagemaker` — the nvBench → Vega-Lite QLoRA adapter (job
  `model-c-dev-qlora-002`), served on the same endpoint as Module A's. It gets
  the result's *profile*, not a prompt: the endpoint renders its training
  prompt (`ML/src/common/prompts.py::chart_user`, the same block as
  `_prompt_block` below) itself. **One attempt**: the adapter decodes greedily,
  so the same input would give the same spec, and it was never trained on a
  critique turn to make a different one.

Either way the spec goes through the same `_read` and `_accept`, so the
guarantees below do not depend on which model answered.

Two properties hold on every path out of `visualize`:

* **Nothing unvalidated is returned.** Every spec that leaves here passed the
  official Vega-Lite v5 JSON Schema *and* had every `field` it names checked
  against the columns the query actually returned. The browser renders what
  comes out of here directly, so this is the last place a bad spec can be
  caught.
* **Failure is `None`, never an exception the caller has to translate.** A
  missing chart is not a broken answer. The sentence and the table are what the
  user reads; the chart is the optional half. This is the one place the two
  orchestrator nodes deliberately differ — `explain_result` raises and the
  caller turns it into a 503, because an answer with no sentence is not an
  answer at all.

**There is no template fallback**, and its absence is the design. A
deterministic chart chosen from row shapes is exactly the classifier deleted on
2026-08-02 for being a second opinion that would eventually contradict the
model about the same table. Reintroducing it as a safety net brings the
contradiction back with a "simplified view" badge on it. No chart is the honest
state, and the product already has a shape for it: the sentence and
the table.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from app.core import sagemaker
from app.core.config import ModelProvider, get_settings
from app.orchestrator import client, formatting, profile, vega
from app.orchestrator.contracts import VisualizeInput
from app.schemas.charts import ChartSpec

logger = logging.getLogger("alibi.orchestrator.visualize")

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "visualize_system.md"

#: What the model says when it judges that no chart improves on the table. Not a
#: failure — rule 7 of the prompt asks for it, and a one-bar chart manufactured
#: to avoid saying it costs the reader more than it gives.
_NO_CHART = "none"


def _system_prompt() -> str:
    """Read from disk on every call, like `explain_result`'s — the prompt is the
    tuning surface, and editing the Markdown then re-asking is the whole loop."""
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _prompt_block(payload: VisualizeInput, max_rows: int) -> str:
    """What the model sees: the question, the column profile, and a sample.

    Unlike the narrative's data block, columns keep their **raw names**. The
    model's `field` values have to match the keys in the rows the browser
    injects, so renaming them here would produce a spec that validates against
    the schema and draws nothing. The business label is supplied alongside, for
    the axis titles rule 6 asks for — the pretty name belongs in the title, the
    real one in the field.
    """
    columns = profile.column_profile(payload.rows, payload.columns)
    lines = []
    if payload.question.strip():
        lines.append(f"The user asked: {payload.question}")
    else:
        # The hand-edited-SQL path: there is no question, and the query itself is
        # deliberately withheld from the prompt. The profile alone is enough to
        # choose a form.
        lines.append("The user ran a report of their own.")
    lines.append(f"Rows in the result: {payload.row_count}")
    if payload.truncated:
        lines.append("The result was cut off; the sample below is the beginning of it.")
    lines.append("")

    lines.append("COLUMNS:")
    for column in columns:
        label = formatting.humanize(column.name, payload.column_labels)
        lines.append(
            f"- {column.name} ({column.dtype}, {column.cardinality} distinct) "
            f"— business label: {label}"
        )
    lines.append("")

    sample = payload.rows[:max_rows]
    lines.append(f"SAMPLE ROWS ({len(sample)} of {payload.row_count}):")
    lines.append(json.dumps(sample, ensure_ascii=False, default=str))
    return "\n".join(lines)


def _read(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse one completion into a spec, or into the critique for the retry.

    Returns `(None, [])` — no spec, no problems — for a deliberate
    `chart_type: "none"`. That triple state is why this returns a tuple rather
    than raising: "no chart because the model chose so" and "no chart because the
    model failed" lead to the same response and must not lead to the same log
    line.
    """
    try:
        raw = client.extract_json(text)
    except (ValueError, json.JSONDecodeError) as err:
        return None, [f"Your reply was not valid JSON ({err}). Reply with the JSON object only."]

    if str(raw.get("chart_type", "")).strip().lower() == _NO_CHART:
        return None, []

    spec = raw.get("spec")
    if not isinstance(spec, dict) or not spec:
        return None, [
            "Your reply had no `spec` object. Return a Vega-Lite v5 specification, "
            'or `{"chart_type": "none", "spec": null}` if no chart helps here.'
        ]
    return spec, []


#: The adapter was trained on English titles ("Total of revenue by Month", "Number
#: of rows"), which the user would read as an English chart in a Spanish answer.
#: Same words, in the order Spanish uses them; column names are left as they are.
_TITLE_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bNumber of rows\b", re.IGNORECASE), "Cantidad de filas"),
    (re.compile(r"\bTotal of\b", re.IGNORECASE), "Total de"),
    (re.compile(r"\bAverage of\b", re.IGNORECASE), "Promedio de"),
    (re.compile(r"\bMinimum of\b", re.IGNORECASE), "Mínimo de"),
    (re.compile(r"\bMaximum of\b", re.IGNORECASE), "Máximo de"),
    (re.compile(r"\bCount of\b", re.IGNORECASE), "Cantidad de"),
    (re.compile(r"\bCount\b"), "Cantidad"),
    (re.compile(r"\bAverage\b"), "Promedio"),
    (re.compile(r"\bMinimum\b"), "Mínimo"),
    (re.compile(r"\bMaximum\b"), "Máximo"),
    (re.compile(r"\s+by\s+", re.IGNORECASE), " por "),
)


def _spanish(text: str) -> str:
    for pattern, spanish in _TITLE_PHRASES:
        text = pattern.sub(spanish, text)
    return text


def _localize_titles(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec with its chart and axis titles in Spanish; nothing else changes."""

    def title(value: Any) -> Any:  # noqa: ANN401 - Vega-Lite's title is a string or an object
        if isinstance(value, str):
            return _spanish(value)
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            return {**value, "text": _spanish(value["text"])}
        return value

    out = dict(spec)
    if "title" in out:
        out["title"] = title(out["title"])

    def channel(enc: Any) -> Any:  # noqa: ANN401
        if not isinstance(enc, dict):
            return enc
        enc = dict(enc)
        if "title" in enc:
            enc["title"] = title(enc["title"])
        axis = enc.get("axis")
        if isinstance(axis, dict) and "title" in axis:  # the axis title wins over the channel's
            enc["axis"] = {**axis, "title": title(axis["title"])}
        return enc

    encoding = out.get("encoding")
    if isinstance(encoding, dict):
        out["encoding"] = {name: channel(enc) for name, enc in encoding.items()}
    return out


def _accept(spec: dict[str, Any], payload: VisualizeInput, *, source: str) -> tuple[ChartSpec | None, list[str]]:
    """Normalise, validate, and wrap one candidate specification."""
    normalized = _localize_titles(vega.normalize(spec))
    problems = vega.check(normalized, payload.columns)
    if problems:
        return None, problems

    chart_type = vega.chart_type_of(normalized)
    if chart_type is None:  # pragma: no cover - `check` already rejects this
        return None, ["The mark cannot be displayed."]

    return (
        ChartSpec(
            chartType=chart_type,
            spec=normalized,
            columns=profile.column_profile(payload.rows, payload.columns),
            source=source,  # type: ignore[arg-type]
        ),
        [],
    )


#: Why `visualize` returned what it did. The answer shows one thing for every
#: `None` — no picture — but "How I got here" says which, so a missing chart can
#: be told apart from a model that is down.
Outcome = Literal["chart", "declined", "rejected", "unavailable", "off"]


def served_by() -> str:
    """Who writes the chart, for the user's step view."""
    settings = get_settings()
    if settings.visualization_provider is ModelProvider.sagemaker:
        return "modelo ajustado de ALIBI (nvBench, SageMaker)"
    return f"Bedrock · {settings.orchestrator_model_id or 'sin modelo'}"


def visualize(payload: VisualizeInput) -> ChartSpec | None:
    """A validated Vega-Lite chart for this result, or `None` if there is none.

    `None` covers several outcomes on purpose, because the user sees one thing in
    all of them — an answer with no picture: the node is switched off, the model
    judged a chart adds nothing, or no attempt produced a spec that validates.
    `visualize_with_outcome` says which.
    """
    return visualize_with_outcome(payload)[0]


def visualize_with_outcome(payload: VisualizeInput) -> tuple[ChartSpec | None, Outcome]:
    """`visualize`, plus why there is or is not a chart."""
    settings = get_settings()
    if not settings.visualization_enabled:
        return None, "off"
    if not payload.columns or not payload.rows:
        # Nothing to plot, and no reason to spend a model call finding that out.
        return None, "off"
    if settings.visualization_provider is ModelProvider.sagemaker:
        return _visualize_fine_tuned(payload)

    config = client.config_from_settings(settings)
    if not config.is_configured:
        logger.info("no model configured; answering without a chart")
        return None, "unavailable"
    config = replace(config, timeout_seconds=settings.visualization_timeout_seconds)

    messages: list[client.Message] = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": _prompt_block(payload, settings.visualization_max_prompt_rows),
        },
    ]

    for attempt, source in enumerate(("model", "model_retry")):
        try:
            completion = client.complete(
                config, messages, max_tokens=settings.visualization_max_tokens
            )
        except client.LlmUnavailable as err:
            # Not raised onward. The query ran and the narrative may well have
            # succeeded on its own thread; losing the chart is the cost, and the
            # answer is still worth showing.
            logger.warning("visualization model unavailable: %s", err)
            return None, "unavailable"

        spec, problems = _read(completion.text)
        if spec is None and not problems:
            logger.info("model judged no chart applicable for this result")
            return None, "declined"
        if spec is not None:
            chart, problems = _accept(spec, payload, source=source)
            if chart is not None:
                logger.debug(
                    "visualize | source=%s | type=%s | tokens=%d/%d",
                    chart.source,
                    chart.chartType.value,
                    completion.prompt_tokens,
                    completion.completion_tokens,
                )
                return chart, "chart"

        logger.info("chart attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        if attempt == 0:
            # The Reflection pattern, same as the narrative's: the rejected spec
            # goes back in as context so "the field `profit` is not one of the
            # columns" refers to something the model can see it wrote.
            messages = messages + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": _critique(problems)},
            ]

    logger.info("no valid chart after two attempts; answering without one")
    return None, "rejected"


_AGGREGATE = re.compile(r"^(count|sum|avg|min|max)\((.*)\)$", re.IGNORECASE)
_AGG_WORD = {"count": "Count", "sum": "Total", "avg": "Average", "min": "Minimum", "max": "Maximum"}


def _trained_label(name: str) -> str:
    """The business label the adapter was trained with, for a column with none.

    A copy of `ML/src/common/prompts.py::humanize` (the test suite holds the two
    together): `COUNT(*)` -> "Number of rows", `SUM(revenue)` -> "Total of
    revenue". The backend's own `formatting.humanize` reads those as
    "Count(*)" / "Sum(revenue)", which is fine for a table header but is not
    what the adapter learned to title axes from.
    """
    agg = _AGGREGATE.match(name.strip())
    if agg:
        fn, inner = agg.group(1).lower(), agg.group(2).strip()
        if inner == "*":
            return "Number of rows"
        return f"{_AGG_WORD[fn]} of {_trained_label(inner).lower()}"
    return re.sub(r"[_\s]+", " ", name.strip()).strip().title()


def _adapter_payload(payload: VisualizeInput) -> dict[str, Any]:
    """The adapter's input: the pieces `chart_user` was trained on, unrendered.

    Raw column names (the spec's `field`s must match the rows the browser
    injects) and a business label beside each: Module B's, when the column has
    one, otherwise the one training would have computed (`_trained_label`). The
    endpoint keeps the first 5 rows — the training sample size — so only those
    are sent.
    """
    return {
        "task": "chart",
        "question": payload.question,
        "row_count": payload.row_count,
        "truncated": payload.truncated,
        "columns": [
            {
                "name": column.name,
                "dtype": column.dtype,
                "cardinality": column.cardinality,
                "label": (payload.column_labels.get(column.name) or "").strip()
                or _trained_label(column.name),
            }
            for column in profile.column_profile(payload.rows, payload.columns)
        ],
        "rows": payload.rows[:5],
    }


#: One chart request at a time from this process. The endpoint generates one
#: request at a time anyway (one GPU, one adapter switch behind a lock), and
#: SageMaker's 60 s limit is measured from when a request is *sent*: three
#: findings' charts sent together were measured at 9, 18 and 27 s, so Deep's six
#: would put the last near the limit. Queued here instead, each call gets its own
#: ~9 s, and the total is the same.
_ADAPTER_CALLS = threading.Lock()


def _visualize_fine_tuned(payload: VisualizeInput) -> tuple[ChartSpec | None, Outcome]:
    target = sagemaker.target_from_settings()
    if target is None:
        logger.info("no SageMaker endpoint configured; answering without a chart")
        return None, "unavailable"
    try:
        with _ADAPTER_CALLS:
            reply = sagemaker.invoke(target, _adapter_payload(payload))
    except sagemaker.SageMakerUnavailable as err:
        # Same contract as the prompted path: a lost chart is not a lost answer.
        logger.warning("chart adapter unavailable: %s", err)
        return None, "unavailable"

    # `raw` is the adapter's text; parsing it here rather than trusting the
    # handler's own `spec` keeps one reader for both providers.
    spec, problems = _read(str(reply.get("raw") or ""))
    if spec is None and not problems:
        logger.info("chart adapter judged no chart applicable for this result")
        return None, "declined"
    if spec is not None:
        chart, problems = _accept(spec, payload, source="model")
        if chart is not None:
            return chart, "chart"
    logger.info("chart adapter's spec rejected: %s", "; ".join(problems))
    return None, "rejected"


def _critique(problems: list[str]) -> str:
    joined = "\n".join(f"- {p}" for p in problems)
    return (
        "Your previous specification was rejected for these reasons:\n"
        f"{joined}\n"
        "Return a corrected Vega-Lite v5 specification fixing every point, or "
        '`{"chart_type": "none", "spec": null}` if no chart fits this result.'
    )


__all__ = ["Outcome", "served_by", "visualize", "visualize_with_outcome"]
