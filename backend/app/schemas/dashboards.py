"""Pydantic models for the `dashboards` domain.

Conform to `contracts/dashboards.yaml`. Two things live here:

* **Dashboards** — named boards over one folder, holding chart *ideas* that are
  re-executed on every open. This is the feature.
* **SavedChart** — a chart pinned from a conversation. It no longer has a
  screen of its own; it is the pool a board is filled from.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.charts import ChartType, ResultTable, SavedChartResult


class SavedChart(BaseModel):
    """A chart pinned from a conversation, with the answer as it was then.

    Still a snapshot, and still right to be one: this is a record of an answer
    the user liked. A dashboard is the opposite — it asks the question again —
    which is why adding this to a board copies its `sql` and `spec` onto a
    `DashboardChart` rather than pointing at it.
    """

    id: str
    datasetId: str
    datasetName: str
    question: str
    chartType: ChartType
    savedAt: str
    #: `SavedChartResult`, not `QueryResult`: a saved chart is a historical
    #: snapshot and still carries the chart fields the chat no longer produces.
    #: Reading it back as the narrowed chat model would silently drop them from
    #: every chart saved before Module C's removal.
    result: SavedChartResult


class SaveChartRequest(BaseModel):
    messageId: str
    chartType: ChartType
    #: Which finding of a multi-finding answer to pin (`QueryResult.findings`).
    #: Omitted, the answer's lead finding — its top-level chart.
    findingIndex: int | None = Field(default=None, ge=0)


#: Columns in react-grid-layout's desktop (`lg`) breakpoint. Only `lg`
#: positions are ever persisted — narrower breakpoints are derived client-side
#: from it (see `GridSurface.tsx`) — so this is the resolution `x`/`w` are
#: actually stored in, and the only one that has to match the frontend's own
#: `COLS.lg`, which cross-references this constant by name in a comment since
#: the two languages can't share it directly.
#:
#: 24, not 12: at 12 columns the smallest reasonable-looking chart was already
#: half the row, so three of them could never fit side by side without a
#: resize so aggressive it stopped looking like a deliberate choice.
GRID_COLUMNS = 24

#: A third of the row — three charts fit a line without the user having to
#: resize anything first.
DEFAULT_CHART_W = GRID_COLUMNS // 3
DEFAULT_CHART_H = 4


class ChartLayout(BaseModel):
    """Where a chart sits, in react-grid-layout's `GRID_COLUMNS`-column space.

    Stored server-side rather than in the browser because a dashboard is a
    thing the user arranged, not a per-device preference — it has to look the
    same on the laptop they built it on and the screen they present it from.
    """

    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=DEFAULT_CHART_W, ge=1, le=GRID_COLUMNS)
    h: int = Field(default=DEFAULT_CHART_H, ge=2)


class DashboardChart(BaseModel):
    """One chart on a board: how to rebuild it, never what it showed.

    `sql` + `spec`, no rows and no narrative. `GET .../charts/{id}/data` runs
    the SQL against the folder's Parquet as it is now and the browser draws
    `spec` over the result, so the board reflects the data rather than the day
    it was assembled.
    """

    id: str
    title: str
    #: The question this came from. Provenance for the detail view; never
    #: re-asked, and never shown as if it were a fresh answer.
    question: str = ""
    #: Re-validated by the read-only guardrail before every run — it was safe
    #: when saved, and the tables it names may have changed since.
    sql: str
    chartType: ChartType
    #: Vega-Lite v5, without `data`.
    spec: dict[str, Any]
    layout: ChartLayout = Field(default_factory=ChartLayout)


class Dashboard(BaseModel):
    """A board, without its charts — what the list screen needs."""

    id: str
    name: str
    folderId: str
    folderName: str = ""
    chartCount: int = 0
    createdAt: str
    updatedAt: str


class DashboardDetail(Dashboard):
    charts: list[DashboardChart] = Field(default_factory=list)


class CreateDashboardRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    folderId: str


class UpdateDashboardRequest(BaseModel):
    """Partial patch — rename only. An empty body is a 422 rather than a no-op,
    matching `UpdateConversationRequest`."""

    name: str = Field(min_length=1, max_length=120)


class AddDashboardChartRequest(BaseModel):
    """Exactly one of `savedChartId` (from the pool) or `messageId` (from the
    board's own chat). Both resolve to the same thing — a `sql` + `spec` pair
    copied onto the board."""

    savedChartId: str | None = None
    messageId: str | None = None
    #: Overrides the spec's own title when the user renamed it before adding.
    title: str | None = None


class UpdateLayoutRequest(BaseModel):
    """The whole arrangement in one call.

    Bulk rather than per-chart because dragging one card compacts the others:
    react-grid-layout returns the *board's* new state, and writing that as N
    requests would leave the board in a shape nobody chose if one of them
    failed.
    """

    layout: dict[str, ChartLayout]


class DashboardChartData(BaseModel):
    """The result of re-running one chart's SQL, right now.

    `status` is explicit because a stale chart is the expected failure on a
    board that recomputes: the folder's files change, and a query that named a
    column someone has since renamed stops working. The card says so and the
    rest of the board carries on.
    """

    chartId: str
    status: Literal["ok", "failed"]
    table: ResultTable | None = None
    rowCount: int = 0
    #: Set when `status` is `failed`: what to show on the card, in the user's
    #: language rather than DuckDB's.
    message: str | None = None
