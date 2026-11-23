"""`folders` domain routes (Module B).

Implements the `/folders` half of `contracts/ingestion.yaml` v0.2.

A folder groups datasets into a "project" so a conversation or a dashboard can
select the whole thing at once. One level only — the absence of a `parentId`
anywhere in this file is the feature, not an omission.

The folder↔dataset relation is many-to-many and is *only a mapping*: `POST
/folders/{id}/datasets` links, it never copies, and `DELETE /folders/{id}`
removes the folder and its links while every dataset stays exactly where it
was.
"""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.folders import (
    AttachDatasetsRequest,
    CreateFolderRequest,
    Folder,
    FolderDetail,
    UpdateFolderRequest,
)
from app.services import folders as store

router = APIRouter(tags=["folders"])

_NAME_TAKEN = "Ya tenés una carpeta con ese nombre."


@router.get("/folders", response_model=list[Folder], summary="The user's folders.")
def list_folders(user: User = Depends(require_user)) -> list[Folder]:
    return store.list_folders(user.id)


@router.post("/folders", response_model=Folder, status_code=201, summary="Create a folder.")
def create_folder(
    request: CreateFolderRequest, user: User = Depends(require_user)
) -> Folder | JSONResponse:
    if store.name_taken(user.id, request.name):
        return error_response(409, "folder_name_taken", _NAME_TAKEN)
    return store.create_folder(user.id, request.name, request.datasetIds)


@router.get(
    "/folders/{folder_id}",
    response_model=FolderDetail,
    response_model_exclude_none=True,
    summary="Folder detail, with its datasets.",
)
def get_folder(folder_id: str, user: User = Depends(require_user)) -> FolderDetail | JSONResponse:
    folder = store.get_folder(user.id, folder_id)
    if folder is None:
        return error_response(404, "not_found", "No se encontró la carpeta.")
    return folder


@router.patch("/folders/{folder_id}", response_model=Folder, summary="Rename a folder.")
def update_folder(
    folder_id: str, request: UpdateFolderRequest, user: User = Depends(require_user)
) -> Folder | JSONResponse:
    if store.name_taken(user.id, request.name, excluding=folder_id):
        return error_response(409, "folder_name_taken", _NAME_TAKEN)
    folder = store.rename_folder(user.id, folder_id, request.name)
    if folder is None:
        return error_response(404, "not_found", "No se encontró la carpeta.")
    return folder


@router.delete(
    "/folders/{folder_id}", status_code=204, response_model=None, summary="Delete the folder."
)
def delete_folder(folder_id: str, user: User = Depends(require_user)) -> Response | JSONResponse:
    if not store.delete_folder(user.id, folder_id):
        return error_response(404, "not_found", "No se encontró la carpeta.")
    return Response(status_code=204)


@router.post(
    "/folders/{folder_id}/datasets",
    response_model=FolderDetail,
    response_model_exclude_none=True,
    summary="Attach existing datasets to the folder.",
)
def attach_datasets(
    folder_id: str, request: AttachDatasetsRequest, user: User = Depends(require_user)
) -> FolderDetail | JSONResponse:
    folder = store.attach(user.id, folder_id, request.datasetIds)
    if folder is None:
        return error_response(404, "not_found", "No se encontró la carpeta.")
    return folder


@router.delete(
    "/folders/{folder_id}/datasets/{dataset_id}",
    status_code=204,
    response_model=None,
    summary="Remove a dataset from the folder (does not delete it).",
)
def detach_dataset(
    folder_id: str, dataset_id: str, user: User = Depends(require_user)
) -> Response | JSONResponse:
    if not store.detach(user.id, folder_id, dataset_id):
        return error_response(404, "not_found", "No se encontró el vínculo.")
    return Response(status_code=204)
