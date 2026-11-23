"""`dashboards` domain service — swap point between storage backends.

Two things live here, and the difference between them is the point of the
domain:

* **Saved charts** are snapshots. A chart pinned from a conversation keeps the
  answer exactly as it was, because it is a record of something the user read.
* **Dashboards** are the opposite. A board stores the *idea* of each chart —
  `sql` + a data-less Vega-Lite spec — and `chart_data` re-runs that SQL against
  the folder's Parquet **as it is now**, every time the board is opened. Upload
  a newer export into the folder and the board moves.

That re-execution is why a board is scoped to a folder rather than to a fixed
set of datasets: the folder is resolved on every read, exactly as it is for a
conversation, so a file added later is visible to every chart on the board
without anything being re-pinned.

Three properties hold on that path, and each is here rather than trusted:

1. **The SQL is re-validated before it runs.** It passed the guardrail when it
   was saved; the tables it names may have changed since, and the data plane's
   check is the one that knows what is registered on *this* connection.
2. **A chart that can no longer run says so.** A renamed column breaks one card
   and nothing else — `chart_data` returns `status: "failed"` with a message
   instead of raising, because the rest of the board is still true.
3. **No narrative is regenerated.** The stored sentence described the numbers
   as they were, so a board that recomputes must not show it; and rewriting it
   would mean a model call per card per open. A dashboard card is a title and a
   picture.

**Adding a fresh answer to a board also pins it to the folder's saved-chart
pool.** Putting a chart on a dashboard is a stronger signal that it is worth
keeping than the chat's own "Save" toggle — the user chose to build something
around it — so `add_chart` calls `save_chart` on the same message when the
source is a question just asked (`messageId`), not merely a board. A chart
added *from* the pool (`savedChartId`) is already there, so this only fires on
the ask-a-question path. See `_pin_for_folder`.
"""

import logging

from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.repositories import dashboards as repo
from app.repositories import datasets as datasets_repo
from app.repositories import folders as folders_repo
from app.schemas.charts import ChartType, SavedChartResult
from app.schemas.dashboards import (
    AddDashboardChartRequest,
    ChartLayout,
    Dashboard,
    DashboardChart,
    DashboardChartData,
    DashboardDetail,
    DashboardChart as DashboardChartSchema,
    SavedChart,
)
from app.services import conversations as chat_store
from app.services import conversations_engine as chat_engine
from app.services import dashboards_memory as mem
from app.services import datasets as datasets_store
from app.services import folders as folders_store
from app.services import query
from app.services.query import QueryError, QueryTable

logger = logging.getLogger("alibi.dashboards")


# ---------------------------------------------------------------------------
# Saved charts (the pool a board is filled from)
# ---------------------------------------------------------------------------


def list_by_dataset(owner_id: str, dataset_id: str) -> list[SavedChart]:
    if pool.is_configured():
        with session_scope() as session:
            if datasets_repo.get_dataset(session, owner_id, dataset_id) is None:
                raise ApiException(404, "not_found", "No se encontró el dataset.")
            return repo.list_by_dataset(session, owner_id, dataset_id)
    return mem.list_by_dataset(owner_id, dataset_id)


def get_saved_chart(owner_id: str, chart_id: str) -> SavedChart | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_saved_chart(session, owner_id, chart_id)
    return mem.get_saved_chart(owner_id, chart_id)


def save_chart(
    owner_id: str,
    dataset_id: str,
    message_id: str,
    chart_type: ChartType,
    finding_index: int | None = None,
) -> SavedChart:
    if pool.is_configured():
        with session_scope() as session:
            dataset = datasets_repo.get_dataset(session, owner_id, dataset_id)
            if dataset is None:
                raise ApiException(404, "not_found", "No se encontró el dataset.")
            message = chat_store.get_assistant_message(owner_id, message_id)
            if message is None:
                raise ApiException(404, "not_found", "No se encontró el mensaje de origen.")
            try:
                pinned = SavedChartResult.from_answer(message.result, finding_index)
            except ValueError as err:
                raise ApiException(404, "not_found", "Esa respuesta no tiene ese hallazgo.") from err
            return repo.insert_saved_chart(
                session,
                owner_id,
                dataset_id,
                dataset.name,
                message.question,
                chart_type,
                pinned,
            )
    return mem.save_chart(owner_id, dataset_id, message_id, chart_type, finding_index)


def remove_saved_chart(owner_id: str, chart_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.remove_saved_chart(session, owner_id, chart_id)
    return mem.remove_saved_chart(owner_id, chart_id)


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


def _folder_or_404(owner_id: str, folder_id: str):  # noqa: ANN202 - FolderDetail
    folder = folders_store.get_folder(owner_id, folder_id)
    if folder is None:
        raise ApiException(404, "not_found", "No se encontró la carpeta.")
    return folder


def list_dashboards(owner_id: str) -> list[Dashboard]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_dashboards(session, owner_id)
    return mem.list_dashboards(owner_id)


def get_dashboard(owner_id: str, dashboard_id: str) -> DashboardDetail | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_dashboard(session, owner_id, dashboard_id)
    return mem.get_dashboard(owner_id, dashboard_id)


def create_dashboard(owner_id: str, name: str, folder_id: str) -> Dashboard:
    folder = _folder_or_404(owner_id, folder_id)
    if pool.is_configured():
        with session_scope() as session:
            return repo.create_dashboard(session, owner_id, name.strip(), folder_id, folder.name)
    return mem.create_dashboard(owner_id, name.strip(), folder_id, folder.name)


def rename_dashboard(owner_id: str, dashboard_id: str, name: str) -> Dashboard | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.rename_dashboard(session, owner_id, dashboard_id, name.strip())
    return mem.rename_dashboard(owner_id, dashboard_id, name.strip())


def delete_dashboard(owner_id: str, dashboard_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.delete_dashboard(session, owner_id, dashboard_id)
    return mem.delete_dashboard(owner_id, dashboard_id)


def _folder_id_of(owner_id: str, dashboard_id: str) -> str:
    if pool.is_configured():
        with session_scope() as session:
            folder_id = repo.folder_id_of(session, owner_id, dashboard_id)
    else:
        folder_id = mem.folder_id_of(owner_id, dashboard_id)
    if folder_id is None:
        raise ApiException(404, "not_found", "No se encontró el dashboard.")
    return folder_id


def available_charts(owner_id: str, dashboard_id: str) -> list[SavedChart]:
    """Charts pinned from conversations over this board's folder.

    Only those with a Vega-Lite spec are offered. A chart saved before Module C
    has no `sql`+`spec` pair to rebuild from — it is a picture of numbers, not
    an idea — so putting one on a board that recomputes would produce a card
    that can never refresh.
    """
    folder = _folder_or_404(owner_id, _folder_id_of(owner_id, dashboard_id))
    dataset_ids = [d.id for d in folder.datasets]
    if pool.is_configured():
        with session_scope() as session:
            charts = repo.list_saved_for_datasets(session, owner_id, dataset_ids)
    else:
        charts = mem.list_saved_for_datasets(owner_id, dataset_ids)
    return [c for c in charts if c.result.chart is not None and c.result.sql]


def _idea_from_saved(chart: SavedChart) -> tuple[str, str, str, ChartType, dict]:
    if chart.result.chart is None or not chart.result.sql:
        raise ApiException(
            422,
            "chart_not_reproducible",
            "Ese gráfico se guardó antes de que ALIBI pudiera reconstruir gráficos a partir de su consulta, "
            "así que no se puede agregar a un dashboard. Volvé a hacer la pregunta para fijar uno nuevo.",
        )
    spec = chart.result.chart
    return chart.question, chart.question, chart.result.sql, spec.chartType, spec.spec


def _idea_from_message(owner_id: str, message_id: str) -> tuple[str, str, str, ChartType, dict]:
    message = chat_store.get_assistant_message(owner_id, message_id)
    if message is None:
        raise ApiException(404, "not_found", "No se encontró el mensaje de origen.")
    if message.result.chart is None:
        raise ApiException(
            422,
            "no_chart_to_add",
            "Esa respuesta no tiene un gráfico para agregar. Probá reformular la pregunta.",
        )
    spec = message.result.chart
    return message.question, message.question, message.result.sql, spec.chartType, spec.spec


def _pin_for_folder(owner_id: str, dashboard_id: str, message_id: str, chart_type: ChartType) -> None:
    """Also save a freshly-asked chart to the folder's saved-chart pool.

    Best-effort and silent: the dashboard chart is the action the caller
    asked for, and it must not fail because this convenience pin couldn't
    find a dataset to attribute the save to — an edge case where the folder's
    last dataset was detached between asking the question and adding its
    chart to the board. `save_chart` needs one specific `dataset_id` to
    denormalise a name onto the row; any dataset in the folder does, since
    `available_charts` filters by folder membership, not by which one was
    picked. The first is what the chat's own "Save" button already uses
    (`activeSources[0]`), so this stays consistent with that convention
    rather than inventing a second one.
    """
    try:
        folder = _folder_or_404(owner_id, _folder_id_of(owner_id, dashboard_id))
        if not folder.datasets:
            return
        save_chart(owner_id, folder.datasets[0].id, message_id, chart_type)
    except Exception:  # noqa: BLE001 - a convenience pin, never worth failing the add
        logger.warning("could not pin chart from message %s to the folder pool", message_id, exc_info=True)


def add_chart(
    owner_id: str, dashboard_id: str, request: AddDashboardChartRequest
) -> DashboardChart:
    """Copy a chart's idea onto the board.

    Copied, not referenced: a board must not break because the pin it came from
    was tidied away, and the two diverge the moment either is renamed.
    """
    if bool(request.savedChartId) == bool(request.messageId):
        raise ApiException(
            422,
            "validation_error",
            "Indicá exactamente uno de savedChartId o messageId.",
        )

    if request.savedChartId:
        saved = get_saved_chart(owner_id, request.savedChartId)
        if saved is None:
            raise ApiException(404, "not_found", "No se encontró el gráfico guardado.")
        title, question, sql, chart_type, spec = _idea_from_saved(saved)
    else:
        message_id = request.messageId or ""
        title, question, sql, chart_type, spec = _idea_from_message(owner_id, message_id)
        # A chart reaching a board is a stronger signal than the chat's own
        # "Save" toggle — the user built something around it — so it earns a
        # pin of its own. Only on this path: one added from the pool
        # (`savedChartId`, the branch above) is already in it.
        _pin_for_folder(owner_id, dashboard_id, message_id, chart_type)

    # The spec's own title is the one Module C wrote for this result, which is
    # better than the raw question; the request may override both.
    if isinstance(spec.get("title"), str) and spec["title"].strip():
        title = spec["title"].strip()
    if request.title and request.title.strip():
        title = request.title.strip()

    if pool.is_configured():
        with session_scope() as session:
            chart = repo.add_chart(
                session,
                owner_id,
                dashboard_id,
                title=title,
                question=question,
                sql=sql,
                chart_type=chart_type,
                spec=spec,
            )
    else:
        chart = mem.add_chart(
            owner_id,
            dashboard_id,
            title=title,
            question=question,
            sql=sql,
            chart_type=chart_type,
            spec=spec,
        )
    if chart is None:
        raise ApiException(404, "not_found", "No se encontró el dashboard.")
    return chart


def remove_chart(owner_id: str, dashboard_id: str, chart_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.remove_chart(session, owner_id, dashboard_id, chart_id)
    return mem.remove_chart(owner_id, dashboard_id, chart_id)


def set_layout(
    owner_id: str, dashboard_id: str, layout: dict[str, ChartLayout]
) -> DashboardDetail | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.set_layout(session, owner_id, dashboard_id, layout)
    return mem.set_layout(owner_id, dashboard_id, layout)


def _get_chart(owner_id: str, dashboard_id: str, chart_id: str) -> DashboardChartSchema | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_chart(session, owner_id, dashboard_id, chart_id)
    return mem.get_chart(owner_id, dashboard_id, chart_id)


def _query_tables(owner_id: str, folder_id: str) -> list[QueryTable]:
    """DuckDB views for every confirmed dataset in the board's folder.

    All or nothing, exactly as the chat's equivalent is: running a chart's JOIN
    over the subset that happens to be materialised would redraw it from fewer
    files than it was built on, silently, which is the wrong-answer failure this
    whole design exists to avoid.
    """
    folder = _folder_or_404(owner_id, folder_id)
    tables: list[QueryTable] = []
    for dataset in folder.datasets:
        if not datasets_store.is_queryable(dataset):
            continue
        try:
            tables.append(
                datasets_store.query_table(owner_id, dataset.id, require_confirmed=True)
            )
        except QueryError as exc:
            raise ApiException(409, exc.code, exc.message) from exc
    if not tables:
        raise ApiException(
            409, "no_queryable_data", "Esta carpeta todavía no tiene ningún archivo listo para consultar."
        )
    return tables


def chart_data(owner_id: str, dashboard_id: str, chart_id: str) -> DashboardChartData:
    """Run one chart's stored SQL against the folder's data as it is now.

    This is the feature. The board holds the question's *shape*; the numbers
    come from here, on every open.

    Failures below are returned, not raised, and that asymmetry is deliberate:
    a board is many independent statements about one folder, so one that has
    stopped being answerable — a renamed column, a file removed — should mark
    itself and leave the others alone. A 500 for the whole board would hide the
    charts that are still correct.
    """
    chart = _get_chart(owner_id, dashboard_id, chart_id)
    if chart is None:
        raise ApiException(404, "not_found", "No se encontró el gráfico en este dashboard.")

    folder_id = _folder_id_of(owner_id, dashboard_id)
    try:
        tables = _query_tables(owner_id, folder_id)
        # `validate=True` (the default) is not redundant with the guardrail this
        # SQL passed when it was pinned. That check ran against a different
        # connection, minutes or weeks ago; this one knows which views exist
        # here and now, and it is the last thing to run before DuckDB sees the
        # query.
        rows = query.run_sql(chart.sql, tables)
    except ApiException as exc:
        logger.info("dashboard chart %s could not run: %s", chart_id, exc.message)
        return DashboardChartData(chartId=chart_id, status="failed", message=exc.message)
    except QueryError as exc:
        logger.info("dashboard chart %s could not run: %s", chart_id, exc.message)
        return DashboardChartData(chartId=chart_id, status="failed", message=exc.message)

    return DashboardChartData(
        chartId=chart_id,
        status="ok",
        table=chat_engine.result_table(rows.rows, rows.columns),
        rowCount=len(rows.rows),
    )
