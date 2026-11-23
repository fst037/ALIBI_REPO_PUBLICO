"""Prompt formats — the ONLY model-specific part of Module A.

A fine-tuned model answers well only when the prompt at inference is byte-for-byte
the shape it saw during training. That shape is a property of the *training data*,
not of the base model, so it has to be pinned per fine-tune and swapped as a unit
when the model is retrained.

Formats are registered by name and selected with `ALIBI_TEXT2SQL_PROMPT_FORMAT`.
Retraining (different base model, different datasets) means: add a new format
here, point the env var at it. Nothing else in the codebase changes — see
`app/services/text2sql/__init__.py` for the model-agnostic pipeline around this.

Keeping old formats registered is deliberate: it's what lets two fine-tunes be
compared on the same evaluation harness.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Callable

from app.schemas.datasets import DatasetSchema
from app.services.text2sql import ddl

# A chat message as the OpenAI-compatible API expects it.
Message = dict[str, str]


@dataclass(frozen=True)
class PromptFormat:
    """How to turn (schema DDL, question) into chat messages, and parse the reply.

    `sql_dialect_in` is the dialect the *model's output* should be parsed as — a
    property of the training targets, not of the database we run against. See
    `sql.py` for why that distinction matters.

    `render_schema` is the schema block's shape — also a property of the
    training data. A format served on SageMaker never uses `build_messages`
    (the endpoint renders its own training prompt from the schema and the
    question); it is kept so the format still says, in one place, what the
    model was trained on.
    """

    name: str
    build_messages: Callable[[str, str], list[Message]]
    parse_completion: Callable[[str], str]
    sql_dialect_in: str
    render_schema: Callable[[Sequence[tuple[str, DatasetSchema]]], str] = field(default=ddl.render)


# ---------------------------------------------------------------------------
# sql-create-context-v1
# ---------------------------------------------------------------------------
# The format of the qwen3-32b fine-tune run on Azure AI Foundry, job
# `ftjob-7d7216f44c3049a4951d31ca750555ec` (2026-07-25). Recovered by parsing
# all 74,649 rows of its training file: a single system prompt (zero variants)
# and a rigid user template (zero rows deviate). Verified properties of the
# 74,649 assistant targets:
#   - 100% start with SELECT
#   - 0 markdown fences, 0 <think> blocks, 0 trailing semicolons
#   - 99.6% single-table schemas, mean schema length 70 chars
#   - 83.3% use DOUBLE-QUOTED string literals -> hence sql_dialect_in="mysql"
#   - questions are English-only (142 of 74,649 carry Spanish markers, i.e. noise),
#     which matches the product rule that user questions and uploaded datasets are
#     in English — so no translation step exists.
# Dataset identified as `b-mc2/sql-create-context` (74,649 + 3,928 = 78,577
# rows exactly); 71.6% of schemas are synthetic WikiSQL `table_name_N`.

_SCC_SYSTEM = (
    "You are a text-to-SQL assistant. Given a database schema and a question in "
    "natural language, generate a single valid SQL query that answers the question. "
    "Respond with only the SQL query, no explanation."
)


def _scc_build(ddl: str, question: str) -> list[Message]:
    # Exact training layout: "### Schema:\n{ddl}\n\n### Question:\n{question}".
    # The DDL is one line (semicolon-separated tables) — training schemas never
    # contained a newline (verified: 0 of 74,649).
    return [
        {"role": "system", "content": _SCC_SYSTEM},
        {"role": "user", "content": f"### Schema:\n{ddl}\n\n### Question:\n{question}"},
    ]


def _scc_parse(completion: str) -> str:
    """Strip the wrappers a chat model may add even when trained not to.

    The training targets were bare SQL, so normally this is a no-op. It stays
    defensive because the base model (qwen3) is a hybrid-reasoning model and can
    fall back to emitting `<think>` blocks or fenced code on unfamiliar input.
    """
    text = completion.strip()

    # Reasoning block (qwen3): keep only what follows it.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()

    # Fenced code block: take the fence contents.
    if text.startswith("```"):
        body = text[3:]
        if "\n" in body:
            first, rest = body.split("\n", 1)
            # Drop a language tag like ```sql
            if first.strip().lower() in {"", "sql"}:
                body = rest
        text = body.split("```", 1)[0].strip()

    return text.strip().rstrip(";").strip()


SQL_CREATE_CONTEXT_V1 = PromptFormat(
    name="sql-create-context-v1",
    build_messages=_scc_build,
    parse_completion=_scc_parse,
    sql_dialect_in="mysql",
)


# ---------------------------------------------------------------------------
# generic-chat-v1
# ---------------------------------------------------------------------------
# For a general instruction-following chat model used as Module A — gpt-4o-mini,
# gpt-5.4-mini, Claude, a local Llama — rather than a fine-tune. It must not be
# served by `sql-create-context-v1`.
#
# The difference that matters is not the wording of the prompt, it is
# `sql_dialect_in`. `sql-create-context-v1` reads the model's output as **MySQL**,
# and that is correct *for that fine-tune*: 83.3% of its training targets use
# double quotes as string literals and none as identifiers, so MySQL is the
# dialect it actually writes. A general chat model writes standard SQL, where
# double quotes are identifiers — and reading that as MySQL is silently
# destructive:
#
#     model writes   SELECT "Total Revenue" FROM ds_a
#     read as mysql  SELECT 'Total Revenue' FROM ds_a   ← a constant string
#     read as duckdb SELECT "Total Revenue" FROM ds_a   ← the column
#
# The MySQL reading produces a query that parses, passes the guardrail, executes
# without error, and returns the same literal text on every row. There is no
# failure anywhere for anyone to notice. Hence a separate format rather than a
# tweak: the dialect is a property of the model, and swapping models has to swap
# it too.
#
# Unlike the fine-tune's, this prompt may say as much as it likes — there is no
# training distribution to stay inside — so it states the constraints the
# guardrail enforces anyway. Getting compliant SQL on the first attempt is
# cheaper than getting it rejected and re-asked.

_GENERIC_SYSTEM = (
    "You are a text-to-SQL assistant for a business-intelligence product. "
    "Given a database schema and a question in natural language, write a single "
    "DuckDB SELECT query that answers it.\n"
    "Rules:\n"
    "- Reply with the SQL query and nothing else: no explanation, no commentary, "
    "no markdown fences.\n"
    "- One statement only. Never write INSERT, UPDATE, DELETE, DROP, CREATE, "
    "ALTER, ATTACH, COPY or any other statement that changes data.\n"
    "- Use only the tables and columns given in the schema. Never invent a column, "
    "and never read from a file or an external source "
    "(no read_parquet, read_csv, glob or similar).\n"
    "- Quote string literals with single quotes. Double quotes are for identifiers.\n"
    "- Prefer an explicit ORDER BY when the question implies a ranking or a "
    "'top N', and a LIMIT when it names a count."
)


def _generic_build(ddl: str, question: str) -> list[Message]:
    return [
        {"role": "system", "content": _GENERIC_SYSTEM},
        {"role": "user", "content": f"Schema:\n{ddl}\n\nQuestion:\n{question}\n\nSQL:"},
    ]


GENERIC_CHAT_V1 = PromptFormat(
    name="generic-chat-v1",
    build_messages=_generic_build,
    # `_scc_parse` is not fine-tune-specific — it strips reasoning blocks, code
    # fences and trailing semicolons, which is exactly the defensive cleanup a
    # general chat model needs more of, not less. Reused rather than copied.
    parse_completion=_scc_parse,
    sql_dialect_in="duckdb",
)


# ---------------------------------------------------------------------------
# qwen-bird-spider-v1
# ---------------------------------------------------------------------------
# The fine-tuned Qwen3.8-27B SQL adapter: SageMaker training job
# `model-a-dev-qlora-001` (2 epochs, 2,110 steps; the final step ships), served
# by `ML/src/model_a/inference.py` with `task: "sql"`. Trained on 16,874 rows —
# 8,577 Spider train, 8,297 BIRD train (7,872 of those with an
# `-- External knowledge:` line before the schema).
#
# The prompt below is byte-for-byte `ML/src/common/prompts.py`'s SQL_SYSTEM_PROMPT
# and `sql_user` — `tests/test_text2sql.py` holds the two copies together. It is
# not what the backend sends: the SageMaker handler renders the prompt from the
# schema and question itself, through that same module. It is here so the
# format documents its own training shape, and so the drift test has something
# to compare.
#
# What the backend does own is the other two halves of the contract:
#   - the schema block (`ddl.render_multiline`: the Spider/BIRD layout, SQLite
#     type names);
#   - reading the answer as **SQLite**, which is what every target was written
#     in and what its accuracy was measured by executing. `sql._sqlite_semantics`
#     restores the three SQLite meanings sqlglot's translation drops (the
#     double-quoted string fallback, 8-byte REAL, JULIANDAY).

_QWEN_SQL_SYSTEM = (
    "You are an expert SQL assistant. Given a database schema and a natural language question, "
    "generate a syntactically correct and minimal SQL SELECT query. "
    "Return only the SQL query, nothing else."
)


def _qwen_build(ddl_text: str, question: str) -> list[Message]:
    return [
        {"role": "system", "content": _QWEN_SQL_SYSTEM},
        {"role": "user", "content": f"### Schema:\n{ddl_text}\n\n### Question:\n{question}\n\n### SQL:"},
    ]


QWEN_BIRD_SPIDER_V1 = PromptFormat(
    name="qwen-bird-spider-v1",
    build_messages=_qwen_build,
    parse_completion=_scc_parse,
    sql_dialect_in="sqlite",
    render_schema=ddl.render_multiline,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_FORMATS: dict[str, PromptFormat] = {
    SQL_CREATE_CONTEXT_V1.name: SQL_CREATE_CONTEXT_V1,
    GENERIC_CHAT_V1.name: GENERIC_CHAT_V1,
    QWEN_BIRD_SPIDER_V1.name: QWEN_BIRD_SPIDER_V1,
}

#: Formats whose model is the fine-tuned adapter on SageMaker. Pairing one of
#: these with a Bedrock model, or the SageMaker endpoint with a chat format,
#: would send a prompt shape the model was never trained on.
SAGEMAKER_FORMATS = frozenset({QWEN_BIRD_SPIDER_V1.name})

DEFAULT_FORMAT = SQL_CREATE_CONTEXT_V1.name


def get_format(name: str) -> PromptFormat:
    try:
        return _FORMATS[name]
    except KeyError:
        known = ", ".join(sorted(_FORMATS))
        raise ValueError(f"unknown prompt format {name!r} — registered: {known}") from None


def register_format(fmt: PromptFormat) -> None:
    """Add a format (used by a future fine-tune, and by tests)."""
    _FORMATS[fmt.name] = fmt
