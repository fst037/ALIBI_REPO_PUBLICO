"""`library` domain routes: conversation history + saved queries.

All endpoints require a bearer token and operate on the caller's own library
(owner scoping in the repository). Backed by `app/services/library.py`.
"""

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.library import HistoryGroup, SavedQuery, SaveQueryRequest
from app.services import library as store

router = APIRouter(tags=["library"])


@router.get("/history", response_model=list[HistoryGroup], summary="History grouped by recency.")
def list_history(user: User = Depends(require_user)) -> list[HistoryGroup]:
    return store.list_history(user.id)


@router.get("/saved-queries", response_model=list[SavedQuery], summary="Saved queries.")
def list_saved_queries(
    q: str | None = Query(default=None), user: User = Depends(require_user)
) -> list[SavedQuery]:
    return store.list_saved_queries(user.id, q)


@router.post(
    "/saved-queries", response_model=SavedQuery, status_code=201, summary="Save a query."
)
def save_query(request: SaveQueryRequest, user: User = Depends(require_user)) -> SavedQuery:
    return store.save_query(user.id, request)


@router.delete(
    "/saved-queries/{saved_query_id}",
    status_code=204,
    response_model=None,
    summary="Remove a saved query.",
)
def delete_saved_query(
    saved_query_id: str, user: User = Depends(require_user)
) -> Response | JSONResponse:
    if not store.delete_saved_query(user.id, saved_query_id):
        return error_response(404, "not_found", "No se encontró la consulta guardada.")
    return Response(status_code=204)
