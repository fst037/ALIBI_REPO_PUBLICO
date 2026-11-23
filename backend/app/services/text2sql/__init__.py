"""Module A — Text-to-SQL. The model-agnostic pipeline.

    question ─► prompt (format) ─► model ─► parse ─► guardrail + normalize ─► SQL

User questions and uploaded datasets are in English by product rule, and the
fine-tune was trained on English questions, so there is no translation step —
the question reaches the model as the user typed it.

Everything here is independent of *which* model is deployed. The one model-specific
piece is the `PromptFormat` (see `formats.py`), selected by config. Retraining —
different base model, BIRD/Spider instead of sql-create-context — means adding a
format and repointing three env vars; no other module changes.

`generate` never returns unvalidated SQL: the guardrail in `sql.py` runs before
anything is handed back, so a caller cannot accidentally surface or execute a
query that mutates data or reaches outside the conversation's dataset.

Two transports, chosen by `ALIBI_TEXT2SQL_PROVIDER`: `bedrock` sends the
format's chat messages to a general model; `sagemaker` sends the schema block and
the question to the fine-tuned adapter, whose endpoint renders its own training
prompt (`app/core/sagemaker.py`). Everything after the reply — parsing, the
guardrail, the dialect — is the same for both.

When no model is configured, `is_configured()` is False and callers answer 503
`text2sql_unavailable`. There is no canned fallback.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import logging

from app.core import sagemaker
from app.core.config import ModelProvider, get_settings
from app.schemas.datasets import DatasetSchema
from app.services.text2sql import ddl, formats, sql
from app.services.text2sql.client import Completion, EndpointConfig, LlmUnavailable, complete
from app.services.text2sql.sql import SqlRejected

logger = logging.getLogger("alibi.text2sql")

__all__ = [
    "GeneratedSql",
    "LlmUnavailable",
    "SqlRejected",
    "generate",
    "is_configured",
    "prompt_format",
    "served_by",
]


@dataclass(frozen=True)
class GeneratedSql:
    """The result of one Text-to-SQL call, with enough provenance to debug it.

    `sql` is validated and normalized to DuckDB. `raw_sql` is what the model
    actually emitted — kept because the two differ routinely (83.3% of this
    fine-tune's training targets use double-quoted string literals that DuckDB
    would reject), and because the raw output is what an evaluation harness and a
    thesis appendix need to show.
    """

    sql: str
    raw_sql: str
    question: str
    ddl: str
    model: str
    format_name: str
    prompt_tokens: int
    completion_tokens: int


def _text2sql_endpoint() -> EndpointConfig:
    settings = get_settings()
    return EndpointConfig(
        model_id=settings.text2sql_model_id or "",
        region=settings.aws_region,
        timeout_seconds=settings.text2sql_timeout_seconds,
    )


def _uses_sagemaker() -> bool:
    return get_settings().text2sql_provider is ModelProvider.sagemaker


def is_configured() -> bool:
    """True when a model is configured, with a prompt format that fits it.

    A mismatch — the fine-tune's format pointed at a Bedrock model, or the
    SageMaker endpoint with a chat format — counts as not configured, and says
    why in the log: it would send a prompt the model was never trained on, and
    its answers would be quietly worse rather than visibly broken.
    """
    settings = get_settings()
    fine_tuned_format = settings.text2sql_prompt_format in formats.SAGEMAKER_FORMATS
    if _uses_sagemaker():
        if not fine_tuned_format:
            logger.error(
                "ALIBI_TEXT2SQL_PROVIDER=sagemaker needs a fine-tune prompt format (%s), not %r",
                ", ".join(sorted(formats.SAGEMAKER_FORMATS)),
                settings.text2sql_prompt_format,
            )
            return False
        return sagemaker.target_from_settings(settings) is not None
    if fine_tuned_format:
        logger.error(
            "prompt format %r is the SageMaker fine-tune's; set ALIBI_TEXT2SQL_PROVIDER=sagemaker",
            settings.text2sql_prompt_format,
        )
        return False
    return _text2sql_endpoint().is_configured


def served_by() -> str:
    """Who writes the SQL, for the user's step view."""
    if _uses_sagemaker():
        return "modelo ajustado de ALIBI (BIRD + Spider, SageMaker)"
    return f"Bedrock · {get_settings().text2sql_model_id or 'sin modelo'}"


def prompt_format() -> formats.PromptFormat:
    return formats.get_format(get_settings().text2sql_prompt_format)


def _with_knowledge(schema_ddl: str, evidence: str | None) -> str:
    """The schema block with BIRD's external-knowledge line in front, as trained.

    `ML/src/model_a/prepare_data.py` built 7,872 of the adapter's training
    schemas as `-- External knowledge: {evidence}` + a blank line + the DDL —
    BIRD's evidence ("female refers to gender = 'F'"), the channel through which
    the model learned to take a hint about what the words of a question mean in
    the data. Sending hints anywhere else (appended to the question, as the
    retry once did) is a prompt shape it never saw.
    """
    evidence = " ".join((evidence or "").split())
    return f"-- External knowledge: {evidence}\n\n{schema_ddl}" if evidence else schema_ddl


def generate(
    question: str,
    context: Sequence[tuple[str, DatasetSchema]],
    evidence: str | None = None,
) -> GeneratedSql:
    """Turn a natural-language question into validated, DuckDB-ready SQL.

    `context` is every (dataset id, schema) the conversation may read — one entry
    for a single file, several for an explicit selection or a folder. All of them
    reach the prompt, which is what makes a JOIN across files possible at all.

    Each `dataset_id` (not the display name) becomes a table identifier — see
    `ddl.table_name` for why.

    `evidence` is BIRD-style external knowledge about the question — what its
    words refer to in these tables, how a figure is computed — written by the
    planner, plus the last error on a retry. The fine-tune gets it where it was
    trained to (`_with_knowledge`); a chat model, as a line after the question.

    Raises `LlmUnavailable` if the model can't be reached, `SqlRejected` if what it
    produced is unsafe or doesn't fit the data, and `ValueError` when no schema in
    `context` has queryable columns.
    """
    fmt = prompt_format()
    schema_ddl = fmt.render_schema(context)
    tables, columns = ddl.allowed_names(context)

    if _uses_sagemaker():
        completion = _complete_on_sagemaker(_with_knowledge(schema_ddl, evidence), question.strip())
    else:
        asked = question.strip()
        if evidence and evidence.strip():
            asked = f"{asked}\n\nHints: {' '.join(evidence.split())}"
        messages = fmt.build_messages(schema_ddl, asked)
        completion = complete(_text2sql_endpoint(), messages, temperature=0.0, max_tokens=512)
    raw_sql = fmt.parse_completion(completion.text)

    try:
        checked = sql.check(
            raw_sql,
            read_dialect=fmt.sql_dialect_in,
            allowed_tables=tables,
            allowed_columns=columns,
        )
    except SqlRejected as rejected:
        # Carried for the record ("How I got here" shows the query that was
        # refused), never for execution: nothing runs a rejected query.
        rejected.raw_sql = raw_sql
        raise

    return GeneratedSql(
        sql=checked.sql,
        raw_sql=checked.raw,
        question=question,
        ddl=schema_ddl,
        model=completion.model,
        format_name=fmt.name,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
    )


def _complete_on_sagemaker(schema_ddl: str, question: str) -> Completion:
    """The fine-tuned adapter: structured input, the endpoint renders the prompt.

    Its reply also carries the endpoint's own guardrail verdict (`valid`), which
    is ignored on purpose: `sql.check` below is the check that knows this
    dataset's tables and columns, and it runs on every path.

    `ModelAsleep` is reported as unavailable here. Waking the model is the run's
    job (`sagemaker.ensure_awake`, before the first question), because only the
    run can show the user that it is waiting.
    """
    target = sagemaker.target_from_settings()
    if target is None:
        raise LlmUnavailable("no SageMaker endpoint configured")
    try:
        reply = sagemaker.invoke(target, {"task": "sql", "schema": schema_ddl, "question": question})
    except sagemaker.SageMakerUnavailable as err:
        raise LlmUnavailable(str(err)) from err
    return Completion(
        text=str(reply.get("sql") or ""),
        model=f"sagemaker:{target.endpoint_name}",
        # The handler does not report token counts; the harness budget counts
        # what it can see, and a fine-tune's call costs instance time, not tokens.
        prompt_tokens=0,
        completion_tokens=0,
    )
