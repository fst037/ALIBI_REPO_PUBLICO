"""Updating a dataset with a new file: append to it, or replace it.

A person with a monthly report uploads next month's file and wants it *in* the
dataset they already have — or uploads the corrected export of the same months
and wants it to *be* that dataset. What must not happen is a new dataset: charts
and boards address a dataset by its id and its columns by name (`FROM ds_<id>`),
so a new id breaks every one of them.

So the file goes through the ordinary upload first (the same pipeline, the same
inference), landing as an unconfirmed dataset — the **staged** one. Then:

    staged table ──align by header──▶ dataset's columns, types checked
                 ──append / replace──▶ combined table (DuckDB)
                 ──recipe (ETLite)───▶ cleaned
                 ──overwrite the dataset's Parquet; drop the staged dataset

The dataset keeps its id, column names, folders and confirmation. Only the rows
(and the counts that describe them) change.

**The structure must be the same.** A column the file lacks, one it adds, or
one whose kind changed is a refusal listing the differences; nothing is written.
Silently tolerating a difference is how a chart ends up reading an empty
column, which is exactly what keeping the dataset was meant to avoid.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from typing import Iterator

from app.core.config import get_settings
from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.ingest.storage import dataset_parquet_key, dataset_prefix, get_object_store
from app.repositories import datasets as repo
from app.schemas.datasets import (
    CleaningRecipe,
    Dataset,
    DatasetSchema,
    DatasetStatus,
    DatasetUpdatePreview,
    DatasetUpdateRequest,
    OverlapStrategy,
    SchemaColumn,
    StructureDifference,
    UpdateMode,
    UpdateTarget,
)
from app.services import datasets as datasets_service
from app.services import datasets_memory as mem
from app.services import etlite
from app.services.query import QueryError, QueryTable, _quote_ident, _quote_literal, run_sql

PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"

_EXISTING = "existing"
_INCOMING = "incoming"


def _header_key(name: str) -> str:
    """Headers are compared the way people read them: case, accents, spaces, `_` ignored."""
    return datasets_service.normalised_name(name.replace("_", " "))


# ---------------------------------------------------------------------------
# Aligning the incoming table with the dataset
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Alignment:
    """How the incoming columns line up with the dataset's, in the dataset's order."""

    differences: list[StructureDifference]
    # Per dataset column (by ordinal): the incoming column's name in its Parquet.
    incoming: list[str | None]
    existing_physical: list[str]

    @property
    def compatible(self) -> bool:
        return not self.differences


def _align_names(
    target_schema: DatasetSchema,
    target_physical: list[str],
    staged_schema: DatasetSchema,
    staged_physical: list[str],
) -> Alignment:
    """Match columns by header, without touching any data.

    A dataset column answers to its Parquet name, the raw header and its current
    name, because a rename at confirmation or an inference rename must not make
    the same file look different. An incoming column answers to its own names.
    """
    target_keys: list[set[str]] = []
    for ordinal, column in enumerate(target_schema.columns):
        keys = {_header_key(column.name)}
        if column.sourceName:
            keys.add(_header_key(column.sourceName))
        if ordinal < len(target_physical):
            keys.add(_header_key(target_physical[ordinal]))
        target_keys.append(keys)

    incoming: list[str | None] = [None] * len(target_schema.columns)
    differences: list[StructureDifference] = []
    for ordinal, column in enumerate(staged_schema.columns):
        physical = staged_physical[ordinal] if ordinal < len(staged_physical) else column.name
        keys = {_header_key(column.name), _header_key(physical)}
        if column.sourceName:
            keys.add(_header_key(column.sourceName))
        hits = [i for i, tk in enumerate(target_keys) if tk & keys and incoming[i] is None]
        if len(hits) > 1:
            exact = [i for i in hits if _header_key(physical) in target_keys[i]]
            hits = exact[:1] if len(exact) >= 1 else hits
        if not hits:
            differences.append(StructureDifference(kind="extra", column=column.sourceName or column.name))
        elif len(hits) > 1:
            differences.append(StructureDifference(kind="ambiguous", column=column.sourceName or column.name))
        else:
            incoming[hits[0]] = physical
    for ordinal, name in enumerate(incoming):
        if name is None:
            differences.append(
                StructureDifference(kind="missing", column=target_schema.columns[ordinal].name)
            )
    return Alignment(differences, incoming, list(target_physical))


def _check_types(
    alignment: Alignment, target_schema: DatasetSchema, existing_uri: str, incoming_uri: str
) -> None:
    """Add a `type` difference for every column whose kind changed.

    Compared on the *physical* types (what the Parquet holds), not the editable
    labels. A whole number may widen into a decimal column; nothing else
    converts, and an incoming column with no values at all fits anywhere.
    """
    existing_types = etlite.physical_types(existing_uri)
    incoming_types = etlite.physical_types(incoming_uri)
    for ordinal, source in enumerate(alignment.incoming):
        if source is None:
            continue
        want = existing_types.get(alignment.existing_physical[ordinal])
        have = incoming_types.get(source)
        if want is None or have is None:
            continue
        want_kind, have_kind = etlite.kind_of(want), etlite.kind_of(have)
        if want_kind == have_kind or (want_kind == "number" and have_kind == "integer"):
            continue
        if _is_all_empty(incoming_uri, source):
            continue
        alignment.differences.append(
            StructureDifference(
                kind="type",
                column=target_schema.columns[ordinal].name,
                expected=want_kind,
                found=have_kind,
            )
        )


def _is_all_empty(uri: str, column: str) -> bool:
    table = QueryTable(name=_INCOMING, uri=uri)
    row = run_sql(
        f"SELECT count({_quote_ident(column)}) AS n FROM {_INCOMING}", [table], validate=False, limit=1
    ).rows[0]
    return int(row["n"] or 0) == 0


def _uri(owner_id: str, dataset_id: str) -> str:
    return get_object_store().uri_for(dataset_parquet_key(owner_id, dataset_id))


@dataclass(slots=True)
class _Pair:
    target: Dataset
    staged: Dataset
    target_schema: DatasetSchema
    alignment: Alignment


def _align(owner_id: str, target_id: str, staged_id: str, *, types: bool = True) -> _Pair:
    if target_id == staged_id:
        raise ApiException(422, "invalid_update", "Elegí un dataset distinto del archivo nuevo.")
    target = datasets_service.get_dataset(owner_id, target_id)
    staged = datasets_service.get_dataset(owner_id, staged_id)
    if target is None or staged is None:
        raise ApiException(404, "not_found", "No se encontró el dataset.")
    if target.status is not DatasetStatus.processed or not target.tableName:
        raise ApiException(
            409, "dataset_not_confirmed", "Confirmá el dataset antes de actualizarlo con un archivo nuevo."
        )
    if staged.status is not DatasetStatus.awaiting_confirmation or not staged.tableName:
        raise ApiException(409, "staged_unavailable", "Este archivo ya no está disponible para actualizar.")
    target_schema = datasets_service.get_schema(owner_id, target_id)
    staged_schema = datasets_service.get_schema(owner_id, staged_id)
    if target_schema is None or staged_schema is None:
        raise ApiException(409, "schema_not_ready", "Todavía estamos leyendo la estructura del archivo.")
    alignment = _align_names(
        target_schema,
        datasets_service.physical_names(owner_id, target_id),
        staged_schema,
        datasets_service.physical_names(owner_id, staged_id),
    )
    if types and alignment.compatible:
        try:
            _check_types(alignment, target_schema, _uri(owner_id, target_id), _uri(owner_id, staged_id))
        except QueryError as exc:
            raise ApiException(409, exc.code, exc.message) from exc
    return _Pair(target, staged, target_schema, alignment)


def update_targets(owner_id: str, staged_id: str) -> list[UpdateTarget] | None:
    """Every confirmed dataset the staged table could update, fitting ones first."""
    staged = datasets_service.get_dataset(owner_id, staged_id)
    if staged is None:
        return None
    found: list[UpdateTarget] = []
    for candidate in datasets_service.list_datasets(owner_id, status=DatasetStatus.processed):
        if candidate.id == staged_id or not candidate.tableName:
            continue
        try:
            pair = _align(owner_id, candidate.id, staged_id)
        except ApiException:
            continue
        found.append(
            UpdateTarget(
                datasetId=candidate.id,
                name=candidate.name,
                rows=candidate.rows,
                compatible=pair.alignment.compatible,
                differences=pair.alignment.differences,
            )
        )
    # Fitting first, then the ones that share the most headers (fewest differences).
    found.sort(key=lambda t: (not t.compatible, len(t.differences)))
    return found


# ---------------------------------------------------------------------------
# Building the combined table
# ---------------------------------------------------------------------------


def _aligned_sql(pair: _Pair, existing_uri: str) -> str:
    """The incoming rows under the dataset's physical names and types."""
    types = etlite.physical_types(existing_uri)
    parts = []
    for ordinal, source in enumerate(pair.alignment.incoming):
        assert source is not None
        name = pair.alignment.existing_physical[ordinal]
        parts.append(f"CAST({_quote_ident(source)} AS {types[name]}) AS {_quote_ident(name)}")
    return f"SELECT {', '.join(parts)} FROM {_INCOMING}"


def _columns_sql(pair: _Pair) -> str:
    return ", ".join(_quote_ident(n) for n in pair.alignment.existing_physical)


def _overlap_clause(pair: _Pair) -> str:
    match = " AND ".join(
        f"t.{_quote_ident(n)} IS NOT DISTINCT FROM a.{_quote_ident(n)}"
        for n in pair.alignment.existing_physical
    )
    return f"EXISTS (SELECT 1 FROM {_EXISTING} t WHERE {match})"


@dataclass(slots=True)
class _Combined:
    path: str
    existing_rows: int
    incoming_rows: int
    overlap_rows: int
    rows: int


@contextlib.contextmanager
def _combined(
    owner_id: str, pair: _Pair, mode: UpdateMode, on_overlap: OverlapStrategy
) -> Iterator[_Combined]:
    """The table the dataset would hold, written to a temp Parquet (removed on exit)."""
    existing_uri = _uri(owner_id, pair.target.id)
    tables = [
        QueryTable(name=_EXISTING, uri=existing_uri),
        QueryTable(name=_INCOMING, uri=_uri(owner_id, pair.staged.id)),
    ]
    aligned = _aligned_sql(pair, existing_uri)
    columns = _columns_sql(pair)

    def count(sql: str) -> int:
        return int(run_sql(f"SELECT count(*) AS n FROM ({sql})", tables, validate=False, limit=1).rows[0]["n"])

    try:
        existing_rows = count(f"SELECT 1 FROM {_EXISTING}")
        incoming_rows = count(aligned)
        overlap = 0
        if mode is UpdateMode.append:
            overlap = count(f"SELECT 1 FROM ({aligned}) a WHERE {_overlap_clause(pair)}")
            incoming = (
                f"SELECT * FROM ({aligned}) a WHERE NOT {_overlap_clause(pair)}"
                if on_overlap is OverlapStrategy.skip
                else aligned
            )
            final = f"SELECT {columns} FROM {_EXISTING} UNION ALL SELECT {columns} FROM ({incoming})"
        else:
            final = aligned
        handle, path = tempfile.mkstemp(suffix=".parquet")
        os.close(handle)
    except QueryError as exc:
        raise ApiException(
            422, "incompatible_values", "Algunos valores del archivo no encajan en las columnas del dataset."
        ) from exc
    try:
        try:
            run_sql(
                f"COPY ({final}) TO {_quote_literal(path)} (FORMAT parquet, COMPRESSION snappy)",
                tables,
                validate=False,
            )
            rows = int(
                run_sql(
                    "SELECT count(*) AS n FROM combined",
                    [QueryTable(name="combined", uri=path)],
                    validate=False,
                    limit=1,
                ).rows[0]["n"]
            )
        except QueryError as exc:
            raise ApiException(
                422, "incompatible_values", "Algunos valores del archivo no encajan en las columnas del dataset."
            ) from exc
        limit = get_settings().max_rows_per_table
        if rows > limit:
            raise ApiException(
                422,
                "row_limit_exceeded",
                f"El resultado tendría {rows:,} filas y el máximo por tabla es {limit:,}.",
            )
        yield _Combined(path, existing_rows, incoming_rows, overlap, rows)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _prepared(pair: _Pair, path: str, recipe: CleaningRecipe | None) -> etlite.PreparedCleaning:
    """The recipe checked against the combined table (fill values resolved over all of it)."""
    columns: list[SchemaColumn] = [c.model_copy(deep=True) for c in pair.target_schema.columns]
    for column in columns:
        column.cleaning = None
    if recipe is not None:
        for item in recipe.columns:
            if 0 <= item.ordinal < len(columns):
                columns[item.ordinal].cleaning = item.cleaning.model_copy(deep=True)
    return datasets_service._prepare(
        path, pair.alignment.existing_physical, columns, recipe.table if recipe else None
    )


def stored_recipe(owner_id: str, dataset_id: str) -> CleaningRecipe | None:
    if pool.is_configured():
        with session_scope() as session:
            raw = repo.cleaning_recipe(session, owner_id, dataset_id)
    else:
        raw = mem.cleaning_recipe(owner_id, dataset_id)
    return CleaningRecipe.model_validate(raw) if raw else None


# ---------------------------------------------------------------------------
# Preview and apply
# ---------------------------------------------------------------------------


def preview_update(
    owner_id: str, target_id: str, request: DatasetUpdateRequest
) -> DatasetUpdatePreview:
    """What the update would do, with nothing written."""
    pair = _align(owner_id, target_id, request.stagedDatasetId)
    stored = stored_recipe(owner_id, target_id)
    if not pair.alignment.compatible:
        return DatasetUpdatePreview(
            compatible=False, differences=pair.alignment.differences, storedCleaning=stored
        )
    with _combined(owner_id, pair, request.mode, request.onOverlap) as combined:
        prepared = _prepared(pair, combined.path, request.cleaning)
        result_rows = combined.rows
        if prepared.plan is not None:
            table = QueryTable(name="cleaned", uri=combined.path, cleaning=prepared.plan)
            try:
                result_rows = etlite.count_rows(table)
            except QueryError as exc:
                raise ApiException(422, "invalid_cleaning", exc.message) from exc
        return DatasetUpdatePreview(
            compatible=True,
            existingRows=combined.existing_rows,
            incomingRows=combined.incoming_rows,
            overlapRows=combined.overlap_rows,
            resultRows=result_rows,
            storedCleaning=stored,
            fills=prepared.fills,
        )


def apply_update(owner_id: str, target_id: str, request: DatasetUpdateRequest) -> Dataset:
    """Overwrite the dataset's rows. Raises `ApiException`; on failure nothing has changed."""
    from app.services.ingestion import human_size

    pair = _align(owner_id, target_id, request.stagedDatasetId)
    if not pair.alignment.compatible:
        raise ApiException(
            422,
            "structure_mismatch",
            "El archivo no tiene la misma estructura que el dataset: "
            + "; ".join(_describe(d) for d in pair.alignment.differences),
        )
    store = get_object_store()
    key = dataset_parquet_key(owner_id, target_id)
    with _combined(owner_id, pair, request.mode, request.onOverlap) as combined:
        prepared = _prepared(pair, combined.path, request.cleaning)
        samples: dict[int, str] = {}
        if prepared.plan is not None:
            try:
                done = etlite.materialise(combined.path, pair.alignment.existing_physical, prepared)
            except QueryError as exc:
                raise ApiException(422, "invalid_cleaning", exc.message) from exc
            data, rows, samples = done.parquet, done.row_count, done.samples
        else:
            with open(combined.path, "rb") as fh:
                data = fh.read()
            rows = combined.rows

    meta = f"{rows:,} rows" + (f" · {pair.target.sourceLabel}" if pair.target.sourceLabel else "")
    recipe = datasets_service.recipe_of(prepared)
    recipe_json = recipe.model_dump(mode="json") if recipe is not None else None

    # The file first, the control plane second — and the old file put back if the
    # second step fails, because an append retried over a half-applied one would
    # add the month twice.
    previous = store.get(key)
    store.put(key, data, PARQUET_CONTENT_TYPE)
    try:
        sizes = dict(
            row_count=rows,
            size_label=human_size(len(data)),
            meta=meta,
            samples=samples,
            recipe=recipe_json,
        )
        if pool.is_configured():
            with session_scope() as session:
                updated = repo.apply_update(session, owner_id, target_id, request.stagedDatasetId, **sizes)
        else:
            updated = mem.apply_update(owner_id, target_id, request.stagedDatasetId, **sizes)
    except Exception:
        store.put(key, previous, PARQUET_CONTENT_TYPE)
        raise
    if updated is None:
        store.put(key, previous, PARQUET_CONTENT_TYPE)
        raise ApiException(404, "not_found", "No se encontró el dataset.")
    try:
        store.delete_prefix(dataset_prefix(owner_id, request.stagedDatasetId))
    except OSError:
        pass
    return updated


def _describe(d: StructureDifference) -> str:
    if d.kind == "missing":
        return f"falta la columna «{d.column}»"
    if d.kind == "extra":
        return f"sobra la columna «{d.column}»"
    if d.kind == "ambiguous":
        return f"la columna «{d.column}» coincide con más de una"
    return f"«{d.column}» pasó de {d.expected} a {d.found}"
