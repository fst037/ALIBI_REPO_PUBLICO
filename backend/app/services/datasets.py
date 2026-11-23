"""`datasets` domain service — the single swap point between storage backends.

Routes call these functions with the same signatures regardless of where the
data lives. When a database is configured (ALIBI_DATABASE_URL set), each call
borrows a pooled connection and delegates to `app.repositories.datasets`,
scoped to `owner_id` (the authenticated user); when it isn't, it falls back to
the in-memory fixtures in `datasets_memory` (single-user, so `owner_id` is
ignored there) — so tests and a no-database local run keep working unchanged.

`query_table` is the bridge into the *data* plane: it turns a dataset's stored
metadata into the `QueryTable` the DuckDB layer reads. It is the only place
that knows a confirmed rename is expressed as a view projection rather than a
rewritten Parquet file.
"""

import unicodedata
from dataclasses import dataclass

from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.ingest.storage import dataset_parquet_key, dataset_prefix, get_object_store
from app.repositories import datasets as repo
from app.schemas.datasets import (
    CleaningPreview,
    CleaningPreviewRequest,
    CleaningRecipe,
    ColumnRecipe,
    ConfirmSchemaRequest,
    Dataset,
    DatasetQuality,
    DatasetSchema,
    DatasetStatus,
    PreviewColumn,
    SchemaColumn,
    TableCleaning,
    TablePreview,
    UpdateDatasetRequest,
)
from app.services import datasets_memory as mem
from app.services import etlite
from app.services.query import (
    QueryError,
    QueryTable,
    is_numeric_column,
    preview as run_preview,
    table_for_dataset,
)
from app.services.text2sql import ddl


def list_datasets(
    owner_id: str, *, folder_id: str | None = None, status: DatasetStatus | None = None
) -> list[Dataset]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_datasets(session, owner_id, folder_id=folder_id, status=status)
    return mem.list_datasets(owner_id, folder_id=folder_id, status=status)


def get_dataset(owner_id: str, dataset_id: str) -> Dataset | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_dataset(session, owner_id, dataset_id)
    return mem.get_dataset(owner_id, dataset_id)


def get_schema(owner_id: str, dataset_id: str) -> DatasetSchema | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_schema(session, owner_id, dataset_id)
    return mem.get_schema(owner_id, dataset_id)


def update_dataset(owner_id: str, dataset_id: str, request: UpdateDatasetRequest) -> Dataset | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.update_dataset(session, owner_id, dataset_id, request)
    return mem.update_dataset(owner_id, dataset_id, request)


def delete_dataset(owner_id: str, dataset_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            deleted = repo.delete_dataset(session, owner_id, dataset_id)
    else:
        deleted = mem.delete_dataset(owner_id, dataset_id)
    if deleted:
        # The control-plane row is gone, so the Parquet behind it is
        # unreachable and would just accrue storage cost.
        try:
            get_object_store().delete_prefix(dataset_prefix(owner_id, dataset_id))
        except OSError:
            pass
    return deleted


def normalised_name(name: str) -> str:
    """The form two dataset names are compared in: trimmed, case- and accent-blind.

    `Ventas 2024`, `ventas 2024` and `Ventás  2024` are the same name to a person
    looking at a library list, so they must be the same name to the uniqueness
    check — otherwise "no duplicates" is a rule the user can break by accident.
    """
    folded = unicodedata.normalize("NFKD", name.strip())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(folded.split()).casefold()


def name_is_taken(owner_id: str, name: str, *, excluding: str | None = None) -> str | None:
    """The existing dataset that already uses `name`, or None.

    `excluding` is the dataset being renamed — without it, confirming a dataset
    without changing its name would collide with itself.
    """
    target = normalised_name(name)
    if not target:
        return None
    for dataset in list_datasets(owner_id):
        if dataset.id == excluding:
            continue
        if normalised_name(dataset.name) == target:
            return dataset.name
    return None


def confirm_schema(owner_id: str, dataset_id: str, request: ConfirmSchemaRequest) -> Dataset | None:
    """Activate the dataset, refusing a name another dataset already uses.

    The uniqueness rule is enforced *here*, at confirmation, and deliberately not
    when the upload is registered. Rejecting at registration would be simpler,
    but it leaves the user nowhere to fix the problem: the name comes from the
    filename, so the only remedy would be renaming the file on disk and starting
    over. Confirmation is the one screen with an editable name field, so failing
    here is what actually forces — and enables — the rename.
    """
    current = get_dataset(owner_id, dataset_id)
    if current is None:
        return None
    proposed = (request.name or current.name).strip()
    clash = name_is_taken(owner_id, proposed, excluding=dataset_id)
    if clash is not None:
        raise ApiException(
            409,
            "dataset_name_taken",
            f"Ya tenés un dataset llamado “{clash}”. "
            "Dale un nombre distinto a este para continuar.",
        )

    # ETLite: the cleaned table replaces the stored one, written *before* the
    # control plane calls the dataset confirmed — a recipe that cannot apply is
    # refused with the dataset still awaiting confirmation. Should the commit
    # below then fail, a retry cleans an already clean table, which every step
    # leaves unchanged.
    cleaned: CleanedTable | None = None
    recipe: dict | None = None
    if request.cleaning is not None or any(c.cleaning for c in request.columns):
        if not current.tableName:
            raise ApiException(409, "table_not_ready", "Esta tabla todavía no está disponible.")
        request, cleaned, stored = _clean_in_place(owner_id, dataset_id, current, request)
        # Kept so the next file that updates this dataset is offered the same
        # cleaning. Not re-applied by itself.
        recipe = stored.model_dump(mode="json") if stored is not None else None

    sizes: dict = (
        {"row_count": cleaned.row_count, "size_label": cleaned.size_label, "meta": cleaned.meta}
        if cleaned is not None
        else {}
    )
    if recipe is not None:
        sizes["recipe"] = recipe
    if pool.is_configured():
        with session_scope() as session:
            return repo.confirm_schema(session, owner_id, dataset_id, request, **sizes)
    return mem.confirm_schema(owner_id, dataset_id, request, **sizes)


# ---------------------------------------------------------------------------
# ETLite
# ---------------------------------------------------------------------------


def physical_names(owner_id: str, dataset_id: str) -> list[str]:
    """Every column's name inside the Parquet, by ordinal (excluded ones too)."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.physical_names(session, owner_id, dataset_id)
    return mem.physical_names(owner_id, dataset_id)


def _raw_uri(owner_id: str, dataset_id: str) -> str:
    return table_for_dataset(owner_id, dataset_id, ddl.table_name(dataset_id)).uri


def _edited_table(
    dataset_id: str,
    uri: str,
    physical: list[str],
    columns: list[SchemaColumn],
    plan: etlite.CleaningPlan | None,
) -> QueryTable:
    """The view as it would be with these (unsaved) edits — same rules as `query_table`."""
    included = [(o, c) for o, c in enumerate(columns) if c.included and o < len(physical)]
    folded = ddl.fold_names([c.name for _, c in included])
    return QueryTable(
        name=ddl.table_name(dataset_id),
        uri=uri,
        projection={alias: physical[o] for alias, (o, _) in zip(folded, included)},
        cleaning=plan,
    )


def _prepare(
    uri: str, physical: list[str], columns: list[SchemaColumn], cleaning: TableCleaning | None
) -> etlite.PreparedCleaning:
    try:
        return etlite.prepare(uri, physical, columns, cleaning)
    except etlite.CleaningError as exc:
        raise ApiException(422, "invalid_cleaning", exc.message) from exc
    except QueryError as exc:
        raise ApiException(409, exc.code, exc.message) from exc


def _prepared(
    owner_id: str,
    dataset_id: str,
    columns: list[SchemaColumn],
    cleaning: TableCleaning | None,
) -> tuple[etlite.PreparedCleaning, QueryTable]:
    physical = physical_names(owner_id, dataset_id)
    uri = _raw_uri(owner_id, dataset_id)
    prepared = _prepare(uri, physical, columns, cleaning)
    return prepared, _edited_table(dataset_id, uri, physical, prepared.columns, prepared.plan)


@dataclass(slots=True)
class CleanedTable:
    """What the dataset's record must say once the cleaned file is stored."""

    row_count: int
    size_label: str
    meta: str


def recipe_of(prepared: etlite.PreparedCleaning) -> CleaningRecipe | None:
    """The prepared recipe in the form a dataset stores it (None when it does nothing)."""
    recipe = CleaningRecipe(
        table=prepared.table,
        columns=[
            ColumnRecipe(ordinal=ordinal, cleaning=column.cleaning)
            for ordinal, column in enumerate(prepared.columns)
            if column.cleaning is not None
        ],
    )
    return None if recipe.is_noop() else recipe


def _clean_in_place(
    owner_id: str, dataset_id: str, dataset: Dataset, request: ConfirmSchemaRequest
) -> tuple[ConfirmSchemaRequest, CleanedTable | None, CleaningRecipe | None]:
    """Write the cleaned table over the dataset's Parquet.

    Returns the request as it must be persisted: example values refreshed for
    the columns whose values changed, and no recipe left on it — the recipe has
    been applied to the data, so there is nothing to keep or re-apply.
    """
    # Lazy, as in `repositories.uploads`: the ingestion module is the processor's
    # entry point and has no business loading with every datasets import.
    from app.services.ingestion import PARQUET_CONTENT_TYPE, human_size

    physical = physical_names(owner_id, dataset_id)
    uri = _raw_uri(owner_id, dataset_id)
    prepared = _prepare(uri, physical, request.columns, request.cleaning)
    columns = [c.model_copy(update={"cleaning": None}) for c in prepared.columns]
    if prepared.plan is None:
        return request.model_copy(update={"columns": columns, "cleaning": None}), None, None

    try:
        done = etlite.materialise(uri, physical, prepared)
    except QueryError as exc:
        raise ApiException(422, "invalid_cleaning", exc.message) from exc
    for ordinal, samples in done.samples.items():
        columns[ordinal].sampleValues = samples

    get_object_store().put(
        dataset_parquet_key(owner_id, dataset_id), done.parquet, PARQUET_CONTENT_TYPE
    )
    meta = f"{done.row_count:,} rows" + (f" · {dataset.sourceLabel}" if dataset.sourceLabel else "")
    return (
        request.model_copy(update={"columns": columns, "cleaning": None}),
        CleanedTable(row_count=done.row_count, size_label=human_size(len(done.parquet)), meta=meta),
        recipe_of(prepared),
    )


def _count(table: QueryTable) -> int:
    try:
        return etlite.count_rows(table)
    except QueryError as exc:
        raise ApiException(422, "invalid_cleaning", exc.message) from exc


def quality(owner_id: str, dataset_id: str) -> DatasetQuality | None:
    """Nulls, blanks and duplicates in the stored table — what ETLite could fix."""
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return None
    if not dataset.tableName:
        raise QueryError("Esta tabla todavía no está disponible para consultar.", "table_not_ready")
    rows, duplicates, columns = etlite.quality(
        _raw_uri(owner_id, dataset_id), physical_names(owner_id, dataset_id)
    )
    return DatasetQuality(datasetId=dataset_id, rowCount=rows, duplicateRows=duplicates, columns=columns)


def preview_cleaning(
    owner_id: str, dataset_id: str, request: CleaningPreviewRequest, *, limit: int = 8
) -> CleaningPreview | None:
    """The table as these edits would leave it. Nothing is saved."""
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return None
    if not dataset.tableName:
        raise ApiException(409, "table_not_ready", "Esta tabla todavía no está disponible.")
    prepared, table = _prepared(owner_id, dataset_id, request.columns, request.cleaning)
    schema = get_schema(owner_id, dataset_id)
    before = schema.rowCount if schema is not None else (dataset.rows or 0)
    after = _count(table) if prepared.plan is not None else before
    try:
        result = run_preview(table, limit=limit)
    except QueryError as exc:
        raise ApiException(422, "invalid_cleaning", exc.message) from exc
    return CleaningPreview(
        rowCount=after,
        removedRows=max(before - after, 0),
        fills=prepared.fills,
        preview=TablePreview(
            datasetId=dataset_id,
            columns=[
                PreviewColumn(
                    key=name,
                    label=name,
                    align="num" if is_numeric_column(result.rows, name) else "left",
                )
                for name in result.columns
            ],
            rows=result.rows,
            truncated=result.truncated,
        ),
    )


def existing_table_names(owner_id: str) -> set[str]:
    """SQL table names already in use, so a new one can avoid them."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.existing_table_names(session, owner_id)
    return mem.existing_table_names(owner_id)


def _memory_projection(owner_id: str, dataset_id: str) -> dict[str, str]:
    schema = mem.get_schema(owner_id, dataset_id)
    physical = mem.physical_names(owner_id, dataset_id)
    if schema is None or not physical:
        return {}
    return {
        column.name: physical[ordinal]
        for ordinal, column in enumerate(schema.columns)
        # An excluded column is left out of the view entirely, so a generated
        # query cannot reach a column the user chose to hide.
        if column.included and ordinal < len(physical)
    }


def is_queryable(dataset: Dataset | None) -> bool:
    """Whether SQL may read this dataset.

    Only `processed`. `awaiting_confirmation` means the table is materialised and
    previewable but nobody has reviewed the *inferred* schema yet (Ingesta 1.2) —
    so querying it would answer using column names and types the user never
    agreed to, and a rename made at confirmation would silently change what a
    saved query means.
    """
    return dataset is not None and dataset.status is DatasetStatus.processed


def query_table(
    owner_id: str,
    dataset_id: str,
    dataset: Dataset | None = None,
    *,
    require_confirmed: bool = False,
) -> QueryTable:
    """The dataset as SQL sees it. Raises `QueryError` if it has no table yet.

    `dataset` may be passed in by a caller that already looked it up (the
    preview route does, to answer 404 before getting here) — otherwise this
    fetches it, so the second round trip only happens when it is needed.

    **The view is named after the dataset id, not `dataset.tableName`.** That is
    the identifier Module A is shown and the one its guardrail allows
    (`text2sql.ddl.table_name`, whose docstring already required the data plane to
    match it). While nothing executed the generated SQL the mismatch was
    invisible; the moment it runs, a query saying `FROM ds_a8kf2` against a view
    called `ventas` fails every time. The id is also collision-proof, which
    matters now that several datasets are registered in one connection —
    `tableName` is a slug of the filename, so two uploads called `ventas.csv`
    only stay distinct through `unique_table_name`.

    `tableName` is still what the pipeline generated and what the UI shows; it is
    simply not the SQL identifier.

    `require_confirmed` is the Ingesta 1.2 rule. It defaults to False because the
    ingestion preview exists precisely to show an *unconfirmed* table — that is
    what the user reviews before confirming. Every SQL path passes True.
    """
    if dataset is None:
        dataset = get_dataset(owner_id, dataset_id)
    if dataset is None or not dataset.tableName:
        raise QueryError(
            "Esta tabla todavía no está disponible para consultar.", "table_not_ready"
        )
    if require_confirmed and not is_queryable(dataset):
        raise QueryError(
            f"«{dataset.name}» no se puede consultar hasta que confirmes su esquema.",
            "schema_unconfirmed",
        )
    if pool.is_configured():
        with session_scope() as session:
            projection = repo.projection(session, owner_id, dataset_id)
    else:
        projection = _memory_projection(owner_id, dataset_id)
    # The view must expose the *folded* names — the identifiers Module A is shown
    # and its guardrail allows. The stored name is the display name and may be
    # anything the user typed in the schema editor ("Monto Total"); aliasing the
    # view to that while telling the model `monto_total` produced SQL the
    # guardrail accepted and DuckDB then rejected. `ddl.fold_names` is the one
    # place that decides this, so the two cannot drift apart.
    projection = dict(
        zip(ddl.fold_names(list(projection.keys())), projection.values())
    )
    # Via `table_for_dataset` rather than building a QueryTable directly: it is
    # what checks the identifier before it reaches a CREATE VIEW.
    return table_for_dataset(
        owner_id, dataset_id, ddl.table_name(dataset_id), columns=projection
    )
