"""`conversations` domain routes (chat orchestrator).

All endpoints require a bearer token and operate on the caller's own
conversations (owner scoping in the repository).
`/conversations/suggestions` is declared before `/conversations/{id}` so the
literal path wins over the id parameter.
"""

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.chat import (
    Conversation,
    CreateConversationRequest,
    ExecuteMessageRequest,
    ExecuteMessageResponse,
    PrepareMessageRequest,
    Suggestion,
    UpdateConversationRequest,
)
from app.schemas.runs import Run, StartRunRequest
from app.services import conversations as store
from app.services import runs

router = APIRouter(tags=["conversations"])


@router.get("/conversations", response_model=list[Conversation], summary="List conversations.")
def list_conversations(
    recent: bool = Query(default=False),
    limit: int = Query(default=6, ge=1),
    user: User = Depends(require_user),
) -> list[Conversation]:
    return store.list_conversations(user.id, recent, limit)


@router.post(
    "/conversations", response_model=Conversation, status_code=201, summary="Open a conversation."
)
def create_conversation(
    request: CreateConversationRequest, user: User = Depends(require_user)
) -> Conversation:
    # The whole request, not just `datasetIds`: the source can be a folder, and
    # a multi-dataset selection must keep every id.
    return store.create_conversation(user.id, request)


@router.get(
    "/conversations/suggestions",
    response_model=list[Suggestion],
    summary="Suggested questions.",
)
def get_suggestions(
    datasetId: str | None = Query(default=None), user: User = Depends(require_user)
) -> list[Suggestion]:
    return store.get_suggestions()


@router.get("/conversations/{conversation_id}", response_model=Conversation, summary="Conversation.")
def get_conversation(
    conversation_id: str, user: User = Depends(require_user)
) -> Conversation | JSONResponse:
    conversation = store.get_conversation(user.id, conversation_id)
    if conversation is None:
        return error_response(404, "not_found", "No se encontró la conversación.")
    return conversation


@router.patch(
    "/conversations/{conversation_id}",
    response_model=Conversation,
    summary="Rename a conversation or star it.",
)
def update_conversation(
    conversation_id: str, request: UpdateConversationRequest, user: User = Depends(require_user)
) -> Conversation | JSONResponse:
    conversation = store.update_conversation(user.id, conversation_id, request)
    if conversation is None:
        return error_response(404, "not_found", "No se encontró la conversación.")
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    response_model=None,
    summary="Delete a conversation and its messages.",
)
def delete_conversation(
    conversation_id: str, user: User = Depends(require_user)
) -> Response | JSONResponse:
    if not store.delete_conversation(user.id, conversation_id):
        return error_response(404, "not_found", "No se encontró la conversación.")
    return Response(status_code=204)


@router.post(
    "/conversations/{conversation_id}/messages/prepare",
    response_model=None,
    summary="Generate SQL + guardrail + cost estimate.",
)
def prepare_message(
    conversation_id: str, request: PrepareMessageRequest, user: User = Depends(require_user)
):
    return store.prepare(user.id, conversation_id, request.question, request.sql)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ExecuteMessageResponse,
    status_code=201,
    summary="Execute the confirmed draft.",
)
def execute_message(
    conversation_id: str, request: ExecuteMessageRequest, user: User = Depends(require_user)
) -> ExecuteMessageResponse:
    return store.execute(user.id, conversation_id, request.draftId)


@router.post(
    "/conversations/{conversation_id}/runs",
    response_model=Run,
    status_code=202,
    summary="Start answering a question; poll the returned run.",
)
def start_run(
    conversation_id: str, request: StartRunRequest, user: User = Depends(require_user)
) -> Run:
    """Returns at once with the run `queued`. A worker answers it outside the
    29 s request ceiling; poll `GET …/runs/{runId}` until `done` or `failed`."""
    return runs.start_run(user.id, conversation_id, request)


@router.get(
    "/conversations/{conversation_id}/runs/{run_id}",
    response_model=Run,
    summary="A run's status, its steps so far, and its outcome once finished.",
)
def get_run(conversation_id: str, run_id: str, user: User = Depends(require_user)) -> Run | JSONResponse:
    run = runs.get_run(user.id, conversation_id, run_id)
    if run is None:
        return error_response(404, "not_found", "No se encontró la respuesta en curso.")
    return run
