"""verification code attempt counter

Revision ID: 0003_verification_code_attempts
Revises: 0002_conversation_favorite
Create Date: 2026-07-28

Adds `verification_codes.attempts`, the wrong-guess counter that lets
`app.repositories.auth` burn a code after N failures. Needed because the
codes are only 6 digits: without an attempt cap, the whole 10^6 space is
walkable inside the 10-minute TTL, and the second factor stops being one.

Existing rows get 0 via the server default — they are all either consumed or
expired by now anyway (the pre-existing rows were written by the stub, which
stored an empty code and never checked it).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_verification_code_attempts"
down_revision = "0002_conversation_favorite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verification_codes",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("verification_codes", "attempts")
