"""asynchronous answer runs

Revision ID: 0008_conversation_runs
Revises: 0007_dashboards
Create Date: 2026-10-05

One table. A question is now answered by a *run*: `POST …/runs` records it as
`queued` and returns at once, a worker executes it outside API Gateway's 29 s
request ceiling, and the client polls the row until it is `done` or `failed`
(the agent harness).

`content` holds the whole run as the API serves it (steps, final message, guard
or error): it is written as a unit after every step and read as a unit on every
poll, so splitting it into columns would buy nothing. `status` and the
timestamps sit beside it only because they are what a query filters or orders by.

CASCADE on the conversation, like `dashboard_charts` on its board: a run has no
meaning without the conversation it answers in.

No data migration: existing conversations simply have no runs.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_conversation_runs"
down_revision = "0007_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "conversation_runs_conversation_idx", "conversation_runs", ["conversation_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("conversation_runs_conversation_idx", table_name="conversation_runs")
    op.drop_table("conversation_runs")
