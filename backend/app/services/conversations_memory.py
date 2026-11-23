"""In-memory conversation/message store: the fallback with no database.

Just the data reads/writes (list/get/create conversations, append + look up
assistant messages); the prepare/execute orchestration lives in the dispatcher
and uses `conversations_engine`. Single-user, so `owner_id` is accepted but
ignored. Seeded from the conversations fixture; grows within a process run.
"""

import secrets
from datetime import datetime, timezone

from app.schemas.chat import (
    AssistantMessage,
    Conversation,
    ConversationSource,
    ConversationSourceType,
    RunMode,
)
from app.services._fixtures import load_json

_conversations: list[Conversation] = [
    Conversation(**c) for c in load_json("conversations.json")
]

# messageId -> AssistantMessage, seeded from the fixture + grown on execute so
# `dashboards.save_chart` and `exports` can reference a message by id.
_assistant_messages: dict[str, AssistantMessage] = {
    m.id: m
    for conv in _conversations
    for m in conv.messages
    if isinstance(m, AssistantMessage)
}


def _resolved(owner_id: str, conversation: Conversation) -> Conversation:
    """Re-read the folder's confirmed datasets, rather than trusting the snapshot.

    A conversation's source is a folder, and a folder is resolved on *every*
    read: a file uploaded into it afterwards joins a conversation already in
    progress, and one removed from it stops being answerable. That is the whole
    reason the folder — not a frozen list of ids — is the unit a conversation
    runs over, so a fixture-mode conversation that snapshotted at creation was
    not a lesser version of the behaviour, it was the opposite of it.

    Imported inside the function: `folders` reaches back into this module's
    sibling stores, and at module scope the two would import each other.
    """
    from app.services import datasets as datasets_store
    from app.services import folders as folders_store

    if conversation.source is None or not conversation.source.id:
        return conversation
    folder = folders_store.get_folder(owner_id, conversation.source.id)
    if folder is None:
        # The folder was deleted. The transcript stays readable — the questions
        # and answers already given are still true — but there is nothing left
        # to query, which `_query_tables` reports when someone asks again.
        return conversation.model_copy(update={"datasetIds": []})
    return conversation.model_copy(
        update={
            "datasetIds": [d.id for d in folder.datasets if datasets_store.is_queryable(d)]
        }
    )


def list_conversations(owner_id: str, recent: bool, limit: int) -> list[Conversation]:
    listed = _conversations[:limit] if recent else _conversations
    return [_resolved(owner_id, c) for c in listed]


def get_conversation(owner_id: str, conversation_id: str) -> Conversation | None:
    found = next((c for c in _conversations if c.id == conversation_id), None)
    return _resolved(owner_id, found) if found is not None else None


def conversation_exists(_owner_id: str, conversation_id: str) -> bool:
    return any(c.id == conversation_id for c in _conversations)


def create_conversation(
    _owner_id: str,
    source_type: ConversationSourceType,
    dataset_ids: list[str],
    source_id: str = "",
    source_name: str = "",
    mode: RunMode = RunMode.quick,
) -> Conversation:
    """Mirror of the repository's create, minus the storage split.

    `dataset_ids` is stored, but it is not what gets read back: `_resolved`
    re-expands the folder on every read, matching the DB path. The stored list
    is only a starting value for a conversation whose folder later disappears.
    """
    conversation = Conversation(
        id="conv_" + secrets.token_urlsafe(6),
        title="New query",
        source=ConversationSource(type=source_type, id=source_id, name=source_name),
        datasetIds=dataset_ids,
        createdAt=datetime.now(timezone.utc).date().isoformat(),
        mode=mode,
        messages=[],
    )
    _conversations.insert(0, conversation)
    return conversation


def set_title(_owner_id: str, conversation_id: str, title: str) -> None:
    stored = _stored(conversation_id)
    if stored is not None:
        stored.title = title


def _stored(conversation_id: str) -> Conversation | None:
    """The mutable entry, not the `_resolved` copy the readers hand out."""
    return next((c for c in _conversations if c.id == conversation_id), None)


def update_conversation(
    owner_id: str,
    conversation_id: str,
    *,
    title: str | None = None,
    favorite: bool | None = None,
    mode: RunMode | None = None,
) -> Conversation | None:
    stored = _stored(conversation_id)
    if stored is None:
        return None
    if title is not None:
        stored.title = title
    if favorite is not None:
        stored.favorite = favorite
    if mode is not None:
        stored.mode = mode
    return _resolved(owner_id, stored)


def delete_conversation(_owner_id: str, conversation_id: str) -> bool:
    stored = _stored(conversation_id)
    if stored is None:
        return False
    _conversations.remove(stored)
    # The by-id index too, or `dashboards`/`exports` would still resolve a
    # message belonging to a conversation that no longer exists.
    for message in stored.messages:
        _assistant_messages.pop(message.id, None)
    return True


def append_assistant_message(_owner_id: str, conversation_id: str, message: AssistantMessage) -> None:
    _assistant_messages[message.id] = message
    # Onto the conversation itself as well, not only the by-id index: reopening
    # a thread from History or the sidebar replays `Conversation.messages`, and
    # without this the canvas would come back empty.
    stored = _stored(conversation_id)
    if stored is not None:
        stored.messages.append(message)


def get_assistant_message(_owner_id: str, message_id: str) -> AssistantMessage | None:
    return _assistant_messages.get(message_id)
