"""In-memory datasets store, seeded from JSON fixtures.

The fallback the `datasets` service uses when no database is configured
(ALIBI_DATABASE_URL unset) — e.g. tests and a no-database local run. It is
single-user, so the `owner_id` the service passes is accepted but ignored here
(the DB repository is where owner scoping actually happens). Mutations persist
within a process run only.
"""

import json
from pathlib import Path

from app.schemas.datasets import (
    ConfirmSchemaRequest,
    Dataset,
    DatasetSchema,
    DatasetStatus,
    UpdateDatasetRequest,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# Module-level mutable state, seeded once at import.
_datasets: list[Dataset] = [Dataset(**item) for item in _load("datasets.json")]
_schemas: dict[str, DatasetSchema] = {
    s.datasetId: s for s in (DatasetSchema(**item) for item in _load("schemas.json"))
}
# dataset_id → the column names inside the Parquet file, by ordinal. Mirrors
# `dataset_columns.physical_name` in the database: fixed at ingestion so a
# rename stays a view projection, and correct even after a second rename.
_physical: dict[str, list[str]] = {}
# dataset_id → the ETLite recipe it was last confirmed or updated with.
_recipes: dict[str, dict] = {}


def reset() -> None:
    """Restore the seeded fixtures, discarding anything ingested (tests)."""
    _datasets[:] = [Dataset(**item) for item in _load("datasets.json")]
    _schemas.clear()
    _schemas.update(
        {s.datasetId: s for s in (DatasetSchema(**item) for item in _load("schemas.json"))}
    )
    _physical.clear()
    _recipes.clear()


def list_datasets(
    _owner_id: str, *, folder_id: str | None = None, status: DatasetStatus | None = None
) -> list[Dataset]:
    # A copy of the list, but the same Dataset objects: callers must not be able
    # to insert or drop entries behind this module's back, while `ingest_memory`
    # legitimately mutates `folderIds` on the objects themselves (folder
    # membership is stored there and nowhere else).
    items = list(_datasets)
    if folder_id is not None:
        items = [d for d in items if folder_id in d.folderIds]
    if status is not None:
        items = [d for d in items if d.status is status]
    return items


def get_dataset(_owner_id: str, dataset_id: str) -> Dataset | None:
    return next((d for d in _datasets if d.id == dataset_id), None)


def add_dataset(dataset: Dataset, schema: DatasetSchema) -> None:
    """Record a freshly materialised table (called by the ingestion commit)."""
    _datasets.insert(0, dataset)
    _schemas[dataset.id] = schema
    _physical[dataset.id] = [c.name for c in schema.columns]


def existing_table_names(_owner_id: str) -> set[str]:
    return {d.tableName for d in _datasets if d.tableName}


def physical_names(_owner_id: str, dataset_id: str) -> list[str]:
    return list(_physical.get(dataset_id, []))


def get_schema(_owner_id: str, dataset_id: str) -> DatasetSchema | None:
    return _schemas.get(dataset_id)


def update_dataset(owner_id: str, dataset_id: str, request: UpdateDatasetRequest) -> Dataset | None:
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return None
    if request.name is not None:
        dataset.name = request.name
    if request.favorite is not None:
        dataset.favorite = request.favorite
    return dataset


def delete_dataset(owner_id: str, dataset_id: str) -> bool:
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return False
    _datasets.remove(dataset)
    _schemas.pop(dataset_id, None)
    _physical.pop(dataset_id, None)
    _recipes.pop(dataset_id, None)
    return True


def confirm_schema(
    owner_id: str,
    dataset_id: str,
    request: ConfirmSchemaRequest,
    *,
    row_count: int | None = None,
    size_label: str | None = None,
    meta: str | None = None,
    recipe: dict | None = None,
) -> Dataset | None:
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return None
    if recipe is not None:
        _recipes[dataset_id] = recipe
    existing = _schemas.get(dataset_id)
    if existing is not None:
        existing.columns = request.columns
        existing.confirmed = True
        if row_count is not None:
            existing.rowCount = row_count
    if row_count is not None:
        dataset.rows = row_count
    if size_label is not None:
        dataset.sizeLabel = size_label
    if meta is not None:
        dataset.meta = meta
    dataset.status = DatasetStatus.processed
    dataset.columns = sum(1 for c in request.columns if c.included)
    # Confirming is also where a multi-table upload gets its tables told apart,
    # so the rename travels with it.
    if request.name is not None:
        dataset.name = request.name
    return dataset


def cleaning_recipe(_owner_id: str, dataset_id: str) -> dict | None:
    return _recipes.get(dataset_id)


def apply_update(
    owner_id: str,
    dataset_id: str,
    staged_id: str,
    *,
    row_count: int,
    size_label: str,
    meta: str,
    samples: dict[int, str],
    recipe: dict | None,
) -> Dataset | None:
    dataset = get_dataset(owner_id, dataset_id)
    if dataset is None:
        return None
    dataset.rows = row_count
    dataset.sizeLabel = size_label
    dataset.meta = meta
    schema = _schemas.get(dataset_id)
    if schema is not None:
        schema.rowCount = row_count
        for ordinal, text in samples.items():
            if ordinal < len(schema.columns):
                schema.columns[ordinal].sampleValues = text
    if recipe is None:
        _recipes.pop(dataset_id, None)
    else:
        _recipes[dataset_id] = recipe
    delete_dataset(owner_id, staged_id)
    return dataset
