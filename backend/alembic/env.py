from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.engine import _sqlalchemy_url
from app.db.models import SQLModel  # noqa: F401 — imported for its side effect (registers all tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _url() -> str:
    """The app's own ALIBI_DATABASE_URL (keyword DSN), translated the same
    way `app/db/engine.py` does — so `alembic upgrade head` always talks to
    whatever the app itself would connect to. `--sql` (offline mode) doesn't
    need this at all, only a real `upgrade`/`downgrade` does."""
    dsn = get_settings().database_url
    if not dsn:
        raise RuntimeError("ALIBI_DATABASE_URL is not set; cannot connect Alembic to a database")
    return _sqlalchemy_url(dsn).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (`alembic upgrade head --sql`)."""
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
