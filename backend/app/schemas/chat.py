"""Pydantic models for the `chat` domain (conversations tag).

Conform to `contracts/chat.yaml`: conversations, the two-phase prepare/execute
message cycle, suggestions, and the read-only guardrail result.
"""

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.schemas.charts import QueryResult


class UserMessage(BaseModel):
    id: str
    role: Literal["user"] = "user"
    text: str
    at: str


class AssistantMessage(BaseModel):
    id: str
    role: Literal["assistant"] = "assistant"
    question: str
    result: QueryResult
    at: str


class GuardMessage(BaseModel):
    id: str
    role: Literal["guard"] = "guard"
    question: str
    at: str


class SystemMessage(BaseModel):
    id: str
    role: Literal["system"] = "system"
    text: str
    at: str


Message = Annotated[
    Union[UserMessage, AssistantMessage, GuardMessage, SystemMessage],
    Field(discriminator="role"),
]


class RunMode(str, Enum):
    """Quick or Deep: the agent harness's budget (few steps, the
    Haiku synthesis) or a longer one (more steps, the Sonnet synthesis).

    A conversation holds its default; a run may override it for one question."""

    quick = "quick"
    deep = "deep"


class ConversationSourceType(str, Enum):
    """What a conversation runs over. **Always a folder.**

    `dataset` (one file) and `datasets` (an explicit multi-selection) were
    removed: the scope Module A is given is now exactly one folder the user
    chose, never a single table and never everything they own. A question about
    one file means a folder containing one file.

    The folder is the right unit because it is the only one that is *resolved on
    every read*. An explicit dataset list is frozen when the conversation opens,
    so a file uploaded into the working set later stayed invisible to a
    conversation already in progress — which is the opposite of what a user
    expects after dropping a new export into the folder they are asking about.
    """

    folder = "folder"


class ConversationSource(BaseModel):
    type: ConversationSourceType = ConversationSourceType.folder
    id: str = ""
    name: str = ""


class Conversation(BaseModel):
    id: str
    title: str
    source: ConversationSource | None = None
    #: Every table Module A is given, not just the first one.
    datasetIds: list[str]
    createdAt: str
    #: Starred by the user from the History screen.
    favorite: bool = False
    #: How far the next questions are investigated, unless one says otherwise.
    mode: RunMode = RunMode.quick
    messages: list[Message]


class UpdateConversationRequest(BaseModel):
    """Partial patch — at least one of `title`, `favorite` or `mode`.

    An empty body is refused rather than counted as an update that did nothing:
    the caller would get a 200 and no change, which is indistinguishable from a
    star that failed to stick.
    """

    title: str | None = None
    favorite: bool | None = None
    mode: RunMode | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "UpdateConversationRequest":
        if self.title is None and self.favorite is None and self.mode is None:
            raise ValueError("provide at least one of 'title', 'favorite' or 'mode'")
        if self.title is not None and not self.title.strip():
            raise ValueError("'title' cannot be blank")
        return self


class CreateConversationRequest(BaseModel):
    """Open a conversation over one folder.

    `folderId` is the only input. `sourceType`/`sourceId` are accepted as
    aliases so an older client keeps working, but a request naming datasets
    directly is now refused rather than silently reinterpreted — the scope a
    question is answered against is the thing most worth being unambiguous
    about.
    """

    folderId: str | None = None
    sourceType: ConversationSourceType | None = None
    sourceId: str | None = None
    #: The conversation's default depth; Quick when omitted.
    mode: RunMode = RunMode.quick

    @model_validator(mode="after")
    def _check_source(self) -> "CreateConversationRequest":
        if not self.resolved_folder_id():
            raise ValueError("provide folderId")
        return self

    def resolved_folder_id(self) -> str:
        return (self.folderId or self.sourceId or "").strip()


class SuggestionIcon(str, Enum):
    trend = "trend"
    calendar = "calendar"
    percent = "percent"
    compare = "compare"


class Suggestion(BaseModel):
    icon: SuggestionIcon
    text: str


# ---- Prepare / execute ----


class PrepareMessageRequest(BaseModel):
    """Exactly one of `question` (natural language) or `sql` (hand-edited)."""

    question: str | None = Field(default=None, min_length=1)
    sql: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> "PrepareMessageRequest":
        if (self.question is None) == (self.sql is None):
            raise ValueError("provide exactly one of 'question' or 'sql'")
        return self


class CostEstimate(BaseModel):
    available: bool
    estimatedRows: int | None = None
    usd: float | None = None
    ars: float | None = None


class PreparedMessage(BaseModel):
    status: Literal["prepared"] = "prepared"
    draftId: str
    sql: str
    estimate: CostEstimate


class GuardResult(BaseModel):
    status: Literal["blocked"] = "blocked"
    question: str
    message: str


PrepareResult = Annotated[Union[PreparedMessage, GuardResult], Field(discriminator="status")]


class ExecuteMessageRequest(BaseModel):
    draftId: str


class CostReceipt(BaseModel):
    rows: int
    usd: float
    ars: float


class ExecuteMessageResponse(BaseModel):
    message: AssistantMessage
    receipt: CostReceipt
