"""Sign in with Google: the Google account an ALIBI user is linked to

Revision ID: 0014_google_sign_in
Revises: 0013_pro_prices_haiku_sonnet
Create Date: 2026-10-14

Sign in with Google, next to email and password. `google_sub` is the ID token's `sub` claim — Google's stable id for
the account, which survives the person changing their address; the email is
not the key. NULL for accounts that never signed in with Google. Unique: one
Google account opens exactly one ALIBI account. `password_hash` was already
nullable, which is what an account created through Google has.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_google_sign_in"
down_revision = "0013_pro_prices_haiku_sonnet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.Text(), nullable=True))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")
