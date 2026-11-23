"""Datasets keep the ETLite recipe they were confirmed with

Revision ID: 0015_dataset_cleaning_recipe
Revises: 0014_google_sign_in
Create Date: 2026-10-17

Updating a dataset with a new file. A new file
that appends to or replaces a dataset is cleaned with the recipe the dataset was
confirmed with, offered for editing each time. JSONB, nullable: NULL for every
dataset confirmed before this, and for any confirmed with no cleaning.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0015_dataset_cleaning_recipe"
down_revision = "0014_google_sign_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("cleaning_recipe", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "cleaning_recipe")
