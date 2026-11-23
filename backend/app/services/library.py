"""`library` domain service — swap point between storage backends.

Saved queries and conversation history run against the database (owner-scoped)
when configured, else the in-memory fixtures. History is derived from
conversations + messages at read time (see `app.repositories.library.list_history`).
"""

from app.db import pool
from app.db.engine import session_scope
from app.repositories import datasets as datasets_repo
from app.repositories import library as repo
from app.schemas.library import HistoryGroup, SavedQuery, SaveQueryRequest
from app.services import billing
from app.services import library_memory as mem


def list_history(owner_id: str) -> list[HistoryGroup]:
    # Free keeps 7 days of History (`plans.limits.historyDays`); Pro, all of it.
    # Older conversations are hidden, not deleted: upgrading brings them back.
    since = billing.history_since(owner_id)
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_history(session, owner_id, since)
    return mem.list_history(owner_id, since)


def list_saved_queries(owner_id: str, query: str | None) -> list[SavedQuery]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_saved_queries(session, owner_id, query)
    return mem.list_saved_queries(owner_id, query)


def save_query(owner_id: str, request: SaveQueryRequest) -> SavedQuery:
    if pool.is_configured():
        with session_scope() as session:
            dataset = datasets_repo.get_dataset(session, owner_id, request.datasetId)
            dataset_name = dataset.name if dataset else request.datasetId
            return repo.save_query(session, owner_id, request, dataset_name)
    return mem.save_query(owner_id, request)


def delete_saved_query(owner_id: str, saved_query_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.delete_saved_query(session, owner_id, saved_query_id)
    return mem.delete_saved_query(owner_id, saved_query_id)
