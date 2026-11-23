"""conversation_datasets + merge the two heads

Revision ID: 0005_conversation_datasets
Revises: 0003_module_b, 0004_english_enum_values
Create Date: 2026-08-01

Two jobs, deliberately in one revision.

**1. It merges a divergent history.** `0003_module_b` and
`0003_verification_code_attempts` were both written against `0002`, and
`0004_english_enum_values` chained onto the latter. The result was *two heads*,
so `alembic upgrade head` failed outright with "Multiple head revisions are
present" — meaning neither Module B's tables nor the English enum rename could
be applied at all. Nothing about that was visible from reading any single file;
it only shows up in `alembic heads`. This revision names both as its
`down_revision`, which is Alembic's merge point.

**2. It adds `conversation_datasets`** — the members of a conversation whose
source is an explicit multi-selection of files. `conversations` can point at one
dataset (`dataset_id`) or one folder (`folder_id`), and neither column can hold
a list, so N arbitrary datasets had nowhere to live. The `conversation_source_ck`
constraint is widened to admit `datasets`, whose rows carry *neither* FK.

`position` is stored because the order decides the layout of Module A's prompt;
a prompt that reshuffled between two reads would make a generation impossible to
reproduce.

**Enum note.** `datasets` is added to the `conversation_source_type` Postgres
enum with `ADD VALUE IF NOT EXISTS`. Two separate PostgreSQL restrictions apply
here and conflating them cost a failed deploy:

1. *Adding* a value inside a transaction block is forbidden only on PostgreSQL
   < 12. This instance is 16.14, so the `ALTER TYPE` itself is fine.
2. *Using* the new value in the same transaction that added it is forbidden on
   every version — "unsafe use of new value ... New enum values must be
   committed before they can be used".

Rule 2 bites because `env.py` runs the whole upgrade in one transaction (no
`transaction_per_migration`), so splitting this into two revisions would not
help either. The `CHECK` constraint below therefore refers to the new member
only by exclusion (`NOT IN ('dataset', 'folder')`), which needs no `COMMIT`
and keeps the upgrade atomic.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_conversation_datasets"
down_revision = ("0003_module_b", "0004_english_enum_values")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE conversation_source_type ADD VALUE IF NOT EXISTS 'datasets'")

    op.create_table(
        "conversation_datasets",
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "dataset_id"),
    )
    op.create_index(
        "conversation_datasets_dataset_idx", "conversation_datasets", ["dataset_id"]
    )

    # Widened, not replaced: a `datasets` conversation sets neither FK, which
    # the original constraint forbade.
    #
    # The third branch says `NOT IN ('dataset', 'folder')` rather than
    # `= 'datasets'` on purpose. PostgreSQL 12+ allows `ALTER TYPE ... ADD VALUE`
    # inside a transaction block, but it still forbids *using* the new label in
    # that same transaction ("unsafe use of new value ... New enum values must be
    # committed before they can be used"). `env.py` configures Alembic without
    # `transaction_per_migration`, so the entire `upgrade head` is one
    # transaction — the ADD VALUE above and this constraint are unavoidably in
    # it together.
    #
    # Naming only labels that already existed sidesteps that completely, with no
    # `COMMIT` hack and no loss of atomicity (a later failure still rolls the
    # whole upgrade back). The two forms are equivalent today, since `datasets`
    # is the only other member.
    op.drop_constraint("conversations_source_ck", "conversations", type_="check")
    op.create_check_constraint(
        "conversations_source_ck",
        "conversations",
        "(source_type = 'dataset' AND dataset_id IS NOT NULL AND folder_id IS NULL)"
        " OR (source_type = 'folder' AND folder_id IS NOT NULL AND dataset_id IS NULL)"
        " OR (source_type NOT IN ('dataset', 'folder')"
        " AND dataset_id IS NULL AND folder_id IS NULL)",
    )


def downgrade() -> None:
    # Any `datasets` conversation has to go first: the narrowed constraint would
    # reject its row, and there is no single dataset to collapse it onto without
    # silently changing what the conversation was asked over.
    op.execute(
        "DELETE FROM messages WHERE conversation_id IN"
        " (SELECT id FROM conversations WHERE source_type = 'datasets')"
    )
    op.execute("DELETE FROM conversations WHERE source_type = 'datasets'")

    op.drop_constraint("conversations_source_ck", "conversations", type_="check")
    op.create_check_constraint(
        "conversations_source_ck",
        "conversations",
        "(source_type = 'dataset' AND dataset_id IS NOT NULL AND folder_id IS NULL)"
        " OR (source_type = 'folder' AND folder_id IS NOT NULL AND dataset_id IS NULL)",
    )

    op.drop_index("conversation_datasets_dataset_idx", table_name="conversation_datasets")
    op.drop_table("conversation_datasets")
    # The enum label is deliberately left in place: PostgreSQL has no
    # `ALTER TYPE ... DROP VALUE`, and recreating the type would rewrite every
    # dependent column for nothing.
