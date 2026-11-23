"""Internal data structures of the ingestion pipeline.

These are *not* wire models — `app/schemas/` holds those. Keeping them
separate means the pipeline can carry things the API never exposes (the raw
cell grid, per-step diagnostics, the parsed pandas frame) without leaking them
into the contract.

The flow is a chain of increasingly-refined shapes:

    bytes → RawTable (a grid of strings)
          → ShapedTable (header identified, junk removed)
          → TypedTable (types inferred, values coerced)
          → ProfiledTable (+ statistics and key candidates)
          → DetectedTable (+ names, ready to persist)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.schemas.datasets import (
    ColumnType,
    DatasetFileType,
    DatasetSchema,
    InferenceConfidence,
    VegaLiteType,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


# A cell as read from the source: text or absent. Everything is a string at
# this stage on purpose — deciding that "1.234,56" is a number is `typing_rules`'
# job, and doing it earlier is how you silently lose leading zeros.
Cell = str | None
Grid = list[list[Cell]]


@dataclass(slots=True)
class TableOrigin:
    """Where in the source file a table came from.

    Drives `Dataset.source_label`, which is what makes a three-table upload
    comprehensible in the confirmation screen.
    """

    sheet: str | None = None
    page: int | None = None
    # Index of this table within its sheet/page, 0-based.
    index: int = 0

    def label(self) -> str:
        if self.sheet is not None:
            base = f"Hoja “{self.sheet}”"
            return base if self.index == 0 else f"{base} · tabla {self.index + 1}"
        if self.page is not None:
            base = f"Página {self.page}"
            return base if self.index == 0 else f"{base} · tabla {self.index + 1}"
        return "Tabla" if self.index == 0 else f"Tabla {self.index + 1}"


@dataclass(slots=True)
class RawTable:
    """A rectangular block of cells, before any interpretation."""

    grid: Grid
    origin: TableOrigin = field(default_factory=TableOrigin)


@dataclass(slots=True)
class ShapedTable:
    """Header identified, preamble/footer/blank rows removed."""

    header: list[str]
    rows: Grid
    origin: TableOrigin
    header_confidence: InferenceConfidence = InferenceConfidence.HIGH
    # Human-readable record of what was thrown away or synthesised, surfaced
    # in the confirmation screen so the user can catch a bad guess.
    notes: list[str] = field(default_factory=list)
    # True when no header row could be found and names were generated.
    header_synthesised: bool = False


@dataclass(slots=True)
class ColumnProfile:
    """Per-column statistics — the input the semantic-inference model sees.

    Only this profile (never the full table) is sent to a model, which is what
    keeps the LLM step cheap and keeps user data out of a third-party prompt.
    """

    name: str
    source_name: str
    ordinal: int
    column_type: ColumnType
    sql_type: str
    vl_type: VegaLiteType
    confidence: InferenceConfidence
    # Filled in by semantic inference when a provider is configured; stays
    # None on the deterministic path.
    description: str | None = None
    n_rows: int = 0
    n_unique: int = 0
    n_null: int = 0
    samples: list[str] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    is_primary_key_candidate: bool = False

    @property
    def null_rate(self) -> float:
        return (self.n_null / self.n_rows) if self.n_rows else 0.0

    def as_prompt_line(self) -> str:
        """One line of the structured profile handed to the inference model."""
        samples = ", ".join(self.samples[:3])
        pk = " [PK candidate]" if self.is_primary_key_candidate else ""
        return (
            f"- {self.source_name} ({self.sql_type}, {self.n_unique} distinct, "
            f"{self.null_rate:.0%} null, examples: {samples}){pk}"
        )


@dataclass(slots=True)
class TypedTable:
    """Values coerced to their inferred types; `frame` is the pandas frame the
    Parquet writer serialises."""

    frame: "pd.DataFrame"
    columns: list[ColumnProfile]
    origin: TableOrigin
    notes: list[str] = field(default_factory=list)
    header_confidence: InferenceConfidence = InferenceConfidence.HIGH

    @property
    def row_count(self) -> int:
        return int(len(self.frame))


@dataclass(slots=True)
class DetectedTable:
    """A table ready to become a Dataset: named, typed, profiled.

    `table_description` and the semantic column names/descriptions are filled
    in by the inference provider when one is configured; with
    `provider=none` they stay empty and the deterministic names stand.
    """

    typed: TypedTable
    suggested_name: str
    source_label: str
    table_description: str | None = None

    @property
    def confidence(self) -> InferenceConfidence:
        """Overall confidence = the weakest link.

        A table whose header had to be guessed is not "alta" just because its
        column types were unambiguous — the user needs to look at it.
        """
        ranks = {InferenceConfidence.HIGH: 2, InferenceConfidence.MEDIUM: 1, InferenceConfidence.LOW: 0}
        worst = min(
            [self.typed.header_confidence, *[c.confidence for c in self.typed.columns]],
            key=lambda c: ranks[c],
            default=InferenceConfidence.MEDIUM,
        )
        return worst


@dataclass(slots=True)
class NewDataset:
    """A materialised table, ready to become rows in the control plane.

    The hand-off between the two halves of ingestion: everything above this
    point is pure computation over bytes, everything below is persistence. It
    exists so `process_upload` can build the whole result before any backend —
    Postgres or the in-memory fixtures — is involved, which is what lets the
    same orchestrator drive both.

    `storage_key` points at the Parquet object; the rows themselves are never
    carried here.
    """

    id: str
    name: str
    table_name: str
    source_label: str
    storage_key: str
    file_type: DatasetFileType
    row_count: int
    column_count: int
    size_label: str
    meta: str
    schema: DatasetSchema
    embeddings: list[list[float]] = field(default_factory=list)
    embedding_model: str | None = None


@dataclass(slots=True)
class IngestOutcome:
    """What `run_pipeline` returns: every table found, plus what to tell the user."""

    tables: list[DetectedTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
