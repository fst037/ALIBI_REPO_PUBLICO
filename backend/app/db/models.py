"""SQLModel table models for the control-plane database.

Mirrors `backend/db/migrations/0001_initial_schema.sql` +
`0002_conversation_favorite.sql` 1:1 — table names, columns, FKs, defaults and
indexes match exactly. This file (its `SQLModel.metadata`) is the source of
truth for schema going forward; `backend/alembic/` generates migrations from
it. The two `.sql` files stay as the historical baseline (see
`backend/db/README.md`).

Scope: the control-plane tables. The *contents* of ingested files are NOT here
— they live as Parquet in the object store and are queried with DuckDB (the
two-data-planes design). What this file models
for an uploaded file is its metadata, its inferred schema, and the S3 pointer
(`datasets.storage_key`). There is deliberately no dynamic per-tenant DDL:
every table here is known at migration time, which is what lets Alembic stay
the single source of schema truth.

Enums reuse the Python enum already defined in `app/schemas/*.py` where one
exists (same taxonomy, not redefined); a few DB enums have no schema-side
equivalent (Pydantic uses a bare `Literal[...]` for them instead), so those
are defined locally below.

JSONB blob columns (`messages.content`, `saved_charts.result`, `plans.features`
/`limits`) are typed as plain `dict`/`list` here — the existing discriminated-
union validation (e.g. `TypeAdapter[Message]` in `app/repositories/
conversations.py`) stays an explicit step in the repository/service layer,
not something the ORM column type needs to express.

`updated_at` columns are kept current by DB-side triggers (`set_updated_at`,
declared in the Alembic baseline) — nothing to do on the Python side.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.config import get_settings
from app.schemas.charts import ChartType
from app.schemas.datasets import (
    ColumnType,
    DatasetFileType,
    DatasetStatus,
    InferenceConfidence,
    VegaLiteType,
)
from app.schemas.plans import BillingCycle, PlanId
from app.schemas.uploads import UploadPhase, UploadStatus

# ---------------------------------------------------------------------------
# Enums with no existing Pydantic equivalent (schemas use a bare Literal[...]
# for these) — defined once here for the ORM columns.
# ---------------------------------------------------------------------------


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    guard = "guard"
    system = "system"


class InvoiceStatus(str, Enum):
    paid = "paid"
    pending = "pending"
    failed = "failed"


class LedgerAction(str, Enum):
    query = "query"
    visualization = "visualization"


class NotificationTone(str, Enum):
    ok = "ok"
    warn = "warn"
    err = "err"
    info = "info"


class ConversationSourceType(str, Enum):
    """What a conversation runs over. `folder` supersedes the original
    one-dataset-per-conversation rule).

    `datasets` (added 0005) is an explicit multi-selection: several files that
    are not a folder. Its members live in `conversation_datasets`, since neither
    `dataset_id` nor `folder_id` can hold a list."""

    dataset = "dataset"
    datasets = "datasets"
    folder = "folder"


def _pg_enum(enum_cls: type[Enum], name: str) -> PGEnum:
    """A Postgres-native ENUM column matching one already CREATEd by the
    baseline migration (Alembic must not try to (re)create it).

    `values_callable` is not optional: SQLAlchemy's default is to bind a Python
    enum member's *name* rather than its `.value`, and the Postgres type was
    CREATEd with the value strings (`'Número entero'`, `'alta'`, ...). Most
    members here have name == value (`csv`, `pending`, `barras`...) so the bug
    is invisible — until a member like `ColumnType.WHOLE_NUMBER = "Número
    entero"` or `InferenceConfidence.HIGH = "alta"` hits the database and
    Postgres rejects the member's name as an unknown enum label.
    """
    return PGEnum(
        enum_cls, name=name, create_type=False, values_callable=lambda e: [m.value for m in e]
    )


# ---------------------------------------------------------------------------
# Plans (reference/config)
# ---------------------------------------------------------------------------


class Plan(SQLModel, table=True):
    __tablename__ = "plans"

    id: PlanId = Field(sa_column=Column(_pg_enum(PlanId, "plan_id"), primary_key=True))
    name: str
    #: Currency the user is charged in. The prices below are its USD reference
    #: values; the peso price is computed at the official rate (0011).
    currency: str = "ARS"
    price_monthly_usd: Decimal
    price_annual_usd: Decimal
    #: Price of one query beyond the monthly quota; NULL = no overage.
    overage_price_usd: Decimal | None = None
    recommended: bool = False
    features: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    limits: dict = Field(sa_column=Column(JSONB, nullable=False))


# ---------------------------------------------------------------------------
# Identity / account
# ---------------------------------------------------------------------------


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(unique=True)
    name: str
    workspace: str | None = None
    password_hash: str | None = None
    email_verified: bool = False
    #: The Google account this user signs in with — the ID token's `sub` (0014).
    #: NULL when they never used "Continuar con Google".
    google_sub: str | None = Field(default=None, unique=True, index=True)
    two_factor_enabled: bool = False
    theme: str | None = None
    product_emails: bool = True
    #: Version of the Terms and Privacy Policy the user accepted, and when —
    #: the record of consent Ley 25.326 asks for (0011). NULL on accounts
    #: created before acceptance was recorded.
    terms_version: str | None = None
    terms_accepted_at: datetime | None = None
    plan_id: PlanId = Field(
        default=PlanId.free,
        sa_column=Column(_pg_enum(PlanId, "plan_id"), ForeignKey("plans.id"), nullable=False),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Subscription(SQLModel, table=True):
    __tablename__ = "subscriptions"

    user_id: str = Field(primary_key=True, foreign_key="users.id")
    plan_id: PlanId = Field(sa_column=Column(_pg_enum(PlanId, "plan_id"), ForeignKey("plans.id"), nullable=False))
    cycle: BillingCycle = Field(
        default=BillingCycle.monthly, sa_column=Column(_pg_enum(BillingCycle, "billing_cycle"), nullable=False)
    )
    payment_brand: str | None = None
    payment_last4: str | None = None
    payment_expiry: str | None = None
    #: Pay-per-query beyond the plan's quota (0010). Off until the user turns it
    #: on; `overage_cap_ars` is the most they will be billed for it in a month
    #: (NULL = no cap).
    overage_enabled: bool = False
    overage_cap_ars: Decimal | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InvoiceModel(SQLModel, table=True):
    __tablename__ = "invoices"
    __table_args__ = (Index("invoices_user_idx", "user_id", "issued_on"),)

    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="users.id")
    issued_on: date
    amount: Decimal
    status: InvoiceStatus = Field(sa_column=Column(_pg_enum(InvoiceStatus, "invoice_status"), nullable=False))


# ---------------------------------------------------------------------------
# Ingestion (Module B) — uploads, datasets, folders.
#
# METADATA only: no row of any user's uploaded file is stored here. An upload
# can produce SEVERAL datasets (a sheet holding three tables, a PDF with tables
# on different pages), which is why `uploads` is its own entity and `datasets`
# points back at it rather than the other way round.
# ---------------------------------------------------------------------------


class Upload(SQLModel, table=True):
    __tablename__ = "uploads"
    __table_args__ = (Index("uploads_owner_idx", "owner_id", "created_at"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id")
    original_filename: str
    file_type: DatasetFileType = Field(sa_column=Column(_pg_enum(DatasetFileType, "file_type"), nullable=False))
    size_bytes: int
    # Key of the RAW file. Nulled out once processing succeeds and the object
    # is deleted — the product rule is that we keep the table, not the
    # document. On failure the object survives 7 days (S3 lifecycle) so the
    # upload can be retried, and this stays populated for exactly that long.
    storage_key: str | None = None
    status: UploadStatus = Field(
        default=UploadStatus.pending,
        sa_column=Column(_pg_enum(UploadStatus, "upload_status"), nullable=False),
    )
    phase: UploadPhase | None = Field(
        default=None, sa_column=Column(_pg_enum(UploadPhase, "upload_phase"), nullable=True)
    )
    detected_tables: int | None = None
    # Folder the resulting datasets get attached to on confirmation, when the
    # user uploaded from inside a folder view.
    folder_id: str | None = Field(default=None, foreign_key="folders.id")
    # Non-fatal notes for the confirmation screen; see `Upload.warnings` in
    # app/schemas/uploads.py for what goes in here.
    warnings: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DatasetModel(SQLModel, table=True):
    __tablename__ = "datasets"

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    file_type: DatasetFileType = Field(sa_column=Column(_pg_enum(DatasetFileType, "file_type"), nullable=False))
    meta: str | None = None
    favorite: bool = False
    recent: bool = False
    status: DatasetStatus = Field(
        default=DatasetStatus.processing,
        sa_column=Column(_pg_enum(DatasetStatus, "dataset_status"), nullable=False),
    )
    row_count: int | None = None
    page_count: int | None = None
    column_count: int | None = None
    size_label: str | None = None
    error: str | None = None
    # Pointer into the DATA plane: the Parquet object holding the rows.
    storage_key: str | None = None
    # The SQL identifier this table is exposed under to Modules A and C.
    # Unique per owner so a generated query can name it without qualification.
    table_name: str | None = None
    upload_id: str | None = Field(default=None, foreign_key="uploads.id", index=True)
    # Provenance inside the source file ("Sheet “Ventas” · table 2").
    source_label: str | None = None
    confirmed_at: datetime | None = None
    # ETLite's last confirmed recipe (`CleaningRecipe`), re-offered when a new
    # file updates this dataset. None: nothing was chosen.
    cleaning_recipe: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DatasetSchemaModel(SQLModel, table=True):
    __tablename__ = "dataset_schemas"

    dataset_id: str = Field(primary_key=True, foreign_key="datasets.id")
    row_count: int = 0
    confidence: InferenceConfidence = Field(
        default=InferenceConfidence.MEDIUM,
        sa_column=Column(_pg_enum(InferenceConfidence, "inference_confidence"), nullable=False),
    )
    confirmed: bool = False
    # One-sentence summary of what the table represents (semantic inference).
    table_description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DatasetColumn(SQLModel, table=True):
    __tablename__ = "dataset_columns"
    __table_args__ = (UniqueConstraint("dataset_id", "ordinal"),)

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: str = Field(foreign_key="datasets.id", index=True)
    ordinal: int
    # Effective name: what the user sees and what SQL uses. May differ from
    # `source_name` when semantic inference or the user renamed it.
    name: str
    # Raw header exactly as it appeared in the file. Kept so a user can still
    # recognise a column that was renamed `fch_vcto` → `fecha_vencimiento`.
    source_name: str | None = None
    # The column's name INSIDE the Parquet file. Written once at ingestion and
    # never edited, which is what lets a rename be a view projection instead of
    # a file rewrite — and lets a column be renamed twice without the mapping
    # losing track of what it points at. Not exposed in the contract: it is an
    # implementation detail of the data plane, not something a client sets.
    physical_name: str | None = None
    type: ColumnType = Field(sa_column=Column(_pg_enum(ColumnType, "column_type"), nullable=False))
    # The physical type in the Parquet file (the projection DuckDB casts to).
    # Distinct from `type`, which is the user-facing label the UI edits.
    sql_type: str | None = None
    vl_type: VegaLiteType | None = Field(
        default=None, sa_column=Column(_pg_enum(VegaLiteType, "vega_lite_type"), nullable=True)
    )
    description: str | None = None
    is_primary_key: bool = False
    sample_values: str | None = None
    included: bool = True
    confidence: InferenceConfidence = Field(
        default=InferenceConfidence.MEDIUM,
        sa_column=Column(_pg_enum(InferenceConfidence, "inference_confidence"), nullable=False),
    )


class DatasetColumnEmbedding(SQLModel, table=True):
    """Per-column embedding for Module A's semantic schema linking.

    Keyed by column rather than by row: the volume is tiny (one vector per
    column, not per record), which is exactly why `pgvector` in the existing
    database beat standing up a separate vector store.
    """

    __tablename__ = "dataset_column_embeddings"
    __table_args__ = (UniqueConstraint("dataset_id", "column_ordinal"),)

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: str = Field(foreign_key="datasets.id", index=True)
    column_ordinal: int
    # Width must match ALIBI_EMBEDDING_DIMENSIONS; changing it is a migration.
    embedding: list[float] = Field(
        sa_column=Column(Vector(get_settings().embedding_dimensions), nullable=False)
    )
    model_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Folder(SQLModel, table=True):
    """A one-level "project" grouping datasets.

    There is deliberately no `parent_id`: "no subfolders" is enforced by the
    shape of the table, not by a rule someone has to remember.
    """

    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FolderDataset(SQLModel, table=True):
    """The folder↔dataset mapping. Many-to-many, and *only* a mapping:
    attaching a dataset to a second folder creates a row here, never a copy of
    the data; deleting a folder deletes rows here, never datasets."""

    __tablename__ = "folder_datasets"

    folder_id: str = Field(primary_key=True, foreign_key="folders.id")
    dataset_id: str = Field(primary_key=True, foreign_key="datasets.id", index=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Chat (conversations + polymorphic messages)
# ---------------------------------------------------------------------------


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("conversations_owner_idx", "owner_id", "created_at"),
        # Exactly one source must be set, matching `source_type` — except
        # `datasets`, whose members are rows in `conversation_datasets` because
        # a single FK column cannot hold a list.
        # Kept byte-compatible with migration 0005, which expresses the third
        # branch by exclusion so it never names a just-added enum label inside
        # the transaction that added it — see that revision's docstring.
        CheckConstraint(
            "(source_type = 'dataset' AND dataset_id IS NOT NULL AND folder_id IS NULL)"
            " OR (source_type = 'folder' AND folder_id IS NOT NULL AND dataset_id IS NULL)"
            " OR (source_type NOT IN ('dataset', 'folder')"
            " AND dataset_id IS NULL AND folder_id IS NULL)",
            name="conversations_source_ck",
        ),
        CheckConstraint("answer_mode IN ('quick', 'deep')", name="conversations_answer_mode_ck"),
    )

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id")
    source_type: ConversationSourceType = Field(
        default=ConversationSourceType.dataset,
        sa_column=Column(_pg_enum(ConversationSourceType, "conversation_source_type"), nullable=False),
    )
    # Nullable since 0003: a folder-sourced conversation has no single dataset.
    dataset_id: str | None = Field(default=None, foreign_key="datasets.id", index=True)
    folder_id: str | None = Field(default=None, foreign_key="folders.id", index=True)
    title: str
    favorite: bool = False
    # Quick or Deep, the default for the conversation's next questions.
    # Text with a CHECK (migration 0009), not a Postgres enum: a third mode would
    # otherwise need the ALTER TYPE dance migration 0005 documents.
    answer_mode: str = Field(
        default="quick",
        sa_column=Column(String(16), nullable=False, server_default="quick"),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationDataset(SQLModel, table=True):
    """The datasets of a `source_type='datasets'` conversation, in order.

    Only a mapping, exactly like `folder_datasets`. `position` is kept because
    the order decides how the tables are laid out in Module A's prompt, and a
    prompt that reshuffles between two reads of the same conversation would make
    a generation impossible to reproduce.

    A `dataset`/`folder` conversation has no rows here: the first names its one
    table in `dataset_id`, the second resolves its list from `folder_datasets`
    on every read, which is what lets a later addition join an open chat.
    """

    __tablename__ = "conversation_datasets"

    conversation_id: str = Field(primary_key=True, foreign_key="conversations.id")
    dataset_id: str = Field(primary_key=True, foreign_key="datasets.id", index=True)
    position: int = 0


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "position"),)

    id: str = Field(primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id")
    position: int
    role: MessageRole = Field(sa_column=Column(_pg_enum(MessageRole, "message_role"), nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content: dict = Field(sa_column=Column(JSONB, nullable=False))


class ConversationRun(SQLModel, table=True):
    """One asynchronous answer run (`app/schemas/runs.Run`), stored whole.

    `content` is the run as the API returns it — steps, final message, guard or
    error — because it is read and written as a unit and polled often; the
    columns beside it are only what a query filters or orders by. CASCADE on the
    conversation: a run has no meaning without the conversation it answers in.
    """

    __tablename__ = "conversation_runs"
    __table_args__ = (Index("conversation_runs_conversation_idx", "conversation_id", "created_at"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id")
    conversation_id: str = Field(
        sa_column=Column(
            ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
        )
    )
    status: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    content: dict = Field(sa_column=Column(JSONB, nullable=False))


# ---------------------------------------------------------------------------
# Saved artifacts (dashboards + library domains)
# ---------------------------------------------------------------------------


class SavedChart(SQLModel, table=True):
    __tablename__ = "saved_charts"
    __table_args__ = (Index("saved_charts_owner_idx", "owner_id", "saved_at"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id")
    dataset_id: str | None = Field(default=None, foreign_key="datasets.id", index=True)
    # Set instead of dataset_id when the chart came from a folder-scoped
    # conversation (which may have joined across several datasets).
    folder_id: str | None = Field(default=None, foreign_key="folders.id", index=True)
    dataset_name: str
    question: str
    chart_type: ChartType = Field(sa_column=Column(_pg_enum(ChartType, "chart_type"), nullable=False))
    saved_at: datetime = Field(default_factory=datetime.utcnow)
    result: dict = Field(sa_column=Column(JSONB, nullable=False))


class Dashboard(SQLModel, table=True):
    """A named board of charts over exactly one folder.

    Scoped to a folder for the same reason a conversation is (see
    `Conversation`): the folder is the only unit resolved on every read, so a
    file added to it later is visible to every chart on the board without
    anything being re-pinned. A board that named datasets directly would freeze
    its scope the day it was created.

    This replaced the implicit per-dataset dashboard — the old
    `/dashboards/:datasetId` gallery, which was every pinned chart for one
    dataset in save order, with no name, no layout and no way to have two.
    `saved_charts` survives as the *pool* those boards are filled from.
    """

    __tablename__ = "dashboards"
    __table_args__ = (Index("dashboards_owner_idx", "owner_id", "updated_at"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    folder_id: str = Field(foreign_key="folders.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DashboardChart(SQLModel, table=True):
    """One chart on a board: the *idea* of a chart, never its values.

    `sql` + `spec` and no rows, deliberately. Opening a dashboard re-runs the
    SQL against the folder's Parquet as it is *now* and renders the stored
    Vega-Lite spec over the fresh result, so a board reflects the data rather
    than the day it was built. That works only because a Module C spec carries
    no `data` — see `app/schemas/charts.ChartSpec`, where dropping the rows was
    already the rule for a different reason.

    Nothing here references `saved_charts`. Adding a pinned chart **copies** its
    idea onto the board: a board must not break because someone tidied up the
    pin it came from, and the two drift apart the moment either is edited.

    There is no narrative. `naturalResponse` describes the numbers as they were
    when the question was asked, so on a board that recomputes it would be a
    confident sentence about last week's data sitting under this week's chart.
    """

    __tablename__ = "dashboard_charts"
    __table_args__ = (Index("dashboard_charts_board_idx", "dashboard_id"),)

    id: str = Field(primary_key=True)
    dashboard_id: str = Field(
        sa_column=Column(
            ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    #: Heading on the card. Seeded from the spec's own title, then editable.
    title: str
    #: The question that produced it, kept for provenance — shown in the detail
    #: modal, never re-asked.
    question: str = ""
    #: Re-run on every open. Re-validated by the read-only guardrail first: it
    #: was safe when it was saved, and the tables it names may have changed.
    sql: str
    chart_type: ChartType = Field(sa_column=Column(_pg_enum(ChartType, "chart_type"), nullable=False))
    #: The Vega-Lite v5 document, without `data`.
    spec: dict = Field(sa_column=Column(JSONB, nullable=False))
    #: Grid position, in react-grid-layout's `GRID_COLUMNS`-column space (see
    #: `app.schemas.dashboards`; 24 as of 2026-08-08, up from an original 12).
    #: These class defaults are never actually read — `repo.add_chart` always
    #: passes `x`/`y`/`w`/`h` explicitly — but are kept in sync for anyone
    #: reading this model on its own.
    x: int = 0
    y: int = 0
    w: int = 8
    h: int = 4
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SavedQuery(SQLModel, table=True):
    __tablename__ = "saved_queries"
    __table_args__ = (Index("saved_queries_owner_idx", "owner_id", "saved_at"),)

    id: str = Field(primary_key=True)
    owner_id: str = Field(foreign_key="users.id")
    question: str
    dataset_id: str | None = Field(default=None, foreign_key="datasets.id")
    dataset_name: str
    chart_type: ChartType = Field(sa_column=Column(_pg_enum(ChartType, "chart_type"), nullable=False))
    saved_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = (Index("notifications_user_idx", "user_id", "read", "created_at"),)

    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="users.id")
    tone: NotificationTone = Field(sa_column=Column(_pg_enum(NotificationTone, "notification_tone"), nullable=False))
    title: str
    body: str
    action_label: str | None = None
    action_href: str | None = None
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Cost ledger
# ---------------------------------------------------------------------------


class CostLedgerEntryModel(SQLModel, table=True):
    __tablename__ = "cost_ledger"
    __table_args__ = (Index("cost_ledger_user_idx", "user_id", "occurred_at"),)

    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="users.id")
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    action_type: LedgerAction = Field(sa_column=Column(_pg_enum(LedgerAction, "ledger_action"), nullable=False))
    dataset_id: str | None = Field(default=None, foreign_key="datasets.id")
    dataset_name: str | None = None
    rows_returned: int = 0
    cost_usd: Decimal = Decimal("0")
    cost_ars: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Auth support (optional, per the 0001 migration's own note)
# ---------------------------------------------------------------------------


class VerificationCode(SQLModel, table=True):
    __tablename__ = "verification_codes"
    __table_args__ = (
        CheckConstraint("purpose IN ('email_verify', 'two_factor')"),
        Index("verification_codes_lookup_idx", "email", "purpose"),
    )

    id: str = Field(primary_key=True)
    user_id: str | None = Field(default=None, foreign_key="users.id")
    email: str
    purpose: str
    #: SHA-256 of the 6-digit code (`app.core.security.hash_code`), never the code.
    code: str
    expires_at: datetime
    #: Wrong guesses so far; the code is burnt at `CODE_MAX_ATTEMPTS` (migration 0003).
    attempts: int = 0
    consumed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuthSessionModel(SQLModel, table=True):
    __tablename__ = "auth_sessions"

    token: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: NULL = no expiry: the session ends only at logout ("Keep me signed in").
    expires_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
