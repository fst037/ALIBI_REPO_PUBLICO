from fastapi import APIRouter
from sqlalchemy import text

from app.db import pool
from app.db.engine import session_scope

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "alibi-backend"}


@router.get("/health/db")
def health_db() -> dict[str, str]:
    """Whether the database answers. While it is waking from an idle stop this
    is a 503 `db_waking` (see `app/db/waker.py`); the frontend polls it to know
    when to retry."""
    if pool.is_configured():
        with session_scope() as session:
            session.exec(text("SELECT 1"))
    return {"status": "ok"}
