"""In-memory library store: the fallback when no database is configured.

Conversation history + saved queries, seeded from fixtures. Single-user, so the
`owner_id` the service passes is accepted but ignored here. Used by tests and a
no-database local run.
"""

import secrets
from datetime import date, datetime, timezone

from app.schemas.charts import ChartType
from app.schemas.chat import AssistantMessage, Conversation
from app.schemas.library import HistoryGroup, HistoryItem, SavedQuery, SaveQueryRequest
from app.services import conversations_memory, datasets_memory
from app.services._fixtures import load_json

_saved_queries: list[SavedQuery] = [SavedQuery(**q) for q in load_json("savedQueries.json")]


def _time_label(created_at: str, today: date) -> str:
    try:
        days = (today - date.fromisoformat(created_at)).days
    except ValueError:
        return created_at
    if days <= 0:
        return "Hoy"
    if days == 1:
        return "Ayer"
    if days < 7:
        return f"hace {days} d"
    return f"{days // 7} sem"


def _bucket(created_at: str, today: date) -> str:
    try:
        when = date.fromisoformat(created_at)
    except ValueError:
        return "Anteriores"
    if when == today:
        return "Hoy"
    return "Esta semana" if (today - when).days < 7 else "Anteriores"


def _on_or_after(created_at: str, since: datetime) -> bool:
    try:
        when = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= since


def _history_item(conversation: Conversation, today: date) -> HistoryItem:
    answers = [m for m in conversation.messages if isinstance(m, AssistantMessage)]
    return HistoryItem(
        id=conversation.id,
        title=conversation.title,
        source=conversation.source.name if conversation.source else "Carpeta",
        questionCount=len(answers),
        time=_time_label(conversation.createdAt, today),
        # Always the table icon: an answer carries no chart any more — Module C
        # will choose one seeing the data itself (`QueryResult`'s docstring), and
        # nothing in between should guess on its behalf.
        chartType=ChartType.table,
        favorite=conversation.favorite,
    )


def list_history(owner_id: str, since: datetime | None = None) -> list[HistoryGroup]:
    """History derived from the conversations themselves, not a separate fixture.

    It used to come from `history.json`, whose ids (`h_1`…`h_7`) matched no
    conversation — so every row on the History screen opened a blank canvas.
    Deriving it here means a row always points at something that can be
    reopened, exactly like the database path does.

    Conversations with no folder source are skipped: those are the pre-folder
    seeds in `conversations.json`, which nothing can reopen or re-query under
    the folder-only contract.
    """
    today = date.today()
    groups: dict[str, list[HistoryItem]] = {"Hoy": [], "Esta semana": [], "Anteriores": []}
    for conversation in conversations_memory.list_conversations(owner_id, False, 0):
        if conversation.source is None or not conversation.source.id:
            continue
        if since is not None and not _on_or_after(conversation.createdAt, since):
            continue
        groups[_bucket(conversation.createdAt, today)].append(_history_item(conversation, today))
    return [HistoryGroup(date=label, items=items) for label, items in groups.items() if items]


def list_saved_queries(_owner_id: str, query: str | None) -> list[SavedQuery]:
    if not query:
        return _saved_queries
    needle = query.lower()
    return [
        q
        for q in _saved_queries
        if needle in q.question.lower() or needle in q.datasetName.lower()
    ]


def save_query(_owner_id: str, request: SaveQueryRequest) -> SavedQuery:
    dataset = datasets_memory.get_dataset("", request.datasetId)
    dataset_name = dataset.name if dataset else request.datasetId
    saved = SavedQuery(
        id="sq_" + secrets.token_urlsafe(6),
        question=request.question,
        datasetId=request.datasetId,
        datasetName=dataset_name,
        date=date.today().isoformat(),
        chartType=request.chartType,
    )
    _saved_queries.insert(0, saved)
    return saved


def delete_saved_query(_owner_id: str, saved_query_id: str) -> bool:
    target = next((q for q in _saved_queries if q.id == saved_query_id), None)
    if target is None:
        return False
    _saved_queries.remove(target)
    return True
