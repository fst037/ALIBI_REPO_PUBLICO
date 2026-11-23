"""Backend-independent chat orchestration for the `conversations` domain.

The pieces of the prepare/execute cycle that don't depend on where data lives:
the intent guardrail, the result envelope, the transient draft store, and the
static suggestions. Both the in-memory and the database paths share these; only
the actual reads/writes of conversations and messages differ between backends.

**There is no fixture path any more.** This module used to carry a canned
`QueryResult` loaded from `conversations.json` — canned SQL, canned rows, a
canned chart — served whenever Module A was unconfigured or a dataset had no
materialised Parquet. That is gone. An answer is now always a real query over
real rows narrated by a real model, or it is an error. The reasoning: a canned result is visually indistinguishable from a real
one, so the failure mode was a user confidently reading demo numbers as their
own. Local and deployed now differ only in which URL they point at.

What remains canned is `suggestions()` — static reference copy for the empty
chat screen, not an answer to anything.

Drafts are held in memory: they live only between a `prepare` and its `execute`
(seconds), so they aren't persisted. In a multi-process deployment a draft made
in one process wouldn't be visible in another — acceptable at this stage; revisit
if prepare/execute must survive across processes.
"""

import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.charts import ChartSpec, Finding, QueryResult, ResultColumn, ResultTable, TrailStep
from app.schemas.chat import (
    AssistantMessage,
    CostEstimate,
    CostReceipt,
    GuardResult,
    PreparedMessage,
    Suggestion,
)
from app.services._fixtures import load_json
from app.services.query import QueryResultRows, is_numeric_column

# Write/DDL intent in the user's *phrasing* (not a security control — the real
# boundary is `text2sql.sql.check`, which inspects the generated SQL). The
# Spanish stems are kept deliberately: the product requires English questions,
# but a user who ignores that and types a destructive request in Spanish is
# still worth refusing before a model call.
#
# **Matched as a command, not as a word anywhere in the sentence.** The previous
# pattern was a bare `\b(...|update|modific|insert|...)` stem search, which
# refused a large class of perfectly ordinary read questions — every one of
# these was blocked with "This query was blocked because it modifies data":
#
#     "How many orders were updated last month?"
#     "Which products had modifications in Q3?"
#     "Insert-only customers by cohort"
#
# Those are questions *about* records that were changed, which is exactly the
# sort of thing a BI user asks, and the refusal is unarguable from their side.
# The guard is a product courtesy — refusing a stated intent before spending a
# model call — so a false positive costs a question the user was entitled to
# ask, while a false negative costs one model call and is then caught by the
# real guardrail anyway. Hence: the mutation verb must be used as an imperative.
_GUARD_PATTERN = re.compile(
    r"""
    (?:^|[.;!?]\s*)                      # start of the request, or a new clause
    (?:\s*(?:please|pls|can\s+you|could\s+you|i\s+(?:want|need)\s+to|
            you\s+(?:should|must)|now|then|and)\s+)*
    (?:borr\w*|elimin\w*|delete|drop|update|insert|modific\w*|actualiz\w*|
       truncate|remove|wipe|erase|alter)\b
    (?!-)                                # "Insert-only customers" is an adjective
    |
    \b(?:drop|truncate|alter)\s+table\b  # explicit DDL, wherever it appears
    |
    \bdelete\s+from\b
    |
    \binsert\s+into\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_GUARD_MESSAGE = (
    "Esta consulta se bloqueó porque modifica datos. ALIBI es de solo lectura."
)

_suggestions = [Suggestion(**s) for s in load_json("suggestions.json")]


@dataclass(frozen=True)
class Draft:
    """A prepared query awaiting the user's confirmation.

    Holds the SQL, not just the question: `execute` must run exactly what the
    user saw and approved. Regenerating from the question at execute time would
    let a second model call produce different SQL than the one confirmed.
    """

    question: str
    sql: str
    #: The user's natural-language question, when there was one. Empty on the
    #: hand-edited-SQL path, where `question` holds the query itself so the
    #: chat can render the user's turn. The two are separate because the
    #: narrative node must never be shown SQL — see `make_sql_draft`.
    nl_question: str = ""
    #: For eviction only; see `_prune_drafts`.
    created_at: float = 0.0
    #: The model calls that wrote this SQL (`usage_meter.ModelCall`s). Carried
    #: to `execute`, which bills the whole question in one ledger row — on the
    #: synchronous path prepare and execute are two different requests.
    usage: tuple = ()


#: How long a draft is worth keeping. A draft lives between a `prepare` and its
#: `execute` — seconds of user think-time — so anything this old was abandoned.
_DRAFT_TTL_SECONDS = 30 * 60
#: Hard ceiling, in case a burst arrives faster than the TTL retires it.
_MAX_DRAFTS = 512

# draftId -> prepared draft, awaiting execute.
_drafts: dict[str, Draft] = {}


def _prune_drafts() -> None:
    """Drop abandoned drafts.

    Every `prepare` writes an entry; only an `execute` removes one. Users
    abandon prepared queries constantly — they rephrase, they edit the SQL, they
    close the tab — so without this the dict only ever grows, for the lifetime of
    the process. On a warm Lambda container or a long-running uvicorn that is an
    unbounded leak holding every question and every generated query in memory.

    Insertion order is creation order (dicts preserve it and drafts are never
    reinserted), so the oldest are simply the first keys.
    """
    now = time.monotonic()
    for draft_id, draft in list(_drafts.items()):
        if now - draft.created_at > _DRAFT_TTL_SECONDS:
            del _drafts[draft_id]
    while len(_drafts) > _MAX_DRAFTS:
        del _drafts[next(iter(_drafts))]


def suggestions() -> list[Suggestion]:
    return _suggestions


# Placeholder a conversation is opened with, before anything has been asked in
# it. `title_from_question` replaces it on the first answered question, which is
# what keeps the sidebar's "Recent" list from being a column of identical
# "New query" rows.
DEFAULT_TITLE = "New query"

_TITLE_MAX = 60


def title_from_question(question: str) -> str:
    """A conversation title derived from its first answered question.

    Collapsed to one line and cut on a word boundary — the sidebar renders it in
    a ~200px column, so a title that only differs past the ellipsis is no more
    useful than "New query" was. Empty input keeps the placeholder rather than
    producing a blank row.
    """
    title = " ".join(question.split())
    if not title:
        return DEFAULT_TITLE
    if len(title) <= _TITLE_MAX:
        return title
    cut = title[:_TITLE_MAX].rsplit(" ", 1)[0] or title[:_TITLE_MAX]
    return cut.rstrip(" ,;:.") + "…"


def is_write(text: str) -> bool:
    return bool(_GUARD_PATTERN.search(text))


def blocked(question: str, message: str | None = None) -> GuardResult:
    """A refusal the UI renders as a guard message.

    `message` carries the specific reason when the AST guardrail rejected
    generated SQL (unknown table, more than one statement, ...); it falls back to
    the generic write-attempt copy for the phrasing-level check.
    """
    return GuardResult(question=question, message=message or _GUARD_MESSAGE)


def _store(draft: Draft, *, estimated_rows: int) -> PreparedMessage:
    draft_id = "draft_" + secrets.token_urlsafe(8)
    _drafts[draft_id] = draft
    # After the insert, so the cap is the cap rather than the cap plus one.
    _prune_drafts()
    # The row estimate and the money are placeholders: nothing measures either
    # yet. `available=False` says so, rather than showing a confident 0.01 that
    # was never computed.
    estimate = CostEstimate(available=False, estimatedRows=estimated_rows, usd=0.0, ars=0.0)
    return PreparedMessage(draftId=draft_id, sql=draft.sql, estimate=estimate)


def make_draft(question: str, sql: str, usage: tuple = ()) -> PreparedMessage:
    """Hold a prepared query, built from the user's question, until they confirm it.

    There is no longer an `executable` flag. Every draft that reaches here was
    generated by Module A or hand-written by the user, and both are scoped to the
    conversation's own tables by the AST guardrail — so every draft runs for
    real. The flag existed only to mark the fixture's template SQL, which named
    the demo's tables and could never have executed.
    """
    return _store(
        Draft(question=question, sql=sql, nl_question=question, created_at=time.monotonic(), usage=usage),
        estimated_rows=0,
    )


def make_sql_draft(raw_sql: str, checked_sql: str) -> PreparedMessage:
    """Hold a query the user wrote themselves.

    `question` carries the SQL as typed, because that is what the chat shows as
    the user's turn — there is no question to show. `nl_question` stays empty,
    and that distinction is the whole point of this being a separate
    constructor: it used to go through `make_draft`, which meant the SQL landed
    in the question slot and was handed to `explain_result` as "The user
    asked: SELECT …".

    That broke two guarantees at once. The narrative node is built so the model
    never sees SQL (`ExplainInput.sql_query` is carried for traceability and
    deliberately kept out of the prompt) — showing it a JOIN is the surest way
    to get one mentioned in a sentence meant to contain no technical vocabulary
    at all. And the grounding validator treats numbers in the question as
    citable, so every literal in the user's `WHERE` clause silently became a
    figure the narrative was allowed to claim.
    """
    return _store(
        Draft(question=raw_sql, sql=checked_sql, nl_question="", created_at=time.monotonic()),
        estimated_rows=0,
    )


def pop_draft(draft_id: str) -> Draft | None:
    return _drafts.pop(draft_id, None)


def result_table(rows: list[dict], columns: list[str]) -> ResultTable:
    """A DuckDB result as the chat's `ResultTable`.

    `ResultColumn.label` is the raw column name: it is whatever the SELECT
    produced — often an alias the model chose, or an expression — so there is no
    prettier name available that would still match what the SQL says.

    Nulls become `"—"` because `ResultTable.rows` holds strings or numbers, not
    `None`; that is the same glyph the frontend's `formatCell` renders for an
    absent value, so an empty cell reads identically whichever side produced it.
    """
    return ResultTable(
        columns=[
            ResultColumn(
                key=name,
                label=name,
                align="num" if is_numeric_column(rows, name) else "left",
            )
            for name in columns
        ],
        rows=[{k: ("—" if v is None else v) for k, v in row.items()} for row in rows],
    )


def build_assistant_message(
    question: str,
    sql: str,
    rows: QueryResultRows,
    narrative: str,
    chart: ChartSpec | None = None,
) -> tuple[AssistantMessage, CostReceipt]:
    """Wrap a real, executed, narrated result for the chat transcript.

    The first four arguments are required. There is no partially-real message:
    the SQL ran, the rows are the user's own, and the sentence was written about
    them and verified against them. A caller that cannot supply all four has
    nothing to put in the transcript and should be raising instead.

    `chart` is the exception, and it defaults to `None` rather than being
    required, because absent is a legitimate state for it: Module C returns no
    chart when none helps, and the answer is complete without one. See
    `QueryResult.chart`.
    """
    result = QueryResult(
        naturalResponse=narrative,
        sql=sql,
        table=result_table(rows.rows, rows.columns),
        rowCount=len(rows.rows),
        rowsLabel=rows_label(rows),
        chart=chart,
    )
    message = AssistantMessage(
        id="m_" + secrets.token_urlsafe(6),
        question=question,
        result=result,
        at=datetime.now(timezone.utc).isoformat(),
    )
    # Like the estimate above, the receipt carries the row count; tokens are not
    # metered per message.
    receipt = CostReceipt(rows=result.rowCount, usd=0.0, ars=0.0)
    return message, receipt


def rows_label(rows: QueryResultRows) -> str:
    return f"{len(rows.rows)} filas" + (" (truncado)" if rows.truncated else "")


@dataclass(frozen=True)
class FindingParts:
    """One finding of an investigation, before it becomes a `Finding`."""

    text: str
    question: str
    sql: str
    rows: QueryResultRows
    chart: ChartSpec | None


def build_investigation_message(
    question: str,
    summary: str,
    findings: list[FindingParts],
    trail: list[TrailStep],
) -> tuple[AssistantMessage, CostReceipt]:
    """An agent-harness answer: the summary, its findings, and how it got there.

    The lead finding fills the top-level fields, exactly as a one-query answer
    does, so a single-finding investigation is indistinguishable in shape from
    one. `findings` is filled only when there are two or more (see
    `QueryResult.findings`).
    """
    lead = findings[0]
    message, receipt = build_assistant_message(question, lead.sql, lead.rows, summary, lead.chart)
    if len(findings) > 1:
        message.result.findings = [
            Finding(
                text=f.text,
                question=f.question,
                sql=f.sql,
                table=result_table(f.rows.rows, f.rows.columns),
                rowCount=len(f.rows.rows),
                rowsLabel=rows_label(f.rows),
                chart=f.chart,
            )
            for f in findings
        ]
    message.result.trail = trail
    return message, receipt


__all__ = [
    "Draft",
    "blocked",
    "build_assistant_message",
    "build_investigation_message",
    "FindingParts",
    "rows_label",
    "is_write",
    "make_draft",
    "make_sql_draft",
    "pop_draft",
    "result_table",
    "suggestions",
]
