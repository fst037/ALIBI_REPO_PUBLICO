"""In-memory notifications store: the fallback with no database.

Single-user, so `user_id` is accepted but ignored. Seeded from fixtures;
mutations persist within a process run.
"""

import secrets

from app.schemas.notifications import Notification, NotificationAction
from app.services._fixtures import load_json

_notifications: list[Notification] = [Notification(**n) for n in load_json("notifications.json")]


def list_notifications(_user_id: str) -> list[Notification]:
    return _notifications


def unread_count(_user_id: str) -> int:
    return sum(1 for n in _notifications if not n.read)


def mark_read(_user_id: str, notification_id: str) -> bool:
    target = next((n for n in _notifications if n.id == notification_id), None)
    if target is None:
        return False
    target.read = True
    return True


def mark_all_read(_user_id: str) -> list[Notification]:
    for n in _notifications:
        n.read = True
    return _notifications


def create(_user_id: str, *, tone: str, title: str, body: str, action: NotificationAction | None = None) -> None:
    _notifications.insert(
        0,
        Notification(
            id="n_" + secrets.token_urlsafe(8), tone=tone, title=title, body=body, time="recién", read=False, action=action
        ),
    )


def reset() -> None:
    _notifications[:] = [Notification(**n) for n in load_json("notifications.json")]
