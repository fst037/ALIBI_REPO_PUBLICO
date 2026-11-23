"""sessions that don't expire ("Keep me signed in")

Revision ID: 0006_persistent_sessions
Revises: 0005_conversation_datasets
Create Date: 2026-08-04

Makes `auth_sessions.expires_at` nullable, where NULL means "this session has
no expiry — it ends when the user signs out".

The alternative was a very distant `expires_at`, which would have made every
reader compare against a date that means "never" without saying so. NULL says
it, and `get_user_by_token` has to handle it explicitly, which is the point.

Existing rows are untouched: they keep their expiry and lapse normally.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_persistent_sessions"
down_revision = "0005_conversation_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "auth_sessions", "expires_at", existing_type=sa.DateTime(), nullable=True
    )


def downgrade() -> None:
    # Sessions with no expiry cannot be represented once the column is NOT NULL
    # again, so they are dropped rather than given an invented expiry — the
    # affected users sign in again, which is the honest outcome.
    op.execute("DELETE FROM auth_sessions WHERE expires_at IS NULL")
    op.alter_column(
        "auth_sessions", "expires_at", existing_type=sa.DateTime(), nullable=False
    )
