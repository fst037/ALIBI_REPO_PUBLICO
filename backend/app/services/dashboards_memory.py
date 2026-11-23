"""In-memory dashboards store: the fallback when no database is configured.

Holds both halves of the domain — the pool of charts pinned from conversations
(seeded from a fixture) and the named boards built out of it. Single-user, so
the `owner_id` the service passes is accepted but ignored.

Boards start empty rather than seeded. A fixture dashboard would hold `sql`
naming tables that exist in no fixture, so every card on it would fail its
re-execution on open — a broken board shipped as a demo.
"""

import secrets
from datetime import date, datetime, timezone

from app.core.errors import ApiException
from app.schemas.charts import ChartType, SavedChartResult
from app.schemas.dashboards import (
    ChartLayout,
    Dashboard,
    DashboardChart,
    DashboardDetail,
    SavedChart,
)
from app.services import conversations as chat_store
from app.services import datasets_memory
from app.services._fixtures import load_json

_saved_charts: list[SavedChart] = [SavedChart(**c) for c in load_json("savedCharts.json")]

#: dashboard id -> (board, charts). One dict so a board and its charts cannot
#: get out of step, which is the whole failure mode a cascade prevents in SQL.
_dashboards: dict[str, tuple[Dashboard, list[DashboardChart]]] = {}


def reset() -> None:
    """Drop every board. Used between tests, like the other memory stores."""
    _dashboards.clear()


def list_by_dataset(_owner_id: str, dataset_id: str) -> list[SavedChart]:
    if datasets_memory.get_dataset("", dataset_id) is None:
        raise ApiException(404, "not_found", "No se encontró el dataset.")
    return [c for c in _saved_charts if c.datasetId == dataset_id]


def get_saved_chart(_owner_id: str, chart_id: str) -> SavedChart | None:
    return next((c for c in _saved_charts if c.id == chart_id), None)


def save_chart(
    _owner_id: str,
    dataset_id: str,
    message_id: str,
    chart_type: ChartType,
    finding_index: int | None = None,
) -> SavedChart:
    dataset = datasets_memory.get_dataset("", dataset_id)
    if dataset is None:
        raise ApiException(404, "not_found", "No se encontró el dataset.")
    message = chat_store.get_assistant_message("", message_id)
    if message is None:
        raise ApiException(404, "not_found", "No se encontró el mensaje de origen.")
    try:
        pinned = SavedChartResult.from_answer(message.result, finding_index)
    except ValueError as err:
        raise ApiException(404, "not_found", "Esa respuesta no tiene ese hallazgo.") from err

    chart = SavedChart(
        id="sc_" + secrets.token_urlsafe(6),
        datasetId=dataset_id,
        datasetName=dataset.name,
        question=message.question,
        chartType=chart_type,
        savedAt=date.today().isoformat(),
        result=pinned,
    )
    _saved_charts.insert(0, chart)
    return chart


def remove_saved_chart(_owner_id: str, chart_id: str) -> bool:
    target = get_saved_chart("", chart_id)
    if target is None:
        return False
    _saved_charts.remove(target)
    return True


def list_saved_for_datasets(_owner_id: str, dataset_ids: list[str]) -> list[SavedChart]:
    wanted = set(dataset_ids)
    return [c for c in _saved_charts if c.datasetId in wanted]


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detail(board: Dashboard, charts: list[DashboardChart]) -> DashboardDetail:
    return DashboardDetail(**{**board.model_dump(), "chartCount": len(charts)}, charts=charts)


def list_dashboards(_owner_id: str) -> list[Dashboard]:
    boards = [
        Dashboard(**{**b.model_dump(), "chartCount": len(charts)})
        for b, charts in _dashboards.values()
    ]
    return sorted(boards, key=lambda b: b.updatedAt, reverse=True)


def get_dashboard(_owner_id: str, dashboard_id: str) -> DashboardDetail | None:
    entry = _dashboards.get(dashboard_id)
    return _detail(*entry) if entry else None


def folder_id_of(_owner_id: str, dashboard_id: str) -> str | None:
    entry = _dashboards.get(dashboard_id)
    return entry[0].folderId if entry else None


def create_dashboard(_owner_id: str, name: str, folder_id: str, folder_name: str) -> Dashboard:
    now = _now()
    board = Dashboard(
        id="db_" + secrets.token_urlsafe(6),
        name=name,
        folderId=folder_id,
        folderName=folder_name,
        chartCount=0,
        createdAt=now,
        updatedAt=now,
    )
    _dashboards[board.id] = (board, [])
    return board


def rename_dashboard(_owner_id: str, dashboard_id: str, name: str) -> Dashboard | None:
    entry = _dashboards.get(dashboard_id)
    if entry is None:
        return None
    board, charts = entry
    board = Dashboard(**{**board.model_dump(), "name": name, "updatedAt": _now()})
    _dashboards[dashboard_id] = (board, charts)
    return Dashboard(**{**board.model_dump(), "chartCount": len(charts)})


def delete_dashboard(_owner_id: str, dashboard_id: str) -> bool:
    return _dashboards.pop(dashboard_id, None) is not None


def _touch(dashboard_id: str) -> None:
    board, charts = _dashboards[dashboard_id]
    _dashboards[dashboard_id] = (
        Dashboard(**{**board.model_dump(), "updatedAt": _now()}),
        charts,
    )


def add_chart(
    _owner_id: str,
    dashboard_id: str,
    *,
    title: str,
    question: str,
    sql: str,
    chart_type: ChartType,
    spec: dict,
) -> DashboardChart | None:
    entry = _dashboards.get(dashboard_id)
    if entry is None:
        return None
    _, charts = entry
    chart = DashboardChart(
        id="dc_" + secrets.token_urlsafe(6),
        title=title,
        question=question,
        sql=sql,
        chartType=chart_type,
        spec=spec,
        # Below everything already on the board — the placement rule, which is
        # deliberately not a judgement call.
        layout=ChartLayout(x=0, y=max((c.layout.y + c.layout.h for c in charts), default=0)),
    )
    charts.append(chart)
    _touch(dashboard_id)
    return chart


def get_chart(_owner_id: str, dashboard_id: str, chart_id: str) -> DashboardChart | None:
    entry = _dashboards.get(dashboard_id)
    if entry is None:
        return None
    return next((c for c in entry[1] if c.id == chart_id), None)


def remove_chart(_owner_id: str, dashboard_id: str, chart_id: str) -> bool:
    entry = _dashboards.get(dashboard_id)
    if entry is None:
        return False
    _, charts = entry
    target = next((c for c in charts if c.id == chart_id), None)
    if target is None:
        return False
    charts.remove(target)
    _touch(dashboard_id)
    return True


def set_layout(
    _owner_id: str, dashboard_id: str, layout: dict[str, ChartLayout]
) -> DashboardDetail | None:
    entry = _dashboards.get(dashboard_id)
    if entry is None:
        return None
    _, charts = entry
    for index, chart in enumerate(charts):
        position = layout.get(chart.id)
        if position is not None:
            charts[index] = DashboardChart(**{**chart.model_dump(), "layout": position})
    _touch(dashboard_id)
    charts.sort(key=lambda c: (c.layout.y, c.layout.x))
    return _detail(*_dashboards[dashboard_id])
