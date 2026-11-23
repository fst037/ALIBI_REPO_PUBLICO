"""Shared chart + query-result models.

Used by the `chat` (QueryResult embedded in assistant messages) and `dashboards`
(saved charts store a copy of the result) domains. Conform to the replicated
Chart/Result schemas in `contracts/chat.yaml` and `contracts/dashboards.yaml`.
"""

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    bar = "bar"
    line = "line"
    scatter = "scatter"
    pie = "pie"
    histogram = "histogram"
    heatmap = "heatmap"
    table = "table"


class CategoricalDatum(BaseModel):
    label: str
    value: float


class ScatterPoint(BaseModel):
    x: float
    y: float
    label: str | None = None


class CategoricalChartData(BaseModel):
    kind: Literal["categorical"] = "categorical"
    data: list[CategoricalDatum]


class ScatterChartData(BaseModel):
    kind: Literal["scatter"] = "scatter"
    points: list[ScatterPoint]


class HeatmapChartData(BaseModel):
    kind: Literal["heatmap"] = "heatmap"
    rows: list[str]
    cols: list[str]
    values: list[list[float]]
    normalize: Literal["global", "column"] | None = None


ChartData = Annotated[
    Union[CategoricalChartData, ScatterChartData, HeatmapChartData],
    Field(discriminator="kind"),
]


class ChartColumn(BaseModel):
    """One result column as Module C was shown it.

    `ordinal` is absent on purpose even though Vega-Lite has it — see
    `app/orchestrator/profile.py` for why it cannot be inferred from a result
    table. The model may still choose `"type": "ordinal"` inside the spec.
    """

    name: str
    dtype: Literal["quantitative", "temporal", "nominal"]
    #: Distinct non-null values across the whole result, not just the sample the
    #: model was prompted with. It is what separates a bar chart from a scatter
    #: plot, so it is worth counting properly.
    cardinality: int


class ChartSpec(BaseModel):
    """Module C's answer: a Vega-Lite v5 specification for one result.

    **Carries no data.** `spec` has no `data` key and is not meant to gain one
    on the wire: the browser injects `result.table.rows`, which it already has.
    Shipping the rows twice would double the response for a large result, and —
    more to the point — a `data` block that arrived from the model would hold
    numbers nobody queried. `orchestrator/vega.normalize` strips it for exactly
    that reason.

    There is no `fallback` source. If the model cannot produce a specification
    that validates, the answer has no chart at all: `QueryResult.chart` is null
    and the user gets the sentence and the table. A deterministic
    template chosen from row shapes was considered and rejected.
    """

    #: Which of the six types this is. Never `table`: `table` means "no chart",
    #: and that is expressed by `QueryResult.chart` being null.
    chartType: ChartType
    #: The Vega-Lite v5 document, validated against the vendored official schema.
    spec: dict[str, Any]
    #: The profile the model was shown. Nothing in the UI reads it any more —
    #: it was added so the chart-type chips could re-encode locally, and the
    #: chips are gone. It stays as the record of what Module C was given, which
    #: is the part that explains why it chose this chart; a saved chart keeps it
    #: as a historical snapshot the rows themselves may no longer reproduce.
    columns: list[ChartColumn]
    #: Whether the first attempt was accepted or the critique-carrying retry
    #: produced it. Recorded for the same reason `Explanation.source` is: a run
    #: that silently needed its second attempt every time looks identical to a
    #: clean one from the outside.
    source: Literal["model", "model_retry"] = "model"


class Insight(BaseModel):
    label: str
    value: str
    delta: str


class ResultColumn(BaseModel):
    key: str
    label: str
    align: Literal["left", "num"]


class ResultTable(BaseModel):
    columns: list[ResultColumn]
    # Cell values are strings (pre-formatted) or numbers.
    rows: list[dict[str, str | int | float]]


class TrailStep(BaseModel):
    """One step the agent harness took, for "How I got here".

    The same steps the progress view showed while the run was live — queries,
    looks at a result, charts — with the planner's reason for each, so the trail
    after the answer tells the story the user watched, not a different one.

    Kept on the answer, not only on the run, because the run is a transient
    record and the conversation is what History replays.
    """

    label: str
    #: `context` — asked so the model understood the data; `insight` — a finding.
    purpose: Literal["context", "insight"] | None = None
    #: The planner's one-sentence reason for the step.
    thought: str | None = None
    #: What the step called (`sql` = Module A, `chart` = Module C, `peek` /
    #: `describe` = a look at a result already held) and who served it.
    tool: Literal["sql", "peek", "describe", "chart"] | None = None
    model: str | None = None
    #: The outcome of a step that is not a query (the chart drawn, or why none).
    detail: str | None = None
    question: str | None = None
    #: The hints sent with the question to Module A (see `RunStep.hints`).
    hints: str | None = None
    sql: str | None = None
    rowCount: int | None = None
    status: Literal["done", "failed"] = "done"
    error: str | None = None


class Finding(BaseModel):
    """One result the user should see, with its own sentence."""

    text: str
    #: What this finding is about, as the planner asked it.
    question: str
    sql: str
    table: ResultTable
    rowCount: int
    rowsLabel: str
    chart: ChartSpec | None = None


class QueryResult(BaseModel):
    """One answer in the chat: the sentence, the SQL, the table, and a chart.

    `chart` is Module C's Vega-Lite specification, produced by
    `orchestrator/nodes/visualize.py` from the executed rows. It is **optional
    and frequently absent**, and that is a designed state rather than a
    degradation:

    * the model judged that no chart adds anything (a single total, one row);
    * or it could not produce a specification that validates.

    Both come back as `chart: null` and the answer is still complete — the
    sentence and the table are what the user reads. There is deliberately no
    template fallback filling the gap: a chart type chosen from row shapes is
    the deterministic classifier that was deleted on 2026-08-02 precisely
    because it would eventually contradict the model about the same table.

    The old `autoChartType`/`chartData`/`chartTitle`/`insights` fields are not
    coming back. They belonged to that classifier and to a bespoke rendering
    union; Module C emits Vega-Lite, which the browser renders directly. They
    survive on `SavedChartResult` so charts saved before the change still
    deserialize.
    """

    naturalResponse: str
    sql: str
    table: ResultTable
    rowCount: int
    rowsLabel: str
    chart: ChartSpec | None = None
    #: Every finding, in order, **when there are two or more** (the agent
    #: harness). Empty for a one-finding answer, which keeps exactly the
    #: shape above. The top-level `sql`/`table`/`chart` always mirror the first
    #: finding, so what reads one result (export, pinning, saved charts) keeps
    #: reading the lead one.
    findings: list[Finding] = Field(default_factory=list)
    #: The queries behind the answer, context and insight, failures included.
    trail: list[TrailStep] = Field(default_factory=list)


class SavedChartResult(QueryResult):
    """A `QueryResult` as the dashboards domain stored it, chart included.

    Kept as a separate model rather than by making the chart fields optional on
    `QueryResult`: the chat can no longer produce these, and a nullable field on
    the response contract would invite callers to keep reading one that is always
    null. Saved charts are historical records — `dashboard_charts.result` holds a
    JSONB snapshot written when charts existed — so the shape has to survive
    whether or not anything writes it today.

    A chart saved *today* carries its picture in the inherited `chart` field —
    the Vega-Lite spec Module C produced — and still writes the legacy fields
    below at their empty defaults. Those defaults are not a placeholder waiting
    to be filled: `autoChartType`/`chartData` describe the pre-Module-C
    rendering union, which nothing produces any more. They stay so that a chart
    saved before 2026-08-07 still loads and still renders through the old SVG
    components on the dashboards screens.
    """

    insights: list[Insight] = Field(default_factory=list)
    chartTitle: str = ""
    autoChartType: ChartType = ChartType.table
    chartData: ChartData = CategoricalChartData(data=[])

    @classmethod
    def from_answer(cls, result: QueryResult, finding_index: int | None = None) -> "SavedChartResult":
        """Widen a chat answer into the stored shape.

        A pin is one chart, so it keeps one finding — the lead one (the top-level
        fields) unless `finding_index` names another — and drops the rest and the
        trail: a saved chart that carried an investigation's every step would be
        a transcript, not a chart. An index the answer does not have is refused
        rather than quietly pinning the lead chart instead.
        """
        if finding_index is None or (finding_index == 0 and not result.findings):
            return cls(**result.model_dump(exclude={"findings", "trail"}))
        if finding_index >= len(result.findings):
            raise ValueError(f"the answer has no finding {finding_index}")
        finding = result.findings[finding_index]
        return cls(
            naturalResponse=finding.text,
            sql=finding.sql,
            table=finding.table,
            rowCount=finding.rowCount,
            rowsLabel=finding.rowsLabel,
            chart=finding.chart,
        )
