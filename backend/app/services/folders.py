"""`folders` domain service — database when configured, fixtures otherwise.

Mirrors `app/services/datasets.py`. The name-collision check is done here
rather than left to the unique constraint so the route can answer 409 with a
sentence the user understands, instead of surfacing an IntegrityError.
"""

from app.db import pool
from app.db.engine import session_scope
from app.repositories import folders as repo
from app.schemas.folders import Folder, FolderDetail
from app.services import ingest_memory as mem


def list_folders(owner_id: str) -> list[Folder]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_folders(session, owner_id)
    return mem.list_folders(owner_id)


def get_folder(owner_id: str, folder_id: str) -> FolderDetail | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_folder(session, owner_id, folder_id)
    return mem.get_folder(owner_id, folder_id)


def exists(owner_id: str, folder_id: str) -> bool:
    """Whether this folder is the caller's. Checked before anything stores a
    reference to it, so a bad id is a 404 rather than an FK violation."""
    if pool.is_configured():
        with session_scope() as session:
            return repo.exists(session, owner_id, folder_id)
    return mem.exists(owner_id, folder_id)


def name_taken(owner_id: str, name: str, *, excluding: str | None = None) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.name_taken(session, owner_id, name, excluding=excluding)
    return mem.name_taken(owner_id, name, excluding=excluding)


def create_folder(owner_id: str, name: str, dataset_ids: list[str]) -> Folder:
    if pool.is_configured():
        with session_scope() as session:
            return repo.create_folder(session, owner_id, name, dataset_ids)
    return mem.create_folder(owner_id, name, dataset_ids)


def rename_folder(owner_id: str, folder_id: str, name: str) -> Folder | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.rename_folder(session, owner_id, folder_id, name)
    return mem.rename_folder(owner_id, folder_id, name)


def delete_folder(owner_id: str, folder_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.delete_folder(session, owner_id, folder_id)
    return mem.delete_folder(owner_id, folder_id)


def attach(owner_id: str, folder_id: str, dataset_ids: list[str]) -> FolderDetail | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.attach(session, owner_id, folder_id, dataset_ids)
    return mem.attach(owner_id, folder_id, dataset_ids)


def detach(owner_id: str, folder_id: str, dataset_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.detach(session, owner_id, folder_id, dataset_id)
    return mem.detach(owner_id, folder_id, dataset_id)
