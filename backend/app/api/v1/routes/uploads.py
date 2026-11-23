"""`uploads` domain routes (Module B ingestion).

Implements the `/uploads` half of `contracts/ingestion.yaml` v0.2.

The API never receives the file. `POST /uploads` records the intent and returns
a presigned URL; the browser PUTs the bytes straight to the object store; the
arrival of the object starts the work. That is what keeps a 25 MB spreadsheet
from having to fit through API Gateway's payload limit and from occupying the
API Lambda for the duration of the transfer.

Progress is polled from `GET /uploads/{id}` rather than pushed. A websocket
would need a connection held open per upload, which a Lambda behind an HTTP API
cannot do — and the stepper in Ingesta 1.1 only has five states to show.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.config import ObjectStoreKind, get_settings
from app.core.errors import error_response
from app.ingest.storage import get_object_store
from app.schemas.auth import User
from app.schemas.uploads import CreateUploadRequest, CreateUploadResponse, Upload
from app.services import uploads as store
from app.services.uploads import UploadRejected

router = APIRouter(tags=["uploads"])


@router.get(
    "/uploads",
    response_model=list[Upload],
    response_model_exclude_none=True,
    summary="The user's recent uploads.",
)
def list_uploads(user: User = Depends(require_user)) -> list[Upload]:
    return store.list_uploads(user.id)


@router.post(
    "/uploads",
    response_model=CreateUploadResponse,
    response_model_exclude_none=True,
    status_code=201,
    summary="Register an upload and return a presigned URL.",
)
def create_upload(
    request: CreateUploadRequest, user: User = Depends(require_user)
) -> CreateUploadResponse | JSONResponse:
    try:
        upload, url, headers, expires = store.create_upload(user.id, request)
    except UploadRejected as exc:
        return error_response(exc.status, exc.code, exc.message)
    return CreateUploadResponse(
        upload=upload, uploadUrl=url, headers=headers, expiresAt=expires
    )


@router.get(
    "/uploads/{upload_id}",
    response_model=Upload,
    response_model_exclude_none=True,
    summary="Upload status (polling endpoint).",
)
def get_upload(upload_id: str, user: User = Depends(require_user)) -> Upload | JSONResponse:
    upload = store.get_upload(user.id, upload_id)
    if upload is None:
        return error_response(404, "not_found", "No se encontró la subida.")
    return upload


@router.delete(
    "/uploads/{upload_id}",
    status_code=204,
    response_model=None,
    summary="Cancel an upload and discard anything unconfirmed.",
)
def delete_upload(upload_id: str, user: User = Depends(require_user)) -> Response | JSONResponse:
    if not store.delete_upload(user.id, upload_id):
        return error_response(404, "not_found", "No se encontró la subida.")
    return Response(status_code=204)


@router.post(
    "/uploads/{upload_id}/retry",
    response_model=Upload,
    response_model_exclude_none=True,
    status_code=202,
    summary="Retry a failed upload.",
)
def retry_upload(upload_id: str, user: User = Depends(require_user)) -> Upload | JSONResponse:
    if store.get_upload(user.id, upload_id) is None:
        return error_response(404, "not_found", "No se encontró la subida.")
    upload = store.retry_upload(user.id, upload_id)
    if upload is None:
        return error_response(
            409,
            "retry_unavailable",
            "El archivo original ya no está disponible. Subilo de nuevo.",
        )
    return upload


# ---------------------------------------------------------------------------
# Local-mode upload receiver
# ---------------------------------------------------------------------------


@router.put(
    "/uploads/local/{key:path}",
    status_code=204,
    response_model=None,
    include_in_schema=False,
    summary="Local-mode upload receiver (does not exist in production).",
)
async def receive_local_upload(
    key: str, request: Request, user: User = Depends(require_user)
) -> Response | JSONResponse:
    """Stands in for S3 when `ALIBI_OBJECT_STORE=local`.

    `LocalFileStore.presigned_put` points here, so the browser's upload code is
    byte-for-byte the same in both modes — the only difference is which host
    the PUT goes to. Excluded from the OpenAPI schema on purpose: it is not
    part of the contract and must not appear in the generated client.

    Two checks matter. It requires the same bearer token as every other
    endpoint (a presigned S3 URL carries its own signature; this one carries
    nothing), and the key must sit under the caller's own prefix, so a token
    cannot be used to write into someone else's namespace.
    """
    if get_settings().object_store is not ObjectStoreKind.local:
        return error_response(404, "not_found", "Ruta no disponible.")

    prefix = f"uploads/{user.id}/"
    if not key.startswith(prefix):
        return error_response(403, "forbidden", "No podés escribir en esa ubicación.")

    upload_id = key[len(prefix) :].split("/", 1)[0]
    if store.get_upload(user.id, upload_id) is None:
        return error_response(404, "not_found", "No se encontró la subida.")

    body = await request.body()
    if not body:
        return error_response(400, "empty_file", "El archivo está vacío.")

    get_object_store().put(key, body, request.headers.get("content-type"))
    store.start_processing(user.id, upload_id)
    return Response(status_code=204)
