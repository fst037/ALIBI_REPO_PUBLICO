"""conversation answer mode (Quick / Deep)

Revision ID: 0009_conversation_answer_mode
Revises: 0008_conversation_runs
Create Date: 2026-10-09

Adds `conversations.answer_mode`: the conversation's default depth for the agent
harness. A run may still override it for
one question — that override lives on the run, not here.

Text with a CHECK rather than a Postgres enum, deliberately: adding a value to an
enum cannot be used in the same transaction that adds it (see 0005's docstring),
and a third mode would be exactly that kind of change. Existing conversations
become `quick`, which is what every one of them has been answering with.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_conversation_answer_mode"
down_revision = "0008_conversation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("answer_mode", sa.String(16), nullable=False, server_default="quick"),
    )
    op.create_check_constraint(
        "conversations_answer_mode_ck", "conversations", "answer_mode IN ('quick', 'deep')"
    )


def downgrade() -> None:
    op.drop_constraint("conversations_answer_mode_ck", "conversations", type_="check")
    op.drop_column("conversations", "answer_mode")
