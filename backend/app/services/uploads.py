"""`uploads` domain service — the swap point between storage backends.

Same shape as `app/services/datasets.py`: a database when one is configured,
the in-memory fixtures otherwise. What it adds is the two side effects an
upload has outside the control plane — issuing a presigned URL against the
object store, and handing the job to a runner once the bytes land.
"""

from __future__ import annotations

from datetime import datetime

from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.ingest.detect import file_type_from_extension
from app.ingest.errors import UnsupportedFileType
from app.ingest.jobs import get_job_runner
from app.ingest.models import NewDataset
from app.ingest.storage import get_object_store, raw_upload_key, raw_upload_prefix
from app.repositories import uploads as repo
from app.schemas.datasets import DatasetFileType
from app.schemas.notifications import NotificationAction
from app.schemas.uploads import CreateUploadRequest, Upload, UploadPhase, UploadStatus
from app.services import billing, folders, ingest_memory as mem, notifications


class UploadRejected(ValueError):
    """A request that never becomes an upload — bad type, or too big."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def file_type_for(filename: str) -> DatasetFileType:
    """Extension-only check, run before a byte has been transferred.

    The real detection is `detect_file_type`, which sniffs magic bytes — but
    that needs the file, and the whole point of presigning is that the API
    never sees it. So this rejects the obviously-unsupported early and the
    pipeline re-checks the content later.
    """
    try:
        return file_type_from_extension(filename)
    except UnsupportedFileType as exc:
        raise UploadRejected("unsupported_file_type", exc.message, 415) from exc


def create_upload(owner_id: str, request: CreateUploadRequest) -> tuple[Upload, str, dict[str, str], datetime]:
    file_type = file_type_for(request.filename)

    # The plan's file size and dataset count (Free: 5 MB, 3 datasets).
    try:
        billing.require_upload_allowance(owner_id, request.sizeBytes)
    except ApiException as exc:
        raise UploadRejected(exc.code, exc.message, exc.status_code) from exc

    # Checked up front rather than left to the FK: `uploads.folder_id` is a real
    # foreign key, so an id that isn't the caller's would surface as a 500 at
    # insert time instead of a 404 the client can act on.
    if request.folderId is not None and not folders.exists(owner_id, request.folderId):
        raise UploadRejected("folder_not_found", "No se encontró la carpeta.", 404)

    if pool.is_configured():
        with session_scope() as session:
            upload = repo.create_upload(
                session, owner_id, request.filename, file_type, request.sizeBytes, request.folderId
            )
    else:
        upload = mem.create_upload(
            owner_id, request.filename, file_type, request.sizeBytes, request.folderId
        )

    key = raw_upload_key(owner_id, upload.id, request.filename)
    url, headers, expires = get_object_store().presigned_put(key, request.contentType)
    return upload, url, headers, expires


def list_uploads(owner_id: str) -> list[Upload]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_uploads(session, owner_id)
    return mem.list_uploads(owner_id)


def get_upload(owner_id: str, upload_id: str) -> Upload | None:
    if pool.is_configured():
        with session_scope() as session:
            upload = repo.get_upload(session, owner_id, upload_id)
    else:
        upload = mem.get_upload(owner_id, upload_id)
    if upload is None:
        return None

    # In the deployed topology stage 1 runs outside the VPC and cannot write the
    # phase to the database, so it publishes progress to `status.json` instead.
    # Merged here rather than in the route, so the stepper works the same in
    # both topologies and the frontend never learns there are two.
    if upload.status is UploadStatus.processing:
        from app.services.ingestion import read_status

        phase = read_status(owner_id, upload_id)
        if phase is not None:
            upload.phase = phase
    return upload


def start_processing(owner_id: str, upload_id: str) -> Upload | None:
    """The bytes are in the store; hand the work to a runner."""
    key = None
    if pool.is_configured():
        with session_scope() as session:
            upload = repo.get_upload(session, owner_id, upload_id)
            if upload is None:
                return None
            key = raw_upload_key(owner_id, upload_id, upload.filename)
            upload = repo.mark_uploaded(session, owner_id, upload_id, key)
    else:
        upload = mem.mark_uploaded(owner_id, upload_id)
    if upload is None:
        return None
    # After the transaction commits, so the worker cannot read a stale row.
    get_job_runner().submit(owner_id, upload_id)
    return upload


def set_phase(owner_id: str, upload_id: str, phase: UploadPhase) -> None:
    if pool.is_configured():
        with session_scope() as session:
            repo.set_phase(session, owner_id, upload_id, phase)
        return
    mem.set_phase(owner_id, upload_id, phase)


def set_detected(owner_id: str, upload_id: str, count: int) -> None:
    if pool.is_configured():
        with session_scope() as session:
            repo.set_detected(session, owner_id, upload_id, count)
        return
    mem.set_detected(owner_id, upload_id, count)


def fail(owner_id: str, upload_id: str, message: str) -> None:
    if pool.is_configured():
        with session_scope() as session:
            repo.fail(session, owner_id, upload_id, message)
    else:
        mem.fail(owner_id, upload_id, message)
    # Ingestion is asynchronous: the user may have left the screen by now.
    upload = get_upload(owner_id, upload_id)
    name = upload.filename if upload else "tu archivo"
    notifications.notify(
        owner_id,
        tone="err",
        title=f"No pudimos procesar {name}",
        body=message,
        action=NotificationAction(label="Reintentar", href="/ingesta"),
    )


def commit(
    owner_id: str, upload_id: str, datasets: list[NewDataset], warnings: list[str]
) -> None:
    """Record every detected table in one transaction. See `repo.commit`."""
    if pool.is_configured():
        with session_scope() as session:
            repo.commit(session, owner_id, upload_id, datasets, warnings)
    else:
        mem.commit(owner_id, upload_id, datasets, warnings)
    # One per upload, however many tables it held. The datasets wait for the
    # user's confirmation, so this is the cue that something needs doing.
    upload = get_upload(owner_id, upload_id)
    name = upload.filename if upload else "Tu archivo"
    count = len(datasets)
    notifications.notify(
        owner_id,
        tone="ok",
        title=f"{name} está listo para revisar",
        body=(
            "Detectamos 1 tabla."
            if count == 1
            else f"Detectamos {count} tablas."
        )
        + " Revisá las columnas y confirmá para empezar a preguntar.",
        action=NotificationAction(label="Revisar y confirmar", href="/ingesta"),
    )


def delete_upload(owner_id: str, upload_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            deleted = repo.delete_upload(session, owner_id, upload_id)
    else:
        deleted = mem.delete_upload(owner_id, upload_id)
    if deleted:
        # Best effort: a raw object that outlives its upload row is noise, not
        # a correctness problem, and the S3 lifecycle rule collects it anyway.
        try:
            get_object_store().delete_prefix(raw_upload_prefix(owner_id, upload_id))
        except OSError:
            pass
    return deleted


def retry_upload(owner_id: str, upload_id: str) -> Upload | None:
    """Re-run a failed upload. `None` when it isn't retryable."""
    if pool.is_configured():
        with session_scope() as session:
            upload = repo.reset_for_retry(session, owner_id, upload_id)
    else:
        upload = mem.reset_for_retry(owner_id, upload_id)
        if upload is not None:
            # The fixture backend has no `storage_key` column to consult, so
            # the object store itself answers "is the raw file still there?".
            key = raw_upload_key(owner_id, upload_id, upload.filename)
            if not get_object_store().exists(key):
                mem.fail(owner_id, upload_id, upload.error or "")
                return None
    if upload is None:
        return None
    get_job_runner().submit(owner_id, upload_id)
    return upload
