"""Pydantic models for the `notifications` tag of `contracts/auth.yaml`."""

from typing import Literal

from pydantic import BaseModel


class NotificationAction(BaseModel):
    label: str
    href: str


class Notification(BaseModel):
    id: str
    tone: Literal["ok", "warn", "err", "info"]
    title: str
    body: str
    time: str
    read: bool
    action: NotificationAction | None = None


class UnreadCount(BaseModel):
    count: int
