"""Programmatic `alembic upgrade head`, shared by:
  * the local-dev startup hook (app.main, gated by ALIBI_AUTO_MIGRATE), and
  * the Lambda's explicit migrate-invoke path (app.main.handler), run once
    per deploy by backend-deploy.yml — never on cold start.

Never called during tests: both call sites gate on `pool.is_configured()`,
and conftest.py forces ALIBI_DATABASE_URL="" for the whole test session.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


def upgrade_to_head() -> None:
    config = Config(str(_ALEMBIC_INI))
    # alembic.ini's `script_location = alembic` is relative to the CWD Alembic
    # is invoked from, NOT to the ini file's own location — fine for the CLI
    # (always run from backend/) but not safe to assume here, since Lambda's
    # CWD is unpredictable. Override with an absolute path.
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
