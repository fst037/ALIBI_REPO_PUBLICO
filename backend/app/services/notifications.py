"""`notifications` domain service — swap point between storage backends.

User-scoped in the database when configured, else the in-memory fixtures.
"""

import logging

from app.db import pool
from app.db.engine import session_scope
from app.repositories import notifications as repo
from app.schemas.notifications import Notification, NotificationAction
from app.services import notifications_memory as mem

logger = logging.getLogger("alibi.notifications")


def list_notifications(user_id: str) -> list[Notification]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_notifications(session, user_id)
    return mem.list_notifications(user_id)


def unread_count(user_id: str) -> int:
    if pool.is_configured():
        with session_scope() as session:
            return repo.unread_count(session, user_id)
    return mem.unread_count(user_id)


def mark_read(user_id: str, notification_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.mark_read(session, user_id, notification_id)
    return mem.mark_read(user_id, notification_id)


def mark_all_read(user_id: str) -> list[Notification]:
    if pool.is_configured():
        with session_scope() as session:
            repo.mark_all_read(session, user_id)
            session.flush()
            return repo.list_notifications(session, user_id)
    return mem.mark_all_read(user_id)


def notify(
    user_id: str, *, tone: str, title: str, body: str, action: NotificationAction | None = None
) -> None:
    """Add a notification to the user's inbox (the bell).

    Never raises: a notice is a courtesy, and the operation that triggered it
    (an ingestion, an answer, a ledger write) must not fail because the bell did.
    """
    try:
        if pool.is_configured():
            with session_scope() as session:
                repo.create(session, user_id, tone=tone, title=title, body=body, action=action)
            return
        mem.create(user_id, tone=tone, title=title, body=body, action=action)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("could not notify user %s: %s", user_id, title)
