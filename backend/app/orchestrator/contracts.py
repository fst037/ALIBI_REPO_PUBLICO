"""Input and output contracts for the narrative-synthesis node.

The specification these implement (`prompt-orquestador-nl-explanation.md`) is
missing its sections 2 and 3 — it jumps from "where the node sits in the graph"
straight to the behavioural rules, while section 6 requires the output to be
"validated against the JSON Schema of section 3". So both contracts are
reconstructed here from the field names the surviving sections reference by
name: `result_rows`, `row_count`, `truncated`, `narrative`, `confidence_score`.

Pydantic *is* the JSON Schema requirement: `Explanation.model_validate` on the
model's parsed JSON is the validation step section 6 asks for, and
`Explanation.model_json_schema()` emits the schema itself for the thesis
appendix — one definition rather than a class and a hand-kept `.json` beside it
that drift apart.

**`insight_type` used to live here** and no longer does. It was derived
deterministically from the result's shape and the query AST, then handed to both
the narrative and the chart chooser so the two could not contradict each other.
That job now belongs to Module C, which sees the data itself and decides what it
is looking at — classifying twice, once in the orchestrator from row shapes and
once in a model trained for it, would put two opinions about the same table into
one response. The narrative node no longer needs the signal: it reads the rows.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExplainInput(BaseModel):
    """Section 2 — what the node receives.

    `sql_query` is carried for **traceability only** (rule 9: log the query and
    the narrative together). It is never placed in the prompt: the surest way to
    stop a model mentioning JOINs is to never show it one.
    """

    question: str
    sql_query: str
    columns: list[str] = Field(default_factory=list)
    #: The executed result, already row-limited by `query.run_sql`. Capped again
    #: before prompting — see `narrative_max_prompt_rows`.
    rows: list[dict[str, Any]] = Field(default_factory=list)
    #: Rows actually returned. Distinct from `len(rows)` once truncation bites.
    row_count: int = 0
    truncated: bool = False
    #: Business-facing labels for the result columns, when Module B's semantic
    #: enrichment produced one. Keyed by the raw result-column name. Without
    #: these the model sees whatever alias the SQL happened to use, which is the
    #: main way technical naming leaks into a sentence that is otherwise clean.
    column_labels: dict[str, str] = Field(default_factory=dict)
    #: Where the numbers came from, in business terms ("Q1 sales export") — used
    #: only to ground the subject of the sentence, never quoted as a file name.
    source_label: str | None = None
    #: BCP-47 of the *numbers*, not of the prose. Fixed at en-US: the narrative
    #: is written in Spanish, but its figures use
    #: the format of the table and chart beside it (`36.4%`, `1,234.56`).
    locale: str = "en-US"


class KeyFigure(BaseModel):
    """One number the narrative cites, with the value exactly as it was read.

    The model must return every figure it used. That turns the grounding rule
    (rule 1) from an instruction into something checkable: each `value` is
    verified against the result rows before the narrative is accepted, so a
    hallucinated statistic is caught rather than trusted.
    """

    label: str
    value: str


class Explanation(BaseModel):
    """Section 3 — what the node returns, and what the model's JSON must satisfy."""

    narrative: str = Field(min_length=1)
    key_figures: list[KeyFigure] = Field(default_factory=list)
    #: The model's own reading of how well the narrative is supported. Below
    #: `narrative_confidence_threshold` it is logged as weak — not discarded,
    #: because grounding is verified on every answer regardless (rule 9).
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    #: How this text was produced. Recorded because a run that silently used its
    #: second attempt every time looks identical to a clean one from the outside.
    source: Literal["model", "model_retry"] = "model"


class VisualizeInput(BaseModel):
    """What Module C receives — the same executed result the narrative sees.

    Deliberately close to `ExplainInput`, because the two nodes describe one
    result and run from one `execute` call. It differs in what it leaves out:
    there are no `column_labels` here. The narrative must never put an internal
    identifier in a sentence, so it is shown business names; a chart's `field`
    values have to be the **raw column names** or Vega-Lite cannot find them in
    the rows the browser injects. Prettifying happens in the axis title, which
    the model is told to write, not in the field reference.

    `sql_query` is carried for traceability only and never prompted, on the same
    grounds as the narrative node: the surest way to keep a JOIN out of a chart
    title is to never show the model one.
    """

    question: str
    sql_query: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    #: Business-facing labels, used only to suggest axis titles — never as
    #: `field` values. See above.
    column_labels: dict[str, str] = Field(default_factory=dict)


__all__ = ["ExplainInput", "Explanation", "KeyFigure", "VisualizeInput"]
