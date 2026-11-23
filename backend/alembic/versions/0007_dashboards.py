"""named dashboards over a folder, holding chart ideas rather than snapshots

Revision ID: 0007_dashboards
Revises: 0006_persistent_sessions
Create Date: 2026-08-08

Two tables.

`dashboards` is a named board scoped to exactly one folder — the same scoping
rule conversations follow, and for the same reason: a folder is resolved on
every read, so a file added to it later is visible to the whole board without
anything being re-pinned.

`dashboard_charts` holds the *idea* of a chart — `sql` plus a Vega-Lite `spec`
with no data — and its position in the grid. Opening a board re-runs the SQL
against the folder's current Parquet and draws the stored spec over the fresh
rows. That is the whole point of the feature, and it is only possible because
a Module C spec already carried no `data` (see `app/schemas/charts.ChartSpec`).

`saved_charts` is deliberately untouched. It stops being a screen of its own
(the per-dataset gallery this replaces) and becomes the pool a board is filled
from; adding one **copies** its idea onto the board rather than referencing it,
so tidying up a pin cannot break a dashboard.

No data migration. Existing pinned charts stay exactly where they are and
remain addable to any board over their folder; nobody's charts move or vanish.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_dashboards"
down_revision = "0006_persistent_sessions"
branch_labels = None
depends_on = None

# Already created by the baseline migration for `saved_charts`/`saved_queries`.
# `create_type=False` stops Alembic trying to CREATE TYPE a second time.
_CHART_TYPE = postgresql.ENUM(name="chart_type", create_type=False)


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("folder_id", sa.String(), sa.ForeignKey("folders.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("dashboards_owner_idx", "dashboards", ["owner_id", "updated_at"])
    op.create_index("ix_dashboards_folder_id", "dashboards", ["folder_id"])

    op.create_table(
        "dashboard_charts",
        sa.Column("id", sa.String(), primary_key=True),
        # CASCADE: a chart has no meaning without its board, and leaving orphan
        # rows behind would make "delete this dashboard" a lie.
        sa.Column(
            "dashboard_id",
            sa.String(),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("question", sa.String(), nullable=False, server_default=""),
        sa.Column("sql", sa.String(), nullable=False),
        sa.Column("chart_type", _CHART_TYPE, nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("y", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("w", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("h", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("dashboard_charts_board_idx", "dashboard_charts", ["dashboard_id"])


def downgrade() -> None:
    op.drop_index("dashboard_charts_board_idx", table_name="dashboard_charts")
    op.drop_table("dashboard_charts")
    op.drop_index("ix_dashboards_folder_id", table_name="dashboards")
    op.drop_index("dashboards_owner_idx", table_name="dashboards")
    op.drop_table("dashboards")
