"""Postgres repository for the `folders` domain (Module B).

A folder is a one-level grouping and the link to a dataset is *only a mapping*.
That has two consequences this file has to honour exactly:

* Attaching a dataset to a second folder inserts a `folder_datasets` row. It
  never copies a dataset, and never moves one.
* Deleting a folder deletes its links. The datasets survive and stay reachable
  from the Biblioteca — ownership runs from user to dataset, not through the
  folder.

There is no `parent_id` anywhere, because there are no subfolders.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import DatasetModel, Folder as FolderModel, FolderDataset
from app.repositories import datasets as datasets_repo
from app.schemas.folders import Folder, FolderDetail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _folder(row: FolderModel, count: int) -> Folder:
    return Folder(
        id=row.id,
        name=row.name,
        datasetCount=count,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _count(session: Session, folder_id: str) -> int:
    return int(
        session.exec(
            select(func.count()).select_from(FolderDataset).where(
                FolderDataset.folder_id == folder_id
            )
        ).one()
    )


def list_folders(session: Session, owner_id: str) -> list[Folder]:
    rows = session.exec(
        select(FolderModel).where(FolderModel.owner_id == owner_id).order_by(FolderModel.name)
    ).all()
    if not rows:
        return []
    # One grouped count for the whole page rather than a count per folder.
    counts = dict(
        session.exec(
            select(FolderDataset.folder_id, func.count())
            .where(FolderDataset.folder_id.in_([r.id for r in rows]))
            .group_by(FolderDataset.folder_id)
        ).all()
    )
    return [_folder(row, counts.get(row.id, 0)) for row in rows]


def _get_row(session: Session, owner_id: str, folder_id: str) -> FolderModel | None:
    return session.exec(
        select(FolderModel).where(FolderModel.id == folder_id, FolderModel.owner_id == owner_id)
    ).first()


def get_folder(session: Session, owner_id: str, folder_id: str) -> FolderDetail | None:
    row = _get_row(session, owner_id, folder_id)
    if row is None:
        return None
    datasets = datasets_repo.list_datasets(session, owner_id, folder_id=folder_id)
    return FolderDetail(
        id=row.id,
        name=row.name,
        datasetCount=len(datasets),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        datasets=datasets,
    )


def exists(session: Session, owner_id: str, folder_id: str) -> bool:
    return _get_row(session, owner_id, folder_id) is not None


def name_taken(session: Session, owner_id: str, name: str, *, excluding: str | None = None) -> bool:
    statement = select(FolderModel.id).where(
        FolderModel.owner_id == owner_id, func.lower(FolderModel.name) == name.strip().lower()
    )
    if excluding is not None:
        statement = statement.where(FolderModel.id != excluding)
    return session.exec(statement).first() is not None


def create_folder(session: Session, owner_id: str, name: str, dataset_ids: list[str]) -> Folder:
    row = FolderModel(id=f"fld_{uuid4().hex[:12]}", owner_id=owner_id, name=name.strip())
    session.add(row)
    session.flush()
    attach(session, owner_id, row.id, dataset_ids)
    # Counted rather than trusting len(dataset_ids): `attach` skips ids that
    # aren't this owner's and ids already linked, so the two can differ.
    return _folder(row, _count(session, row.id))


def rename_folder(session: Session, owner_id: str, folder_id: str, name: str) -> Folder | None:
    row = _get_row(session, owner_id, folder_id)
    if row is None:
        return None
    row.name = name.strip()
    row.updated_at = _now()
    session.add(row)
    session.flush()
    return _folder(row, _count(session, folder_id))


def delete_folder(session: Session, owner_id: str, folder_id: str) -> bool:
    row = _get_row(session, owner_id, folder_id)
    if row is None:
        return False
    for link in session.exec(
        select(FolderDataset).where(FolderDataset.folder_id == folder_id)
    ).all():
        session.delete(link)
    session.flush()
    session.delete(row)
    return True


def attach(
    session: Session, owner_id: str, folder_id: str, dataset_ids: list[str]
) -> FolderDetail | None:
    if _get_row(session, owner_id, folder_id) is None:
        return None
    if dataset_ids:
        # Only the caller's own datasets, and only ones not already linked —
        # attaching twice is a no-op, not a primary-key violation.
        owned = set(
            session.exec(
                select(DatasetModel.id).where(
                    DatasetModel.owner_id == owner_id, DatasetModel.id.in_(dataset_ids)
                )
            ).all()
        )
        linked = set(
            session.exec(
                select(FolderDataset.dataset_id).where(
                    FolderDataset.folder_id == folder_id,
                    FolderDataset.dataset_id.in_(dataset_ids),
                )
            ).all()
        )
        for dataset_id in dataset_ids:
            if dataset_id in owned and dataset_id not in linked:
                session.add(FolderDataset(folder_id=folder_id, dataset_id=dataset_id))
        session.flush()
    return get_folder(session, owner_id, folder_id)


def detach(session: Session, owner_id: str, folder_id: str, dataset_id: str) -> bool:
    if _get_row(session, owner_id, folder_id) is None:
        return False
    link = session.exec(
        select(FolderDataset).where(
            FolderDataset.folder_id == folder_id, FolderDataset.dataset_id == dataset_id
        )
    ).first()
    if link is None:
        return False
    # Removes the mapping only. The dataset is untouched.
    session.delete(link)
    return True
