"""Postgres repository for the `notifications` domain (user-scoped).

The API's `time` is a relative display string ("12 min ago", "yesterday");
the database stores the real `created_at` timestamp and this module renders it at
read time, so the label stays live instead of frozen like a fixture string.
"""

import secrets
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import Notification as NotificationRow
from app.schemas.notifications import Notification, NotificationAction


#: The bell shows the latest few; older ones are noise, not history.
MAX_LISTED = 50


def _relative(created_at: datetime) -> str:
    seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
    minutes, hours, days = seconds / 60, seconds / 3600, seconds / 86400
    if minutes < 1:
        return "recién"
    if minutes < 60:
        return f"hace {int(minutes)} min"
    if hours < 24:
        return f"hace {int(hours)} h"
    if days < 2:
        return "ayer"
    return f"hace {int(days)} días"


def _notification(row: NotificationRow) -> Notification:
    action = None
    if row.action_label is not None:
        action = NotificationAction(label=row.action_label, href=row.action_href)
    return Notification(
        id=row.id,
        tone=row.tone,
        title=row.title,
        body=row.body,
        time=_relative(row.created_at),
        read=row.read,
        action=action,
    )


def list_notifications(session: Session, user_id: str) -> list[Notification]:
    rows = session.exec(
        select(NotificationRow).where(NotificationRow.user_id == user_id).order_by(NotificationRow.created_at.desc()).limit(MAX_LISTED)
    ).all()
    return [_notification(row) for row in rows]


def unread_count(session: Session, user_id: str) -> int:
    return session.exec(
        select(func.count())
        .select_from(NotificationRow)
        .where(NotificationRow.user_id == user_id, NotificationRow.read == False)  # noqa: E712
    ).one()


def mark_read(session: Session, user_id: str, notification_id: str) -> bool:
    row = session.exec(
        select(NotificationRow).where(NotificationRow.id == notification_id, NotificationRow.user_id == user_id)
    ).first()
    if row is None:
        return False
    row.read = True
    session.add(row)
    return True


def mark_all_read(session: Session, user_id: str) -> None:
    rows = session.exec(select(NotificationRow).where(NotificationRow.user_id == user_id)).all()
    for row in rows:
        row.read = True
        session.add(row)


def create(
    session: Session,
    user_id: str,
    *,
    tone: str,
    title: str,
    body: str,
    action: NotificationAction | None = None,
) -> None:
    session.add(
        NotificationRow(
            id="n_" + secrets.token_urlsafe(8),
            user_id=user_id,
            tone=tone,
            title=title,
            body=body,
            action_label=action.label if action else None,
            action_href=action.href if action else None,
            created_at=datetime.now(timezone.utc),
        )
    )
