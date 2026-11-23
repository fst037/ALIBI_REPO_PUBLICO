"""SQLModel/SQLAlchemy engine + session — every repository's DB entry point.

Same rationale as the connection handling this replaced: no long-lived pool,
Lambda-cold-start-friendly, works over the local SSM tunnel (see git history
of `app/db/pool.py` for the original psycopg-pool experiment that motivated
this). `get_engine()` gives a cached `Engine` (SQLAlchemy pools connections
itself, but `NullPool` keeps a single short-lived connection per checkout —
one connection per call, not a persistent pool) and `session_scope()` is a
context manager that commits on a clean exit / rolls back on exception.
`app.db.pool.is_configured()` is still the single switch deciding whether a
service call reaches here at all, or falls back to the in-memory fixtures.

`ALIBI_DATABASE_URL` is a psycopg **keyword-string** DSN (see
`app/core/config.py`), not a SQLAlchemy URL — `_sqlalchemy_url()` translates
one into the other so the existing `.env` format doesn't need to change.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import URL
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from app.core.config import get_settings


def _sqlalchemy_url(dsn: str) -> URL:
    """Translate a psycopg keyword-string DSN into a SQLAlchemy URL."""
    parts = conninfo_to_dict(dsn)
    query = {k: str(v) for k, v in parts.items() if k not in {"host", "port", "dbname", "user", "password"}}
    return URL.create(
        "postgresql+psycopg",
        username=parts.get("user"),
        password=parts.get("password"),
        host=parts.get("host"),
        port=int(parts["port"]) if parts.get("port") else None,
        database=parts.get("dbname"),
        query=query,
    )


@lru_cache
def get_engine():
    settings = get_settings()
    dsn = settings.database_url
    if not dsn:
        raise RuntimeError("ALIBI_DATABASE_URL is not set; cannot create a DB engine")
    return create_engine(
        _sqlalchemy_url(dsn),
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Open a session for the duration of the block, then close it.

    Commits on a clean exit, rolls back on exception — same contract as
    `app.db.pool.connection()`.
    """
    with Session(get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
