"""An answer run: one question, answered asynchronously, step by step.

A question can take longer than one API Gateway request allows (29 s) — and with
the agent harness it routinely will, because a
Deep answer runs several queries before a bigger model writes the synthesis. So
asking is two calls: `POST /conversations/{id}/runs` returns at once with a run
in `queued`, a worker executes it, and the client polls `GET …/runs/{runId}`,
rendering `steps` as they land.

A run ends in exactly one of three ways:

* `done` with `message` — an answer, appended to the conversation like any other.
* `done` with `guard` — the request was refused (a stated intent to write, or SQL
  the guardrail rejected). A guard is an answer too, not a failure.
* `failed` with `error` — the same `code`/`message` pair the old synchronous
  endpoints answered with as an HTTP error (`text2sql_unavailable`,
  `narrative_unavailable`, a query error …), so the client explains it the same way.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.schemas.chat import AssistantMessage, CostReceipt, GuardResult, RunMode


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class RunStepStatus(str, Enum):
    running = "running"
    done = "done"
    failed = "failed"


class RunStepPurpose(str, Enum):
    """Why a query was run (the multi-chart rule).

    `context` steps exist so the model understands the data (counts, ranges,
    distinct values) and stay in "How I got here"; `insight` steps are findings
    the user should see, with their chart and rows."""

    context = "context"
    insight = "insight"


class RunStepTool(str, Enum):
    """What a step called, so the user sees which part of ALIBI did the work.

    `sql` is Module A writing a query (then the guardrail and DuckDB run it);
    `peek` / `describe` are the planner reading a result it already holds;
    `chart` is Module C. Steps with no tool are the planner thinking or the
    writer composing the answer."""

    sql = "sql"
    peek = "peek"
    describe = "describe"
    chart = "chart"


class RunStep(BaseModel):
    id: str
    #: What the step is doing, in the user's language — shown while it runs.
    label: str
    status: RunStepStatus = RunStepStatus.running
    purpose: RunStepPurpose | None = None
    #: The question this step put to the data, as the planner phrased it for
    #: Module A. Set on the agent harness's query steps.
    question: str | None = None
    #: The planner's BIRD-style hints sent with that question to Module A, in
    #: the `-- External knowledge:` slot its training data used.
    hints: str | None = None
    #: Why the planner took this step — its own one-sentence note, in the
    #: user's language, shown under the label while the run is live.
    thought: str | None = None
    #: What the step called, and who served it ("modelo ajustado de ALIBI…" or
    #: a Bedrock model id) — what a thought led to, shown beside it.
    tool: RunStepTool | None = None
    model: str | None = None
    #: The outcome of a step that is not a query, in the user's language — the
    #: chart Module C drew, or why it drew none.
    detail: str | None = None
    sql: str | None = None
    rowCount: int | None = None
    error: str | None = None
    startedAt: str
    finishedAt: str | None = None


class RunError(BaseModel):
    code: str
    message: str


class Run(BaseModel):
    id: str
    conversationId: str
    #: What the user asked — the question, or the SQL they wrote by hand.
    question: str
    #: Set when `question` is hand-written SQL rather than natural language: it
    #: then goes straight to the guardrail instead of to Module A.
    isSql: bool = False
    mode: RunMode = RunMode.quick
    status: RunStatus = RunStatus.queued
    steps: list[RunStep] = Field(default_factory=list)
    message: AssistantMessage | None = None
    guard: GuardResult | None = None
    receipt: CostReceipt | None = None
    error: RunError | None = None
    createdAt: str
    updatedAt: str


class StartRunRequest(BaseModel):
    """A natural-language question, or hand-written SQL — exactly one."""

    question: str | None = Field(default=None, min_length=1)
    sql: str | None = Field(default=None, min_length=1)
    #: This question only. Omitted, the conversation's own `mode` — so a client
    #: overrides one question without changing the conversation's default.
    mode: RunMode | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> StartRunRequest:
        if (self.question is None) == (self.sql is None):
            raise ValueError("send exactly one of `question` or `sql`")
        return self
