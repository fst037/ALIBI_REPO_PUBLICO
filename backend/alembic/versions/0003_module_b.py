"""Module B — Zero-ETL ingestion: uploads, folders, richer column metadata

Revision ID: 0003_module_b
Revises: 0002_conversation_favorite
Create Date: 2026-07-28

Implements the control-plane half of Module B's two-data-planes design.
Nothing here stores the *contents* of an
uploaded file — rows live as Parquet in the object store and are read with
DuckDB. What this adds is:

  * `uploads`      — one row per uploaded file, tracking async processing.
  * `folders` + `folder_datasets` — one-level "projects"; the join table is a
    pure many-to-many mapping, so a dataset in two folders is one dataset with
    two links, never a copy.
  * `dataset_column_embeddings` — pgvector column embeddings for Module A's
    schema linking (needs the `vector` extension, created below).
  * Columns on `datasets` / `dataset_columns` / `dataset_schemas` carrying the
    inference output (semantic names, descriptions, Vega-Lite hints, keys).
  * A source reference on `conversations`, which makes `dataset_id` nullable —
    this deliberately supersedes the 2026-07-25 "a conversation is over exactly
    one dataset" decision.

Two Postgres notes, both deliberate:

1. `ALTER TYPE ... ADD VALUE` is used to extend the existing `file_type` and
   `dataset_status` enums. On PostgreSQL 12+ that is allowed inside a
   transaction as long as the new value is not *used* in the same transaction
   — which it isn't here (nothing below writes 'docx'/'json'/
   'awaiting_confirmation'). Do not add a data migration to this revision that
   inserts one of those values; put it in a follow-up revision instead.
2. `CREATE EXTENSION vector` requires an appropriately privileged role. The RDS
   master user (`alibi_admin`) has it; a future least-privilege app role would
   not, which is why the extension is created here rather than lazily at
   runtime.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_module_b"
down_revision = "0002_conversation_favorite"
branch_labels = None
depends_on = None

# Width of the stored embeddings. Must match ALIBI_EMBEDDING_DIMENSIONS; it is
# hard-coded here rather than read from settings because a migration has to
# describe what was actually applied, not what today's config happens to say.
EMBEDDING_DIM = 512

_UPLOAD_STATUS = postgresql.ENUM(
    "pending", "uploaded", "processing", "ready", "error", name="upload_status"
)
_UPLOAD_PHASE = postgresql.ENUM(
    "uploading", "extracting", "profiling", "inferring", "storing", "done", name="upload_phase"
)
_VEGA_LITE_TYPE = postgresql.ENUM(
    "quantitative", "nominal", "ordinal", "temporal", name="vega_lite_type"
)
_CONVERSATION_SOURCE_TYPE = postgresql.ENUM("dataset", "folder", name="conversation_source_type")


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- new enum types -------------------------------------------------
    _UPLOAD_STATUS.create(bind, checkfirst=True)
    _UPLOAD_PHASE.create(bind, checkfirst=True)
    _VEGA_LITE_TYPE.create(bind, checkfirst=True)
    _CONVERSATION_SOURCE_TYPE.create(bind, checkfirst=True)

    # ---- extend existing enums (see note 1 in the docstring) ------------
    op.execute("ALTER TYPE file_type ADD VALUE IF NOT EXISTS 'docx'")
    op.execute("ALTER TYPE file_type ADD VALUE IF NOT EXISTS 'json'")
    op.execute("ALTER TYPE dataset_status ADD VALUE IF NOT EXISTS 'awaiting_confirmation'")

    # ---- folders --------------------------------------------------------
    op.create_table(
        "folders",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="folders_owner_name_key"),
    )
    op.create_index("folders_owner_idx", "folders", ["owner_id"])
    op.execute(
        "CREATE TRIGGER folders_set_updated_at BEFORE UPDATE ON folders"
        " FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ---- uploads --------------------------------------------------------
    op.create_table(
        "uploads",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column(
            "file_type",
            postgresql.ENUM(name="file_type", create_type=False),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Key of the RAW object. Nulled once processing succeeds and the file
        # is deleted — the product rule is "keep the table, not the document".
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="upload_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("phase", postgresql.ENUM(name="upload_phase", create_type=False), nullable=True),
        sa.Column("detected_tables", sa.Integer(), nullable=True),
        sa.Column("folder_id", sa.Text(), sa.ForeignKey("folders.id", ondelete="SET NULL"), nullable=True),
        # Non-fatal notes for the confirmation screen ("encontramos 3 tablas",
        # "descartamos una fila de totales") — the pipeline's guesses are shown
        # to the user rather than applied silently.
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("uploads_owner_idx", "uploads", ["owner_id", "created_at"])
    op.execute(
        "CREATE TRIGGER uploads_set_updated_at BEFORE UPDATE ON uploads"
        " FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ---- datasets: link to the upload + the data-plane pointer -----------
    op.add_column("datasets", sa.Column("table_name", sa.Text(), nullable=True))
    op.add_column(
        "datasets",
        sa.Column("upload_id", sa.Text(), sa.ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("datasets", sa.Column("source_label", sa.Text(), nullable=True))
    op.add_column("datasets", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("datasets_upload_idx", "datasets", ["upload_id"])
    # A generated query names the table unqualified, so it has to be unique
    # per owner. Partial: rows predating Module B have no table_name.
    op.create_index(
        "datasets_owner_table_name_key",
        "datasets",
        ["owner_id", "table_name"],
        unique=True,
        postgresql_where=sa.text("table_name IS NOT NULL"),
    )

    # ---- folder_datasets: the mapping -----------------------------------
    op.create_table(
        "folder_datasets",
        sa.Column("folder_id", sa.Text(), sa.ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("dataset_id", sa.Text(), sa.ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("folder_datasets_dataset_idx", "folder_datasets", ["dataset_id"])

    # ---- inference output on the schema/columns -------------------------
    op.add_column("dataset_schemas", sa.Column("table_description", sa.Text(), nullable=True))
    op.add_column("dataset_columns", sa.Column("source_name", sa.Text(), nullable=True))
    # The column's name inside the Parquet file — fixed at ingestion, never
    # edited. Renaming a column is then a view projection rather than a file
    # rewrite, and survives being renamed more than once.
    op.add_column("dataset_columns", sa.Column("physical_name", sa.Text(), nullable=True))
    op.add_column("dataset_columns", sa.Column("sql_type", sa.Text(), nullable=True))
    op.add_column(
        "dataset_columns",
        sa.Column("vl_type", postgresql.ENUM(name="vega_lite_type", create_type=False), nullable=True),
    )
    op.add_column("dataset_columns", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "dataset_columns",
        sa.Column("is_primary_key", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ---- column embeddings (pgvector) -----------------------------------
    op.create_table(
        "dataset_column_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "dataset_id", sa.Text(), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("column_ordinal", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("dataset_id", "column_ordinal", name="dataset_column_embeddings_key"),
    )
    op.create_index("dataset_column_embeddings_dataset_idx", "dataset_column_embeddings", ["dataset_id"])
    # Cosine distance is what the retrieval side uses. Deliberately NOT an
    # ANN index (ivfflat/hnsw): at one vector per column the table is tiny, an
    # exact scan is faster than an approximate index, and ivfflat needs
    # training data that doesn't exist yet on a fresh install.

    # ---- conversations: dataset OR folder --------------------------------
    op.add_column(
        "conversations",
        sa.Column(
            "source_type",
            postgresql.ENUM(name="conversation_source_type", create_type=False),
            nullable=False,
            server_default="dataset",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("folder_id", sa.Text(), sa.ForeignKey("folders.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("conversations_folder_idx", "conversations", ["folder_id"])
    # Every existing row is dataset-scoped, so the default above is already
    # correct for them; only now can dataset_id be relaxed.
    op.alter_column("conversations", "dataset_id", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        "conversations_source_ck",
        "conversations",
        "(source_type = 'dataset' AND dataset_id IS NOT NULL AND folder_id IS NULL)"
        " OR (source_type = 'folder' AND folder_id IS NOT NULL AND dataset_id IS NULL)",
    )

    # ---- saved charts from a folder-scoped conversation ------------------
    op.add_column(
        "saved_charts",
        sa.Column("folder_id", sa.Text(), sa.ForeignKey("folders.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("saved_charts_folder_idx", "saved_charts", ["folder_id"])


def downgrade() -> None:
    """Reverses everything except the two `ALTER TYPE ADD VALUE` calls.

    PostgreSQL cannot remove a value from an enum type; undoing those would
    mean recreating `file_type` / `dataset_status` and rewriting every column
    that uses them. Leaving three unused labels behind is harmless, so the
    downgrade deliberately stops short of that rather than performing a risky
    type swap on tables holding real data.
    """
    bind = op.get_bind()

    op.drop_index("saved_charts_folder_idx", table_name="saved_charts")
    op.drop_column("saved_charts", "folder_id")

    op.drop_constraint("conversations_source_ck", "conversations", type_="check")
    # Folder-scoped conversations have no dataset_id and cannot survive the
    # NOT NULL that comes back; drop them rather than fail the migration.
    op.execute("DELETE FROM conversations WHERE dataset_id IS NULL")
    op.alter_column("conversations", "dataset_id", existing_type=sa.Text(), nullable=False)
    op.drop_index("conversations_folder_idx", table_name="conversations")
    op.drop_column("conversations", "folder_id")
    op.drop_column("conversations", "source_type")

    op.drop_index("dataset_column_embeddings_dataset_idx", table_name="dataset_column_embeddings")
    op.drop_table("dataset_column_embeddings")

    op.drop_column("dataset_columns", "is_primary_key")
    op.drop_column("dataset_columns", "description")
    op.drop_column("dataset_columns", "vl_type")
    op.drop_column("dataset_columns", "sql_type")
    op.drop_column("dataset_columns", "physical_name")
    op.drop_column("dataset_columns", "source_name")
    op.drop_column("dataset_schemas", "table_description")

    op.drop_index("folder_datasets_dataset_idx", table_name="folder_datasets")
    op.drop_table("folder_datasets")

    op.drop_index("datasets_owner_table_name_key", table_name="datasets")
    op.drop_index("datasets_upload_idx", table_name="datasets")
    op.drop_column("datasets", "confirmed_at")
    op.drop_column("datasets", "source_label")
    op.drop_column("datasets", "upload_id")
    op.drop_column("datasets", "table_name")

    op.execute("DROP TRIGGER IF EXISTS uploads_set_updated_at ON uploads")
    op.drop_index("uploads_owner_idx", table_name="uploads")
    op.drop_table("uploads")

    op.execute("DROP TRIGGER IF EXISTS folders_set_updated_at ON folders")
    op.drop_index("folders_owner_idx", table_name="folders")
    op.drop_table("folders")

    _CONVERSATION_SOURCE_TYPE.drop(bind, checkfirst=True)
    _VEGA_LITE_TYPE.drop(bind, checkfirst=True)
    _UPLOAD_PHASE.drop(bind, checkfirst=True)
    _UPLOAD_STATUS.drop(bind, checkfirst=True)
    # The `vector` extension is left in place: other things may come to depend
    # on it, and dropping an extension is not the business of a table-level
    # migration's downgrade.
