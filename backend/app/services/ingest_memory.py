"""In-memory uploads + folders, the no-database backend for Module B.

The counterpart to `datasets_memory` for the two entities Module B added. It
is used whenever `ALIBI_DATABASE_URL` is unset — the test suite, and a local
run against nothing but a laptop — and it is a real backend, not a stub: the
upload it records is processed by the real pipeline, the Parquet it points at
is really written, and the preview really comes back through DuckDB.

Two deliberate choices:

* **Folder membership lives on `Dataset.folderIds`**, not in a separate link
  map. The two directions (a dataset's folders, a folder's datasets) then
  cannot drift out of sync, because there is only one fact. `folder → datasets`
  is a scan, which is free at fixture scale and is a real indexed join in the
  Postgres repository where it isn't.
* **This module imports `datasets_memory`, never the reverse.** One direction
  only, so there is no import cycle to reason about.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import uuid4

from app.ingest.models import NewDataset
from app.schemas.datasets import Dataset, DatasetStatus
from app.schemas.folders import Folder, FolderDetail
from app.schemas.uploads import Upload, UploadPhase, UploadStatus
from app.services import datasets_memory as datasets_mem

# Jobs run on a background thread while requests read this state, so every
# mutation takes the lock. The DB backend gets the same guarantee from its
# transaction.
_lock = threading.RLock()

_uploads: dict[str, Upload] = {}
_folders: dict[str, Folder] = {}
# Kept out of the wire model: the folder an upload should land in is an
# instruction for commit time, not something the client needs echoed back.
_folder_of: dict[str, str | None] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset() -> None:
    """Drop all Module B fixture state (used between tests)."""
    with _lock:
        _uploads.clear()
        _folders.clear()
        _folder_of.clear()


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def create_upload(
    owner_id: str, filename: str, file_type, size_bytes: int, folder_id: str | None
) -> Upload:
    from app.services.ingestion import human_size

    upload = Upload(
        id=f"up_{uuid4().hex[:12]}",
        filename=filename,
        fileType=file_type,
        sizeBytes=size_bytes,
        sizeLabel=human_size(size_bytes),
        status=UploadStatus.pending,
        createdAt=_now(),
    )
    with _lock:
        _uploads[upload.id] = upload
        _folder_of[upload.id] = folder_id
    return upload


def list_uploads(_owner_id: str) -> list[Upload]:
    with _lock:
        return sorted(_uploads.values(), key=lambda u: u.createdAt, reverse=True)


def get_upload(_owner_id: str, upload_id: str) -> Upload | None:
    return _uploads.get(upload_id)


def mark_uploaded(owner_id: str, upload_id: str) -> Upload | None:
    """The bytes landed; processing is about to start."""
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is None:
            return None
        upload.status = UploadStatus.processing
        upload.phase = UploadPhase.uploading
        upload.error = None
        upload.updatedAt = _now()
        return upload


def set_phase(_owner_id: str, upload_id: str, phase: UploadPhase) -> None:
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is None:
            return
        upload.status = UploadStatus.processing
        upload.phase = phase
        upload.updatedAt = _now()


def set_detected(_owner_id: str, upload_id: str, count: int) -> None:
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is not None:
            upload.detectedTables = count


def fail(_owner_id: str, upload_id: str, message: str) -> None:
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is None:
            return
        upload.status = UploadStatus.error
        upload.phase = None
        upload.error = message
        upload.updatedAt = _now()


def commit(
    owner_id: str, upload_id: str, datasets: list[NewDataset], warnings: list[str]
) -> None:
    """Record the materialised tables and finish the upload.

    Every dataset lands as `awaiting_confirmation`: the table exists and is
    previewable, but nothing may query it until the user has looked at the
    inferred schema (Ingesta 1.2).
    """
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is None:
            return
        folder_id = _folder_of.get(upload_id)
        created: list[Dataset] = []
        for new in datasets:
            dataset = Dataset(
                id=new.id,
                name=new.name,
                fileType=new.file_type,
                meta=new.meta,
                favorite=False,
                recent=True,
                status=DatasetStatus.awaiting_confirmation,
                createdAt=_now().date().isoformat(),
                updatedAt=_now().date().isoformat(),
                tableName=new.table_name,
                uploadId=upload_id,
                sourceLabel=new.source_label,
                folderIds=[folder_id] if folder_id and folder_id in _folders else [],
                rows=new.row_count,
                columns=new.column_count,
                sizeLabel=new.size_label,
            )
            datasets_mem.add_dataset(dataset, new.schema)
            created.append(dataset)

        upload.status = UploadStatus.ready
        upload.phase = UploadPhase.done
        upload.detectedTables = len(datasets)
        upload.datasets = created
        upload.warnings = list(warnings)
        upload.updatedAt = _now()


def delete_upload(owner_id: str, upload_id: str) -> bool:
    """Cancel an upload, discarding only what was never confirmed."""
    with _lock:
        upload = _uploads.pop(upload_id, None)
        _folder_of.pop(upload_id, None)
        if upload is None:
            return False
    for dataset in list(upload.datasets):
        current = datasets_mem.get_dataset(owner_id, dataset.id)
        if current is not None and current.status is DatasetStatus.awaiting_confirmation:
            datasets_mem.delete_dataset(owner_id, dataset.id)
    return True


def reset_for_retry(_owner_id: str, upload_id: str) -> Upload | None:
    with _lock:
        upload = _uploads.get(upload_id)
        if upload is None or upload.status is not UploadStatus.error:
            return None
        upload.status = UploadStatus.processing
        upload.phase = UploadPhase.extracting
        upload.error = None
        upload.updatedAt = _now()
        return upload


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def _counts(owner_id: str) -> dict[str, int]:
    """folder id → how many datasets link to it, in one pass over the library.

    The counterpart of the single grouped query the Postgres repository runs;
    counting per folder would be a scan per folder.
    """
    counts: dict[str, int] = {}
    for dataset in datasets_mem.list_datasets(owner_id):
        for folder_id in dataset.folderIds:
            counts[folder_id] = counts.get(folder_id, 0) + 1
    return counts


def _folder(row: Folder, count: int) -> Folder:
    return Folder(
        id=row.id,
        name=row.name,
        datasetCount=count,
        createdAt=row.createdAt,
        updatedAt=row.updatedAt,
    )


def list_folders(owner_id: str) -> list[Folder]:
    counts = _counts(owner_id)
    with _lock:
        return [
            _folder(f, counts.get(f.id, 0))
            for f in sorted(_folders.values(), key=lambda f: f.name.lower())
        ]


def get_folder(owner_id: str, folder_id: str) -> FolderDetail | None:
    with _lock:
        row = _folders.get(folder_id)
        if row is None:
            return None
        datasets = [d for d in datasets_mem.list_datasets(owner_id) if folder_id in d.folderIds]
        return FolderDetail(
            id=row.id,
            name=row.name,
            datasetCount=len(datasets),
            createdAt=row.createdAt,
            updatedAt=row.updatedAt,
            datasets=datasets,
        )


def exists(_owner_id: str, folder_id: str) -> bool:
    with _lock:
        return folder_id in _folders


def name_taken(_owner_id: str, name: str, *, excluding: str | None = None) -> bool:
    lowered = name.strip().lower()
    with _lock:
        return any(
            f.name.strip().lower() == lowered and f.id != excluding for f in _folders.values()
        )


def create_folder(owner_id: str, name: str, dataset_ids: list[str]) -> Folder:
    folder = Folder(
        id=f"fld_{uuid4().hex[:12]}", name=name.strip(), datasetCount=0, createdAt=_now()
    )
    with _lock:
        _folders[folder.id] = folder
    attach(owner_id, folder.id, dataset_ids)
    # Recounted rather than trusting len(dataset_ids): `attach` skips ids that
    # aren't this owner's, so the two can legitimately differ.
    return _folder(folder, _counts(owner_id).get(folder.id, 0))


def rename_folder(owner_id: str, folder_id: str, name: str) -> Folder | None:
    count = _counts(owner_id).get(folder_id, 0)
    with _lock:
        row = _folders.get(folder_id)
        if row is None:
            return None
        row.name = name.strip()
        row.updatedAt = _now()
        return _folder(row, count)


def delete_folder(owner_id: str, folder_id: str) -> bool:
    """Deletes the folder and its links. Datasets are untouched, by design."""
    with _lock:
        if _folders.pop(folder_id, None) is None:
            return False
    for dataset in datasets_mem.list_datasets(owner_id):
        if folder_id in dataset.folderIds:
            dataset.folderIds.remove(folder_id)
    return True


def attach(owner_id: str, folder_id: str, dataset_ids: list[str]) -> FolderDetail | None:
    """Link datasets into a folder. Idempotent, and never copies anything."""
    with _lock:
        if folder_id not in _folders:
            return None
    for dataset_id in dataset_ids:
        dataset = datasets_mem.get_dataset(owner_id, dataset_id)
        # An id that isn't the caller's dataset is skipped rather than 404ing
        # the whole call: attaching is a bulk operation and one stale id in the
        # list shouldn't lose the other nine.
        if dataset is not None and folder_id not in dataset.folderIds:
            dataset.folderIds.append(folder_id)
    return get_folder(owner_id, folder_id)


def detach(owner_id: str, folder_id: str, dataset_id: str) -> bool:
    with _lock:
        if folder_id not in _folders:
            return False
    dataset = datasets_mem.get_dataset(owner_id, dataset_id)
    if dataset is None or folder_id not in dataset.folderIds:
        return False
    dataset.folderIds.remove(folder_id)
    return True
