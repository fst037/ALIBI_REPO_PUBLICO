"""`datasets` domain routes (ingestion).

Implements `contracts/ingestion.yaml`. Backed by `app/services/datasets.py`,
which reads the database when configured (scoped to the authenticated user) or
the in-memory fixtures otherwise. The real Zero-ETL ingestion (Module B)
replaces the service layer later without touching these routes.

All endpoints require a bearer token (`require_user`) and operate only on the
caller's own datasets — `owner_id` scoping happens in the repository, so a
dataset that isn't yours reads as 404, not another user's data.

Creating a dataset lives in `uploads.py`, not here: a dataset is a *table found
inside* an uploaded file, and one file can yield several, so it is the upload
that is created and the datasets that fall out of it.
"""

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.datasets import (
    CleaningPreview,
    CleaningPreviewRequest,
    ConfirmSchemaRequest,
    Dataset,
    DatasetQuality,
    DatasetSchema,
    DatasetStatus,
    DatasetUpdatePreview,
    DatasetUpdateRequest,
    PreviewColumn,
    TablePreview,
    UpdateDatasetRequest,
    UpdateTarget,
)
from app.services import dataset_update
from app.services import datasets as store
from app.services.query import QueryError, is_numeric_column, preview as run_preview

router = APIRouter(tags=["datasets"])


@router.get(
    "/datasets",
    response_model=list[Dataset],
    response_model_exclude_none=True,
    summary="Datasets in the workspace.",
)
def list_datasets(
    folderId: str | None = Query(default=None),
    status: DatasetStatus | None = Query(default=None),
    user: User = Depends(require_user),
) -> list[Dataset]:
    return store.list_datasets(user.id, folder_id=folderId, status=status)


@router.get(
    "/datasets/{dataset_id}",
    response_model=Dataset,
    response_model_exclude_none=True,
    summary="Dataset details.",
)
def get_dataset(dataset_id: str, user: User = Depends(require_user)) -> Dataset | JSONResponse:
    dataset = store.get_dataset(user.id, dataset_id)
    if dataset is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return dataset


@router.patch(
    "/datasets/{dataset_id}",
    response_model=Dataset,
    response_model_exclude_none=True,
    summary="Update a dataset (name and/or favorite).",
)
def update_dataset(
    dataset_id: str, request: UpdateDatasetRequest, user: User = Depends(require_user)
) -> Dataset | JSONResponse:
    dataset = store.update_dataset(user.id, dataset_id, request)
    if dataset is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return dataset


@router.delete(
    "/datasets/{dataset_id}", status_code=204, response_model=None, summary="Delete a dataset."
)
def delete_dataset(dataset_id: str, user: User = Depends(require_user)) -> Response | JSONResponse:
    if not store.delete_dataset(user.id, dataset_id):
        return error_response(404, "not_found", "No se encontró el dataset.")
    return Response(status_code=204)


@router.get(
    "/datasets/{dataset_id}/schema",
    response_model=DatasetSchema,
    summary="Inferred schema for the dataset.",
)
def get_schema(dataset_id: str, user: User = Depends(require_user)) -> DatasetSchema | JSONResponse:
    if store.get_dataset(user.id, dataset_id) is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    schema = store.get_schema(user.id, dataset_id)
    if schema is None:
        return error_response(
            409, "schema_not_ready", "Todavía estamos infiriendo la estructura del archivo."
        )
    return schema


@router.put(
    "/datasets/{dataset_id}/schema",
    response_model=Dataset,
    response_model_exclude_none=True,
    summary="Confirm the schema and activate the dataset.",
)
def confirm_schema(
    dataset_id: str, request: ConfirmSchemaRequest, user: User = Depends(require_user)
) -> Dataset | JSONResponse:
    dataset = store.confirm_schema(user.id, dataset_id, request)
    if dataset is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return dataset


@router.get(
    "/datasets/{dataset_id}/preview",
    response_model=TablePreview,
    summary="First rows of the table.",
)
def preview_dataset(
    dataset_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(require_user),
) -> TablePreview | JSONResponse:
    """Reads the Parquet through DuckDB — the same engine Modules A and C use.

    No SQL crosses the wire here, so the read-only guard isn't involved: the
    statement is built from a checked table identifier and nothing else.
    """
    dataset = store.get_dataset(user.id, dataset_id)
    if dataset is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    try:
        table = store.query_table(user.id, dataset_id, dataset)
        result = run_preview(table, limit=limit)
    except QueryError as exc:
        # 409, not 500: the dataset exists, its table just isn't ready yet.
        return error_response(409, exc.code, exc.message)
    return TablePreview(
        datasetId=dataset_id,
        columns=[
            PreviewColumn(
                key=name,
                label=name,
                # Numbers right-aligned, everything else left — decided from the
                # values rather than the declared type, so a preview of an
                # unconfirmed table still lines up. "num" is the vocabulary the
                # frontend's DataTable already speaks (see charts.ResultColumn).
                align="num" if is_numeric_column(result.rows, name) else "left",
            )
            for name in result.columns
        ],
        rows=result.rows,
        truncated=result.truncated,
    )



@router.get(
    "/datasets/{dataset_id}/quality",
    response_model=DatasetQuality,
    summary="Empty cells, stray whitespace and duplicate rows in the stored table (ETLite).",
)
def dataset_quality(
    dataset_id: str, user: User = Depends(require_user)
) -> DatasetQuality | JSONResponse:
    """Counted over the table as ingested, before any cleaning — what ETLite could fix."""
    try:
        found = store.quality(user.id, dataset_id)
    except QueryError as exc:
        return error_response(409, exc.code, exc.message)
    if found is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return found


@router.post(
    "/datasets/{dataset_id}/cleaning-preview",
    response_model=CleaningPreview,
    summary="Preview the table with an ETLite cleaning recipe applied, without saving it.",
)
def preview_cleaning(
    dataset_id: str,
    request: CleaningPreviewRequest,
    limit: int = Query(default=8, ge=1, le=200),
    user: User = Depends(require_user),
) -> CleaningPreview | JSONResponse:
    """Same validation as confirming, so a recipe that previews is one that saves.

    422 `invalid_cleaning` when an operation cannot apply to a column (trimming
    a number, a fill value of the wrong type, a mean over an all-empty column).
    """
    found = store.preview_cleaning(user.id, dataset_id, request, limit=limit)
    if found is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return found


@router.get(
    "/datasets/{dataset_id}/update-targets",
    response_model=list[UpdateTarget],
    summary="Confirmed datasets that a freshly uploaded table could append to or replace.",
)
def update_targets(
    dataset_id: str, user: User = Depends(require_user)
) -> list[UpdateTarget] | JSONResponse:
    """`dataset_id` is the unconfirmed table the upload produced. Each candidate says
    whether the structure matches, and if not, how it differs."""
    found = dataset_update.update_targets(user.id, dataset_id)
    if found is None:
        return error_response(404, "not_found", "No se encontró el dataset.")
    return found


@router.post(
    "/datasets/{dataset_id}/update-preview",
    response_model=DatasetUpdatePreview,
    summary="What appending or replacing would do, without saving it.",
)
def preview_update(
    dataset_id: str, request: DatasetUpdateRequest, user: User = Depends(require_user)
) -> DatasetUpdatePreview:
    """`dataset_id` is the dataset being updated. Structure differences come back as
    `compatible: false` with the list, not as an error."""
    return dataset_update.preview_update(user.id, dataset_id, request)


@router.post(
    "/datasets/{dataset_id}/update",
    response_model=Dataset,
    response_model_exclude_none=True,
    summary="Append a new file's rows to the dataset, or replace them, keeping its id.",
)
def apply_update(
    dataset_id: str, request: DatasetUpdateRequest, user: User = Depends(require_user)
) -> Dataset:
    """The dataset keeps its id and column names, so charts and boards built on it keep
    working. 422 `structure_mismatch` (with the differences in the message) when the
    file is not the same structure; nothing is written then."""
    return dataset_update.apply_update(user.id, dataset_id, request)
