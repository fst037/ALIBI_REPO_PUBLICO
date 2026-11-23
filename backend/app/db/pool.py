"""`is_configured()` lets the domain services decide, per call, whether to hit
the database (ALIBI_DATABASE_URL set) or fall back to the in-memory fixtures.

The actual connection is opened by `app.db.engine.session_scope()` (SQLModel/
SQLAlchemy) — every repository has been migrated there; this module now only
holds the DB-vs-fixture switch itself.
"""

from app.core.config import get_settings


def is_configured() -> bool:
    """True when a database DSN is set (otherwise services use fixtures)."""
    return bool(get_settings().database_url)
