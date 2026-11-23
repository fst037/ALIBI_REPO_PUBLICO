"""`notifications` domain routes (user-scoped)."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.notifications import Notification, UnreadCount
from app.services import notifications as store

router = APIRouter(tags=["notifications"])


@router.get(
    "/notifications",
    response_model=list[Notification],
    response_model_exclude_none=True,
    summary="List notifications.",
)
def list_notifications(user: User = Depends(require_user)) -> list[Notification]:
    return store.list_notifications(user.id)


@router.get("/notifications/unread-count", response_model=UnreadCount, summary="Unread count (badge).")
def get_unread_count(user: User = Depends(require_user)) -> UnreadCount:
    return UnreadCount(count=store.unread_count(user.id))


@router.post(
    "/notifications/{notification_id}/read",
    response_model=list[Notification],
    response_model_exclude_none=True,
    summary="Mark a notification as read.",
)
def mark_notification_read(
    notification_id: str, user: User = Depends(require_user)
) -> list[Notification] | JSONResponse:
    if not store.mark_read(user.id, notification_id):
        return error_response(404, "not_found", "No se encontró la notificación.")
    return store.list_notifications(user.id)


@router.post(
    "/notifications/read-all",
    response_model=list[Notification],
    response_model_exclude_none=True,
    summary="Mark all as read.",
)
def mark_all_notifications_read(user: User = Depends(require_user)) -> list[Notification]:
    return store.mark_all_read(user.id)
