"""Postgres repository for the `conversations` domain.

Owner-scoped. A conversation runs over a folder; that replaced the earlier single-dataset and explicit-selection
shapes. `datasetIds` on the wire is the resolved list, resolved *here* on each
read, which is what lets a dataset added to the folder later join a
conversation that is already open.

`db.models.ConversationSourceType` still carries the old `dataset`/`datasets`
labels for rows created before that decision; this module tolerates reading
them (see `_source`) but never writes them.

Messages are polymorphic (user/assistant/guard/system); each is stored whole as
JSONB in `messages.content` and reconstructed through the `Message` discriminated
union, with `role`/`position` promoted to columns for ordering and filtering.
"""

from sqlalchemy import func
from sqlmodel import Session, select
from pydantic import TypeAdapter

from app.core.security import new_id
from app.db.models import (
    Conversation as ConversationRow,
    ConversationDataset as ConversationDatasetRow,
    ConversationSourceType as RowSourceType,
    DatasetModel as DatasetRow,
    Folder as FolderRow,
    FolderDataset as FolderDatasetRow,
    Message as MessageRow,
)
from app.schemas.chat import (
    AssistantMessage,
    Conversation,
    ConversationSource,
    ConversationSourceType,
    Message,
    RunMode,
)
from app.schemas.datasets import DatasetStatus

_message_adapter: TypeAdapter[Message] = TypeAdapter(Message)


def _load_messages(session: Session, conversation_id: str) -> list[Message]:
    rows = session.exec(
        select(MessageRow.content).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.position)
    ).all()
    return [_message_adapter.validate_python(content) for content in rows]


def _resolve_datasets(session: Session, row: ConversationRow) -> list[str]:
    """The dataset ids this conversation actually queries.

    Ordered deterministically in all three cases: the prompt Module A receives is
    built from this list, and one that reshuffled between reads would make a
    generation impossible to reproduce.
    """
    if row.source_type is RowSourceType.folder:
        # Confirmed datasets only (Ingesta 1.2). A file just uploaded into the
        # folder is previewable but not queryable, so it joins the conversation
        # the moment its schema is confirmed — not before.
        return list(
            session.exec(
                select(FolderDatasetRow.dataset_id)
                .join(DatasetRow, DatasetRow.id == FolderDatasetRow.dataset_id)
                .where(
                    FolderDatasetRow.folder_id == row.folder_id,
                    DatasetRow.status == DatasetStatus.processed,
                )
                .order_by(FolderDatasetRow.added_at, FolderDatasetRow.dataset_id)
            ).all()
        )
    if row.source_type is RowSourceType.datasets:
        return list(
            session.exec(
                select(ConversationDatasetRow.dataset_id)
                .where(ConversationDatasetRow.conversation_id == row.id)
                .order_by(ConversationDatasetRow.position)
            ).all()
        )
    return [row.dataset_id] if row.dataset_id else []


def _source(session: Session, row: ConversationRow, dataset_ids: list[str]) -> ConversationSource:
    """The header descriptor — what the canvas titles the conversation with."""
    if row.source_type is RowSourceType.folder:
        name = session.exec(select(FolderRow.name).where(FolderRow.id == row.folder_id)).first()
        return ConversationSource(
            type=ConversationSourceType.folder, id=row.folder_id or "", name=name or "Folder"
        )
    # Legacy rows: `dataset`/`datasets` source types predate the folder-only
    # contract and can no longer be created,
    # but old rows may still carry them. `ConversationSourceType` (the wire
    # schema) dropped those members, so render legacy rows as best-effort
    # folder sources instead of crashing the whole list on one stale row.
    if row.source_type is RowSourceType.datasets:
        return ConversationSource(
            type=ConversationSourceType.folder, id="", name=f"{len(dataset_ids)} datasets"
        )
    name = session.exec(select(DatasetRow.name).where(DatasetRow.id == row.dataset_id)).first()
    return ConversationSource(
        type=ConversationSourceType.folder, id="", name=name or "Dataset"
    )


def _conversation(session: Session, row: ConversationRow) -> Conversation:
    dataset_ids = _resolve_datasets(session, row)
    return Conversation(
        id=row.id,
        title=row.title,
        source=_source(session, row, dataset_ids),
        datasetIds=dataset_ids,
        createdAt=row.created_at.date().isoformat(),
        favorite=row.favorite,
        mode=RunMode(row.answer_mode),
        messages=_load_messages(session, row.id),
    )


def list_conversations(session: Session, owner_id: str, recent: bool, limit: int) -> list[Conversation]:
    query = (
        select(ConversationRow).where(ConversationRow.owner_id == owner_id).order_by(ConversationRow.created_at.desc())
    )
    if recent:
        query = query.limit(limit)
    rows = session.exec(query).all()
    return [_conversation(session, row) for row in rows]


def get_conversation(session: Session, owner_id: str, conversation_id: str) -> Conversation | None:
    row = session.exec(
        select(ConversationRow).where(ConversationRow.id == conversation_id, ConversationRow.owner_id == owner_id)
    ).first()
    return _conversation(session, row) if row else None


def conversation_exists(session: Session, owner_id: str, conversation_id: str) -> bool:
    row = session.exec(
        select(ConversationRow.id).where(ConversationRow.id == conversation_id, ConversationRow.owner_id == owner_id)
    ).first()
    return row is not None


def create_conversation(
    session: Session,
    owner_id: str,
    source_type: ConversationSourceType,
    *,
    folder_id: str | None = None,
    mode: RunMode = RunMode.quick,
) -> Conversation:
    """Open a conversation over a folder.

    `source_type` is always `folder` — `dataset` and `datasets` were removed
    from the API-level `ConversationSourceType` (see its docstring); only the
    DB-level enum (`db.models.ConversationSourceType`) still carries the old
    labels, kept for the rows that already exist under them. `dataset_id` stays
    unset here for the same reason: a new conversation never sets it.
    """
    row = ConversationRow(
        id=new_id("conv"),
        owner_id=owner_id,
        source_type=RowSourceType(source_type.value),
        dataset_id=None,
        folder_id=folder_id,
        title="New query",
        answer_mode=mode.value,
    )
    session.add(row)
    session.flush()

    resolved = _resolve_datasets(session, row)
    return Conversation(
        id=row.id,
        title=row.title,
        source=_source(session, row, resolved),
        datasetIds=resolved,
        createdAt=row.created_at.date().isoformat(),
        mode=mode,
        messages=[],
    )


def _row(session: Session, owner_id: str, conversation_id: str) -> ConversationRow | None:
    return session.exec(
        select(ConversationRow).where(ConversationRow.id == conversation_id, ConversationRow.owner_id == owner_id)
    ).first()


def set_title(session: Session, owner_id: str, conversation_id: str, title: str) -> None:
    """Rename a conversation. *Whether* to rename is the caller's decision."""
    row = _row(session, owner_id, conversation_id)
    if row is None:
        return
    row.title = title
    session.add(row)
    session.flush()


def update_conversation(
    session: Session,
    owner_id: str,
    conversation_id: str,
    *,
    title: str | None = None,
    favorite: bool | None = None,
    mode: RunMode | None = None,
) -> Conversation | None:
    """Rename, star and/or set the default depth. `None` fields are left untouched."""
    row = _row(session, owner_id, conversation_id)
    if row is None:
        return None
    if title is not None:
        row.title = title
    if favorite is not None:
        row.favorite = favorite
    if mode is not None:
        row.answer_mode = mode.value
    session.add(row)
    session.flush()
    return _conversation(session, row)


def delete_conversation(session: Session, owner_id: str, conversation_id: str) -> bool:
    """Remove a conversation and everything hanging off it.

    Messages and the legacy `conversation_datasets` rows are deleted explicitly
    rather than left to a cascade: the schema declares the FKs but not
    `ON DELETE CASCADE`, so relying on one would leave orphan rows behind on
    Postgres and fail the delete outright once the FK is enforced.
    """
    row = _row(session, owner_id, conversation_id)
    if row is None:
        return False
    # Runs cascade at the database level too (migration 0008); deleted here as
    # well so this function's "explicit, not left to a cascade" rule holds.
    from app.repositories.runs import delete_conversation_runs

    delete_conversation_runs(session, conversation_id)
    for message in session.exec(select(MessageRow).where(MessageRow.conversation_id == conversation_id)).all():
        session.delete(message)
    for link in session.exec(
        select(ConversationDatasetRow).where(ConversationDatasetRow.conversation_id == conversation_id)
    ).all():
        session.delete(link)
    session.delete(row)
    session.flush()
    return True


def append_assistant_message(session: Session, owner_id: str, conversation_id: str, message: AssistantMessage) -> None:
    # Owner-checked here too, not only by the caller. It took `owner_id` and
    # ignored it, so the scoping depended entirely on `execute` remembering to
    # check first — the one function in this module that could write into
    # another user's conversation if a future caller forgot.
    if _row(session, owner_id, conversation_id) is None:
        return
    next_position = session.exec(
        select(func.coalesce(func.max(MessageRow.position), -1) + 1).where(
            MessageRow.conversation_id == conversation_id
        )
    ).one()
    session.add(
        MessageRow(
            id=message.id,
            conversation_id=conversation_id,
            position=next_position,
            role="assistant",
            content=message.model_dump(mode="json"),
        )
    )
    session.flush()


def get_assistant_message(session: Session, owner_id: str, message_id: str) -> AssistantMessage | None:
    row = session.exec(
        select(MessageRow)
        .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
        .where(MessageRow.id == message_id, MessageRow.role == "assistant", ConversationRow.owner_id == owner_id)
    ).first()
    return AssistantMessage(**row.content) if row else None
