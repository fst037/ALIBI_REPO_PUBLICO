"""Pydantic models for the `datasets` (ingestion) domain.

These conform to `contracts/ingestion.yaml` — the hand-authored spec is the
source of truth, and the frontend's generated client was built from it, so the
JSON these models emit must match field-for-field. Field names are kept in
camelCase (matching the contract and `frontend/src/types/index.ts`) so the wire
shape lines up with the generated TypeScript client with no alias machinery.

Module B added the upload/folder surface and the
richer per-column metadata; `uploads.py` and `folders.py` hold those, and this
module keeps the dataset/schema shapes they both reference.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DatasetFileType(str, Enum):
    csv = "csv"
    xlsx = "xlsx"
    pdf = "pdf"
    docx = "docx"
    json = "json"


class DatasetStatus(str, Enum):
    processed = "processed"
    processing = "processing"
    # The table exists and is previewable, but the user hasn't confirmed the
    # inferred schema yet, so it isn't queryable (Ingesta 1.2).
    awaiting_confirmation = "awaiting_confirmation"
    error = "error"


class ColumnType(str, Enum):
    TEXT = "Text"
    NUMBER = "Number"
    WHOLE_NUMBER = "Whole number"
    DATE = "Date"
    PERCENTAGE = "Percentage"
    BOOLEAN = "Boolean"


class VegaLiteType(str, Enum):
    """Type hint Module C consumes when choosing an encoding."""

    quantitative = "quantitative"
    nominal = "nominal"
    ordinal = "ordinal"
    temporal = "temporal"


class InferenceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Dataset(BaseModel):
    id: str
    name: str
    fileType: DatasetFileType
    meta: str
    favorite: bool
    recent: bool
    status: DatasetStatus
    createdAt: str
    updatedAt: str
    # The SQL identifier Modules A and C put in the FROM clause.
    tableName: str | None = None
    # Which upload produced it; several datasets can share one.
    uploadId: str | None = None
    # Where inside the file it came from ("Sheet “Ventas” · table 2"), so a
    # multi-table upload is unambiguous in the confirmation screen.
    sourceLabel: str | None = None
    folderIds: list[str] = Field(default_factory=list)
    # Optional (tabular vs PDF, and the error message when status == error).
    rows: int | None = None
    pages: int | None = None
    columns: int | None = None
    sizeLabel: str | None = None
    error: str | None = None


class NullStrategy(str, Enum):
    """What ETLite does with a column's empty cells."""

    keep = "keep"
    # Drop every row where this column is empty.
    drop_row = "drop_row"
    zero = "zero"
    mean = "mean"
    median = "median"
    # The most frequent value.
    mode = "mode"
    # A value the user typed (`ColumnCleaning.fillValue`).
    value = "value"


class TextCase(str, Enum):
    keep = "keep"
    upper = "upper"
    lower = "lower"


class ColumnCleaning(BaseModel):
    """ETLite's per-column recipe, applied to the stored table on confirmation.

    Values are normalised first (trim → case → placeholders → rounding) and the
    null strategy runs on the result, so a placeholder turned empty can be filled.

    Ingestion already strips the ends of every cell and reads the usual
    placeholders (`-`, `N/A`, `s/d`, …) as empty, so these cover what it cannot
    guess: inner runs of spaces, casing, and placeholders particular to a file.
    """

    nulls: NullStrategy = NullStrategy.keep
    # For `value`, what the user typed. For the others the server resolves the
    # literal itself (shown back in `CleaningPreview.fills`); a sent one is ignored.
    fillValue: str | None = None
    # Strip both ends and collapse inner runs of whitespace. Text columns only.
    trim: bool = False
    case: TextCase = TextCase.keep
    # Values that mean "no data" in this file ("desconocido", "?"), compared
    # ignoring case. Text columns only.
    nullTokens: list[str] = Field(default_factory=list, max_length=20)
    # Round to this many decimals. Decimal-number columns only.
    decimals: int | None = Field(default=None, ge=0, le=6)

    def is_noop(self) -> bool:
        return (
            self.nulls is NullStrategy.keep
            and not self.trim
            and self.case is TextCase.keep
            and not any(t.strip() for t in self.nullTokens)
            and self.decimals is None
        )


class DuplicateStrategy(str, Enum):
    keep = "keep"
    # Rows identical in every included column.
    exact = "exact"
    # Rows that share the values of `TableCleaning.duplicateColumns`.
    by_columns = "by_columns"


class TableCleaning(BaseModel):
    """ETLite's table-level recipe. The first occurrence of a duplicate is kept."""

    duplicates: DuplicateStrategy = DuplicateStrategy.keep
    # Ordinals into the schema's column list, not names: a later rename must
    # not change which columns identify a duplicate.
    duplicateColumns: list[int] = Field(default_factory=list)

    def is_noop(self) -> bool:
        return self.duplicates is DuplicateStrategy.keep


class SchemaColumn(BaseModel):
    name: str
    type: ColumnType
    sampleValues: str
    included: bool
    confidence: InferenceConfidence
    # Raw header as it appeared in the file. Kept so the user still recognises
    # a column that semantic inference renamed (`fch_vcto` →
    # `fecha_vencimiento`); None when the name was never changed.
    sourceName: str | None = None
    vlType: VegaLiteType | None = None
    description: str | None = None
    isPrimaryKey: bool = False
    # ETLite, request only: read by the confirm and the preview, applied to the
    # data, never stored — a schema read back always has None here.
    cleaning: ColumnCleaning | None = None


class DatasetSchema(BaseModel):
    datasetId: str
    # Rows in the stored table — after ETLite's cleaning, once confirmed with one.
    rowCount: int
    confidence: InferenceConfidence
    columns: list[SchemaColumn]
    tableDescription: str | None = None
    confirmed: bool = False


class PreviewColumn(BaseModel):
    key: str
    label: str
    # `num` (not "right") because that is what `charts.ResultColumn` uses and
    # what the frontend's DataTable understands — the two must agree or the
    # preview needs its own adapter. Typed as a Literal so a wrong value is a
    # validation error here rather than a silently unstyled column there.
    align: Literal["left", "num"] = "left"


class TablePreview(BaseModel):
    """First N rows, read through DuckDB — used by the confirmation screen and
    the Biblioteca drawer.

    Deliberately the same shape as `charts.ResultTable`, so the frontend's
    `DataTable` renders an ingestion preview and a query result with the same
    component and no adapter.
    """

    datasetId: str
    columns: list[PreviewColumn]
    rows: list[dict[str, str | float | None]]
    truncated: bool = False


class UpdateDatasetRequest(BaseModel):
    """Partial patch — at least one of `name` or `favorite` (per the contract)."""

    name: str | None = Field(default=None, min_length=1)
    favorite: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "UpdateDatasetRequest":
        if self.name is None and self.favorite is None:
            raise ValueError("provide at least one of 'name' or 'favorite'")
        return self


class ConfirmSchemaRequest(BaseModel):
    columns: list[SchemaColumn]
    # The UI lets the user rename on confirm — which matters most when one
    # upload produced several tables all named after the same file.
    name: str | None = Field(default=None, min_length=1)
    cleaning: TableCleaning | None = None


# ---------------------------------------------------------------------------
# ETLite — light cleaning chosen at confirmation
# ---------------------------------------------------------------------------

ColumnKind = Literal["integer", "number", "text", "date", "boolean", "other"]


class ColumnQuality(BaseModel):
    """What ETLite found in one column of the stored table, before any cleaning."""

    ordinal: int
    # The physical type, which is what decides which operations apply — the
    # user-facing `SchemaColumn.type` label can be edited, the file cannot.
    kind: ColumnKind
    nullCount: int
    # Text only: cells that trimming would change (stray or repeated spaces).
    whitespaceCount: int = 0


class DatasetQuality(BaseModel):
    datasetId: str
    rowCount: int
    # Rows identical to an earlier row in every column.
    duplicateRows: int
    columns: list[ColumnQuality]


class CleaningPreviewRequest(BaseModel):
    """The schema as currently edited, cleaning included — nothing is saved."""

    columns: list[SchemaColumn]
    cleaning: TableCleaning | None = None


class ResolvedFill(BaseModel):
    ordinal: int
    # The literal the empty cells get, e.g. the mean the server computed.
    value: str


class CleaningPreview(BaseModel):
    rowCount: int
    # Rows dropped by `drop_row` and by duplicate removal together.
    removedRows: int
    fills: list[ResolvedFill] = Field(default_factory=list)
    preview: TablePreview


# ---------------------------------------------------------------------------
# Updating a dataset with a new file (append / replace)
# ---------------------------------------------------------------------------


class UpdateMode(str, Enum):
    """How an incoming file combines with the dataset it updates."""

    # The new rows go after the existing ones (next month's report).
    append = "append"
    # The new rows are the dataset (the same months, corrected or re-exported).
    replace = "replace"


class OverlapStrategy(str, Enum):
    """On append: what to do with incoming rows already in the dataset, cell for cell."""

    keep_all = "keep_all"
    skip = "skip"


class ColumnRecipe(BaseModel):
    ordinal: int
    cleaning: ColumnCleaning


class CleaningRecipe(BaseModel):
    """ETLite's recipe as stored on a dataset, so the next file can be cleaned the same way.

    Addressed by ordinal like everywhere in ETLite: an update requires the same
    columns, so an ordinal means the same column in every file.
    """

    table: TableCleaning | None = None
    columns: list[ColumnRecipe] = Field(default_factory=list)

    def is_noop(self) -> bool:
        return (self.table is None or self.table.is_noop()) and all(
            c.cleaning.is_noop() for c in self.columns
        )


class StructureDifference(BaseModel):
    """One way the incoming table is not the same structure as the dataset."""

    kind: Literal["missing", "extra", "type", "ambiguous"]
    column: str
    # For `type`: what the dataset holds and what the file has.
    expected: str | None = None
    found: str | None = None


class UpdateTarget(BaseModel):
    """A dataset an incoming table could update, and whether it fits."""

    datasetId: str
    name: str
    rows: int | None = None
    compatible: bool
    differences: list[StructureDifference] = Field(default_factory=list)


class DatasetUpdateRequest(BaseModel):
    # The unconfirmed dataset the upload produced; consumed by the update.
    stagedDatasetId: str
    mode: UpdateMode
    onOverlap: OverlapStrategy = OverlapStrategy.keep_all
    # The recipe to apply to the combined table; stored on the dataset afterwards.
    # None means "no cleaning" (and clears a stored one).
    cleaning: CleaningRecipe | None = None


class DatasetUpdatePreview(BaseModel):
    compatible: bool
    differences: list[StructureDifference] = Field(default_factory=list)
    existingRows: int = 0
    incomingRows: int = 0
    # Append only: incoming rows identical to a stored row in every column.
    overlapRows: int = 0
    # Rows the dataset would have, after the overlap choice and the recipe.
    resultRows: int = 0
    # What was stored at the last confirmation / update, to prefill the editor.
    storedCleaning: CleaningRecipe | None = None
    fills: list[ResolvedFill] = Field(default_factory=list)
