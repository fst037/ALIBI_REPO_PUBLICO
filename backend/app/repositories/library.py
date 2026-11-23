"""Postgres repository for the `library` domain (saved queries + history).

Owner-scoped. `dataset_name` is denormalised on the saved-query row (so it keeps
a readable source even if the dataset is later deleted); the service resolves it
from the datasets repository before inserting.

History is a read-time projection over conversations + messages (title, source
dataset, question count, last chart type, favorite), grouped by recency.
"""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, or_, select

from app.core.security import new_id
from app.db.models import (
    Conversation as ConversationRow,
    ConversationDataset as ConversationDatasetRow,
    ConversationSourceType as RowSourceType,
    DatasetModel,
    Folder as FolderRow,
    Message as MessageRow,
    SavedQuery as SavedQueryRow,
)
from app.schemas.charts import ChartType
from app.schemas.library import HistoryGroup, HistoryItem, SavedQuery, SaveQueryRequest

_WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _saved_query(row: SavedQueryRow) -> SavedQuery:
    return SavedQuery(
        id=row.id,
        question=row.question,
        datasetId=row.dataset_id,
        datasetName=row.dataset_name,
        date=row.saved_at.date().isoformat(),
        chartType=row.chart_type,
    )


def list_saved_queries(session: Session, owner_id: str, query: str | None) -> list[SavedQuery]:
    stmt = select(SavedQueryRow).where(SavedQueryRow.owner_id == owner_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(SavedQueryRow.question.ilike(like), SavedQueryRow.dataset_name.ilike(like)))
    stmt = stmt.order_by(SavedQueryRow.saved_at.desc())
    return [_saved_query(row) for row in session.exec(stmt).all()]


def save_query(session: Session, owner_id: str, request: SaveQueryRequest, dataset_name: str) -> SavedQuery:
    row = SavedQueryRow(
        id=new_id("sq"),
        owner_id=owner_id,
        question=request.question,
        dataset_id=request.datasetId,
        dataset_name=dataset_name,
        chart_type=request.chartType,
    )
    session.add(row)
    session.flush()
    return _saved_query(row)


def delete_saved_query(session: Session, owner_id: str, saved_query_id: str) -> bool:
    row = session.exec(
        select(SavedQueryRow).where(SavedQueryRow.id == saved_query_id, SavedQueryRow.owner_id == owner_id)
    ).first()
    if row is None:
        return False
    session.delete(row)
    return True


# ── History (derived from conversations + messages) ─────────────────────────


def _time_label(created_at: datetime, now: datetime) -> str:
    secs = (now - created_at).total_seconds()
    if secs < 3600:
        return f"hace {max(int(secs // 60), 1)} min"
    if secs < 86400:
        return f"hace {int(secs // 3600)} h"
    if secs < 7 * 86400:
        return _WEEKDAYS[created_at.weekday()]
    return f"{int(secs // (7 * 86400))} sem"


def _bucket(created_at: datetime, now: datetime) -> str:
    if created_at.date() == now.date():
        return "Hoy"
    if (now - created_at).days < 7:
        return "Esta semana"
    return "Anteriores"


def _source_label(row) -> str:
    """What a History row shows under "source" — mirrors `conversations._source`.

    A conversation runs over a folder, so the folder's name is the answer. The
    `dataset`/`datasets` branches are for rows written before the folder-only
    contract: they can no longer be created,
    but they still have to render.
    """
    if row.source_type is RowSourceType.folder:
        return row.folder_name or "Folder"
    if row.source_type is RowSourceType.datasets:
        return f"{row.dataset_count} datasets"
    return row.dataset_name or "Dataset"


def list_history(session: Session, owner_id: str, since: datetime | None = None) -> list[HistoryGroup]:
    question_count = (
        select(func.count())
        .select_from(MessageRow)
        .where(MessageRow.conversation_id == ConversationRow.id, MessageRow.role == "assistant")
        .correlate(ConversationRow)
        .scalar_subquery()
    )
    last_chart_type = (
        select(MessageRow.content["result"]["autoChartType"].astext)
        .where(MessageRow.conversation_id == ConversationRow.id, MessageRow.role == "assistant")
        .order_by(MessageRow.position.desc())
        .limit(1)
        .correlate(ConversationRow)
        .scalar_subquery()
    )
    # Counted, not named: a legacy `datasets` conversation points at no single
    # entity. Only these rows read it.
    dataset_count = (
        select(func.count())
        .select_from(ConversationDatasetRow)
        .where(ConversationDatasetRow.conversation_id == ConversationRow.id)
        .correlate(ConversationRow)
        .scalar_subquery()
    )
    # Outer joins on purpose. A folder conversation — the only shape that can be
    # created now — has no `dataset_id`, so the inner join this used to do
    # dropped *every* current conversation from History and left only pre-folder
    # rows behind.
    stmt = (
        select(
            ConversationRow.id,
            ConversationRow.title,
            ConversationRow.favorite,
            ConversationRow.created_at,
            ConversationRow.source_type,
            DatasetModel.name.label("dataset_name"),
            FolderRow.name.label("folder_name"),
            dataset_count.label("dataset_count"),
            question_count.label("question_count"),
            last_chart_type.label("chart_type"),
        )
        .outerjoin(DatasetModel, DatasetModel.id == ConversationRow.dataset_id)
        .outerjoin(FolderRow, FolderRow.id == ConversationRow.folder_id)
        .where(ConversationRow.owner_id == owner_id)
        .order_by(ConversationRow.created_at.desc())
    )
    if since is not None:
        stmt = stmt.where(ConversationRow.created_at >= since)
    rows = session.exec(stmt).all()

    now = datetime.now(timezone.utc)
    # Preserve group order (recency); only emit non-empty groups.
    groups: dict[str, list[HistoryItem]] = {"Hoy": [], "Esta semana": [], "Anteriores": []}
    for row in rows:
        groups[_bucket(row.created_at, now)].append(
            HistoryItem(
                id=row.id,
                title=row.title,
                source=_source_label(row),
                questionCount=row.question_count,
                time=_time_label(row.created_at, now),
                # A conversation with no executed query yet has no chart — default
                # to the table view.
                chartType=ChartType(row.chart_type) if row.chart_type else ChartType.table,
                favorite=row.favorite,
            )
        )
    return [HistoryGroup(date=label, items=items) for label, items in groups.items() if items]
