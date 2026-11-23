"""Postgres repository for the `dashboards` domain.

Owner-scoped throughout. Two stores that look alike and are not:

* **Saved charts** keep a full JSONB snapshot of the `QueryResult`, so a pinned
  answer survives the message it came from being deleted. `dataset_name` is
  denormalised for the same reason.
* **Dashboards** keep no results at all — a chart on a board is `sql` + `spec`,
  re-executed on every read. Snapshotting there would defeat the feature.

Ownership of a `dashboard_charts` row is always checked through its board;
there is no `owner_id` on the chart, because a chart that outlived its
dashboard would be unreachable anyway (the FK cascades).
"""

from datetime import datetime

from sqlmodel import Session, select

from app.core.security import new_id
from app.db.models import Dashboard as DashboardRow
from app.db.models import DashboardChart as DashboardChartRow
from app.db.models import Folder as FolderModel
from app.db.models import SavedChart as SavedChartRow
from app.schemas.charts import ChartType, SavedChartResult
from app.schemas.dashboards import (
    DEFAULT_CHART_H,
    DEFAULT_CHART_W,
    ChartLayout,
    Dashboard,
    DashboardChart,
    DashboardDetail,
    SavedChart,
)


def _saved_chart(row: SavedChartRow) -> SavedChart:
    return SavedChart(
        id=row.id,
        datasetId=row.dataset_id,
        datasetName=row.dataset_name,
        question=row.question,
        chartType=row.chart_type,
        savedAt=row.saved_at.date().isoformat(),
        result=SavedChartResult(**row.result),
    )


def list_by_dataset(session: Session, owner_id: str, dataset_id: str) -> list[SavedChart]:
    rows = session.exec(
        select(SavedChartRow)
        .where(SavedChartRow.owner_id == owner_id, SavedChartRow.dataset_id == dataset_id)
        .order_by(SavedChartRow.saved_at.desc())
    ).all()
    return [_saved_chart(row) for row in rows]


def get_saved_chart(session: Session, owner_id: str, chart_id: str) -> SavedChart | None:
    row = session.exec(
        select(SavedChartRow).where(SavedChartRow.id == chart_id, SavedChartRow.owner_id == owner_id)
    ).first()
    return _saved_chart(row) if row else None


def insert_saved_chart(
    session: Session,
    owner_id: str,
    dataset_id: str,
    dataset_name: str,
    question: str,
    chart_type: ChartType,
    result: SavedChartResult,
) -> SavedChart:
    row = SavedChartRow(
        id=new_id("sc"),
        owner_id=owner_id,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        question=question,
        chart_type=chart_type,
        result=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return _saved_chart(row)


def remove_saved_chart(session: Session, owner_id: str, chart_id: str) -> bool:
    row = session.exec(
        select(SavedChartRow).where(SavedChartRow.id == chart_id, SavedChartRow.owner_id == owner_id)
    ).first()
    if row is None:
        return False
    session.delete(row)
    return True


def list_saved_for_datasets(
    session: Session, owner_id: str, dataset_ids: list[str]
) -> list[SavedChart]:
    """The pool a board over these datasets can be filled from.

    Filtered by the folder's *current* datasets rather than by a stored folder
    id, because `save_chart` records the dataset the conversation resolved to,
    not the folder it ran over. That turns out to be the behaviour a user
    expects anyway: move a file into the folder and the charts pinned from it
    become available to the board.
    """
    if not dataset_ids:
        return []
    rows = session.exec(
        select(SavedChartRow)
        .where(
            SavedChartRow.owner_id == owner_id,
            SavedChartRow.dataset_id.in_(dataset_ids),  # type: ignore[union-attr]
        )
        .order_by(SavedChartRow.saved_at.desc())
    ).all()
    return [_saved_chart(row) for row in rows]


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


def _layout(row: DashboardChartRow) -> ChartLayout:
    return ChartLayout(x=row.x, y=row.y, w=row.w, h=row.h)


def _chart(row: DashboardChartRow) -> DashboardChart:
    return DashboardChart(
        id=row.id,
        title=row.title,
        question=row.question,
        sql=row.sql,
        chartType=row.chart_type,
        spec=row.spec,
        layout=_layout(row),
    )


def _dashboard(row: DashboardRow, folder_name: str, chart_count: int) -> Dashboard:
    return Dashboard(
        id=row.id,
        name=row.name,
        folderId=row.folder_id,
        folderName=folder_name,
        chartCount=chart_count,
        createdAt=row.created_at.isoformat(),
        updatedAt=row.updated_at.isoformat(),
    )


def _folder_names(session: Session, folder_ids: list[str]) -> dict[str, str]:
    if not folder_ids:
        return {}
    rows = session.exec(
        select(FolderModel).where(FolderModel.id.in_(folder_ids))  # type: ignore[union-attr]
    ).all()
    return {row.id: row.name for row in rows}


def _chart_rows(session: Session, dashboard_id: str) -> list[DashboardChartRow]:
    """Charts in reading order: top to bottom, then left to right.

    Sorted here rather than in the browser so a board that has never been
    dragged still comes back in the order things were added, and so the grid
    gets a stable list to key off.
    """
    return list(
        session.exec(
            select(DashboardChartRow)
            .where(DashboardChartRow.dashboard_id == dashboard_id)
            .order_by(DashboardChartRow.y, DashboardChartRow.x, DashboardChartRow.created_at)
        ).all()
    )


def list_dashboards(session: Session, owner_id: str) -> list[Dashboard]:
    rows = list(
        session.exec(
            select(DashboardRow)
            .where(DashboardRow.owner_id == owner_id)
            .order_by(DashboardRow.updated_at.desc())
        ).all()
    )
    names = _folder_names(session, [r.folder_id for r in rows])
    out = []
    for row in rows:
        count = len(_chart_rows(session, row.id))
        out.append(_dashboard(row, names.get(row.folder_id, ""), count))
    return out


def _get_row(session: Session, owner_id: str, dashboard_id: str) -> DashboardRow | None:
    return session.exec(
        select(DashboardRow).where(
            DashboardRow.id == dashboard_id, DashboardRow.owner_id == owner_id
        )
    ).first()


def get_dashboard(session: Session, owner_id: str, dashboard_id: str) -> DashboardDetail | None:
    row = _get_row(session, owner_id, dashboard_id)
    if row is None:
        return None
    charts = [_chart(c) for c in _chart_rows(session, row.id)]
    names = _folder_names(session, [row.folder_id])
    base = _dashboard(row, names.get(row.folder_id, ""), len(charts))
    return DashboardDetail(**base.model_dump(), charts=charts)


def folder_id_of(session: Session, owner_id: str, dashboard_id: str) -> str | None:
    row = _get_row(session, owner_id, dashboard_id)
    return row.folder_id if row else None


def create_dashboard(
    session: Session, owner_id: str, name: str, folder_id: str, folder_name: str
) -> Dashboard:
    row = DashboardRow(id=new_id("db"), owner_id=owner_id, name=name, folder_id=folder_id)
    session.add(row)
    session.flush()
    return _dashboard(row, folder_name, 0)


def rename_dashboard(
    session: Session, owner_id: str, dashboard_id: str, name: str
) -> Dashboard | None:
    row = _get_row(session, owner_id, dashboard_id)
    if row is None:
        return None
    row.name = name
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.flush()
    names = _folder_names(session, [row.folder_id])
    return _dashboard(row, names.get(row.folder_id, ""), len(_chart_rows(session, row.id)))


def delete_dashboard(session: Session, owner_id: str, dashboard_id: str) -> bool:
    row = _get_row(session, owner_id, dashboard_id)
    if row is None:
        return False
    # Explicit rather than relying on the FK cascade: the in-memory backend has
    # no cascade, and a difference in what "delete a dashboard" leaves behind
    # between the two backends is the kind of thing tests never catch.
    for chart in _chart_rows(session, dashboard_id):
        session.delete(chart)
    session.delete(row)
    return True


def _next_row(session: Session, dashboard_id: str) -> int:
    """The y a new chart lands on: below everything already there.

    Placement is not a judgement call — the plan says new charts go at the
    bottom and the user drags from there. This is that rule.
    """
    rows = _chart_rows(session, dashboard_id)
    return max((r.y + r.h for r in rows), default=0)


def add_chart(
    session: Session,
    owner_id: str,
    dashboard_id: str,
    *,
    title: str,
    question: str,
    sql: str,
    chart_type: ChartType,
    spec: dict,
) -> DashboardChart | None:
    board = _get_row(session, owner_id, dashboard_id)
    if board is None:
        return None
    row = DashboardChartRow(
        id=new_id("dc"),
        dashboard_id=dashboard_id,
        title=title,
        question=question,
        sql=sql,
        chart_type=chart_type,
        spec=spec,
        x=0,
        y=_next_row(session, dashboard_id),
        w=DEFAULT_CHART_W,
        h=DEFAULT_CHART_H,
    )
    session.add(row)
    board.updated_at = datetime.utcnow()
    session.add(board)
    session.flush()
    return _chart(row)


def get_chart(
    session: Session, owner_id: str, dashboard_id: str, chart_id: str
) -> DashboardChart | None:
    if _get_row(session, owner_id, dashboard_id) is None:
        return None
    row = session.exec(
        select(DashboardChartRow).where(
            DashboardChartRow.id == chart_id,
            DashboardChartRow.dashboard_id == dashboard_id,
        )
    ).first()
    return _chart(row) if row else None


def remove_chart(session: Session, owner_id: str, dashboard_id: str, chart_id: str) -> bool:
    board = _get_row(session, owner_id, dashboard_id)
    if board is None:
        return False
    row = session.exec(
        select(DashboardChartRow).where(
            DashboardChartRow.id == chart_id,
            DashboardChartRow.dashboard_id == dashboard_id,
        )
    ).first()
    if row is None:
        return False
    session.delete(row)
    board.updated_at = datetime.utcnow()
    session.add(board)
    return True


def set_layout(
    session: Session, owner_id: str, dashboard_id: str, layout: dict[str, ChartLayout]
) -> DashboardDetail | None:
    board = _get_row(session, owner_id, dashboard_id)
    if board is None:
        return None
    for row in _chart_rows(session, dashboard_id):
        position = layout.get(row.id)
        if position is None:
            # Charts the client didn't mention keep their place. A layout that
            # arrived mid-add would otherwise silently reset the new card.
            continue
        row.x, row.y, row.w, row.h = position.x, position.y, position.w, position.h
        session.add(row)
    board.updated_at = datetime.utcnow()
    session.add(board)
    session.flush()
    return get_dashboard(session, owner_id, dashboard_id)
