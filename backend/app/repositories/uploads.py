"""Postgres repository for the `uploads` domain (Module B).

An upload is the *file*; a dataset is a *table found inside it*. One upload can
yield several, which is why these are separate entities and why the commit
below is a single transaction: a half-recorded three-table spreadsheet would
leave datasets nobody can trace back to a source.

Every query is scoped to `owner_id`, same rule as everywhere else.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from app.db.models import DatasetModel, Upload as UploadModel
from app.ingest.models import NewDataset
from app.repositories import datasets as datasets_repo
from app.schemas.datasets import Dataset, DatasetFileType, DatasetStatus
from app.schemas.uploads import Upload, UploadPhase, UploadStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _upload(row: UploadModel, datasets: list[Dataset] | None = None) -> Upload:
    from app.services.ingestion import human_size

    return Upload(
        id=row.id,
        filename=row.original_filename,
        fileType=row.file_type,
        sizeBytes=row.size_bytes,
        sizeLabel=human_size(row.size_bytes),
        status=row.status,
        phase=row.phase,
        detectedTables=row.detected_tables,
        datasets=datasets or [],
        warnings=list(row.warnings or []),
        error=row.error,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _datasets_of(session: Session, owner_id: str, upload_id: str) -> list[Dataset]:
    rows = session.exec(
        select(DatasetModel).where(
            DatasetModel.owner_id == owner_id, DatasetModel.upload_id == upload_id
        )
    ).all()
    folders = datasets_repo.folder_map(session, [r.id for r in rows])
    return [datasets_repo.to_dataset(row, folders.get(row.id)) for row in rows]


def list_uploads(session: Session, owner_id: str) -> list[Upload]:
    rows = session.exec(
        select(UploadModel)
        .where(UploadModel.owner_id == owner_id)
        .order_by(UploadModel.created_at.desc())
    ).all()
    return [_upload(row, _datasets_of(session, owner_id, row.id)) for row in rows]


def get_upload(session: Session, owner_id: str, upload_id: str) -> Upload | None:
    row = session.exec(
        select(UploadModel).where(UploadModel.id == upload_id, UploadModel.owner_id == owner_id)
    ).first()
    if row is None:
        return None
    return _upload(row, _datasets_of(session, owner_id, upload_id))


def create_upload(
    session: Session,
    owner_id: str,
    filename: str,
    file_type: DatasetFileType,
    size_bytes: int,
    folder_id: str | None,
) -> Upload:
    row = UploadModel(
        id=f"up_{uuid4().hex[:12]}",
        owner_id=owner_id,
        original_filename=filename,
        file_type=file_type,
        size_bytes=size_bytes,
        status=UploadStatus.pending,
        folder_id=folder_id,
    )
    session.add(row)
    session.flush()
    return _upload(row)


def _get_row(session: Session, owner_id: str, upload_id: str) -> UploadModel | None:
    return session.exec(
        select(UploadModel).where(UploadModel.id == upload_id, UploadModel.owner_id == owner_id)
    ).first()


def mark_uploaded(session: Session, owner_id: str, upload_id: str, storage_key: str) -> Upload | None:
    row = _get_row(session, owner_id, upload_id)
    if row is None:
        return None
    row.status = UploadStatus.processing
    row.phase = UploadPhase.uploading
    row.storage_key = storage_key
    row.error = None
    row.updated_at = _now()
    session.add(row)
    session.flush()
    return _upload(row)


def set_phase(session: Session, owner_id: str, upload_id: str, phase: UploadPhase) -> None:
    row = _get_row(session, owner_id, upload_id)
    if row is None:
        return
    row.status = UploadStatus.processing
    row.phase = phase
    row.updated_at = _now()
    session.add(row)


def set_detected(session: Session, owner_id: str, upload_id: str, count: int) -> None:
    row = _get_row(session, owner_id, upload_id)
    if row is not None:
        row.detected_tables = count
        session.add(row)


def fail(session: Session, owner_id: str, upload_id: str, message: str) -> None:
    row = _get_row(session, owner_id, upload_id)
    if row is None:
        return
    row.status = UploadStatus.error
    row.phase = None
    row.error = message
    row.updated_at = _now()
    # storage_key stays set: the raw object survives a failure (7-day S3
    # lifecycle) precisely so `retry` has something to re-read.
    session.add(row)


def commit(
    session: Session,
    owner_id: str,
    upload_id: str,
    datasets: list[NewDataset],
    warnings: list[str],
) -> None:
    row = _get_row(session, owner_id, upload_id)
    if row is None:
        return
    for new in datasets:
        datasets_repo.insert_dataset(session, owner_id, upload_id, new, row.folder_id)
    row.status = UploadStatus.ready
    row.phase = UploadPhase.done
    row.detected_tables = len(datasets)
    row.warnings = list(warnings)
    # The raw document is deleted by the caller once this transaction commits;
    # dropping the pointer here is the record that we no longer hold the file.
    row.storage_key = None
    row.updated_at = _now()
    session.add(row)
    session.flush()


def delete_upload(session: Session, owner_id: str, upload_id: str) -> bool:
    row = _get_row(session, owner_id, upload_id)
    if row is None:
        return False
    # One pass over the upload's datasets, split by whether the user confirmed
    # them. An unconfirmed one is discarded with the upload; a confirmed one is
    # theirs now, independent of the file it happened to come from — it only
    # loses the FK to the row about to vanish.
    for dataset in session.exec(
        select(DatasetModel).where(
            DatasetModel.owner_id == owner_id, DatasetModel.upload_id == upload_id
        )
    ).all():
        if dataset.status is DatasetStatus.awaiting_confirmation:
            datasets_repo.delete_dataset(session, owner_id, dataset.id)
        else:
            dataset.upload_id = None
            session.add(dataset)
    session.flush()
    session.delete(row)
    return True


def reset_for_retry(session: Session, owner_id: str, upload_id: str) -> Upload | None:
    row = _get_row(session, owner_id, upload_id)
    if row is None or row.status is not UploadStatus.error or not row.storage_key:
        return None
    row.status = UploadStatus.processing
    row.phase = UploadPhase.extracting
    row.error = None
    row.updated_at = _now()
    session.add(row)
    session.flush()
    return _upload(row)
