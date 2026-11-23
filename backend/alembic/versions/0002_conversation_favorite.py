"""conversation favorite flag

Revision ID: 0002_conversation_favorite
Revises: 0001_baseline
Create Date: 2026-07-27

Adds `conversations.favorite` — the successor to the old hand-applied
`0002_conversation_favorite.sql` (no longer in the repo; this revision is now
the real, applied source of truth). Supports the `HistoryItem.favorite` flag
surfaced by `app.repositories.library.list_history`.

Unlike 0001_baseline (a raw-SQL baseline reproducing pre-Alembic history),
this is a normal Alembic revision using the `op` API — the pattern every
future migration should follow.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_conversation_favorite"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("conversations", "favorite")
