"""Answer runs in Postgres (`conversation_runs`). Owner-scoped on every read."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from app.db.models import ConversationRun as RunRow
from app.schemas.runs import Run


def save_run(session: Session, owner_id: str, run: Run) -> None:
    """Insert or replace the run. Called after every step, so it stays small."""
    row = session.get(RunRow, run.id)
    content = run.model_dump(mode="json")
    if row is None:
        session.add(
            RunRow(
                id=run.id,
                owner_id=owner_id,
                conversation_id=run.conversationId,
                status=run.status.value,
                content=content,
            )
        )
    else:
        if row.owner_id != owner_id:
            return
        row.status = run.status.value
        row.content = content
        row.updated_at = datetime.utcnow()
        session.add(row)
    session.flush()


def get_run(session: Session, owner_id: str, run_id: str) -> Run | None:
    row = session.exec(select(RunRow).where(RunRow.id == run_id, RunRow.owner_id == owner_id)).first()
    return Run(**row.content) if row else None


def delete_conversation_runs(session: Session, conversation_id: str) -> None:
    for row in session.exec(select(RunRow).where(RunRow.conversation_id == conversation_id)).all():
        session.delete(row)
