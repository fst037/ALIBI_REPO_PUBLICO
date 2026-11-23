"""`conversations` domain service — swap point + chat orchestration.

Data reads/writes (list/get/create conversations, append + look up assistant
messages) dispatch to the database repository (owner-scoped) when configured,
else the in-memory fixtures. The prepare/execute cycle and suggestions come from
`conversations_engine` (result envelope, transient drafts, phrasing check).

**A conversation runs over one folder.** The user picks it; every *confirmed*
dataset inside becomes a table Module A is shown and the data plane registers, so
a JOIN across files in that folder is just SQL. The folder is resolved on every
read, so a file uploaded into it later joins a conversation already in progress.
Module A never sees a single isolated table, and never sees everything the user
owns.

The cycle:

    prepare   question ─► Module A (prompt → model → AST guardrail → DuckDB SQL)
              or hand-written SQL ─► the same guardrail
              ─► a draft the user confirms

    execute   the confirmed SQL ─► DuckDB over the folder's Parquet
              ─► rows ─┬─► orchestrator `explain_result` ─► a verified sentence
                       └─► Module C `visualize`  ─► a validated Vega-Lite spec

The last two run **concurrently**. Neither reads the other's output — both
describe the same rows — and running them in sequence would put two model
round-trips inside one request, against API Gateway's 29s ceiling, after the
query has already spent part of it. Their failure modes differ, though, and
deliberately: no sentence is a 503, no chart is just an answer without a
picture.

**Every step is real or it is an error.** There is no fixture path and no
degraded mode: if Module A is unconfigured or unreachable, `prepare` is a 503; if
the narrative model is, `execute` is a 503. A canned SQL string or a template
sentence under a real results table is indistinguishable from a real answer, and
the user has no way to tell which they got. The same reasoning is why a chart
that fails to generate is absent rather than replaced by a deterministic one.
Local and deployed differ only in which URL they point at.

**Asking is asynchronous.** `answer()` is what an answer run
(`app/services/runs.py`) executes, in a worker outside API Gateway's 29 s
ceiling. A question goes to the agent harness (`services/investigation.py`), which may run several queries; hand-written SQL
runs this same cycle as steps. `prepare`/`execute` stay for the synchronous
path.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from app.core import usage_meter
from app.core.config import get_settings
from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.repositories import conversations as repo
from app.repositories import datasets as datasets_repo
from app.repositories import folders as folders_repo
from app.schemas.charts import ChartSpec
from app.schemas.chat import (
    AssistantMessage,
    Conversation,
    ConversationSourceType,
    CreateConversationRequest,
    ExecuteMessageResponse,
    GuardResult,
    PreparedMessage,
    Suggestion,
    UpdateConversationRequest,
)
from app.schemas.datasets import Dataset, DatasetSchema
from app.schemas.folders import FolderDetail
from app.schemas.runs import RunStep, RunStepPurpose
from app.services import billing
from app.services import conversations_engine as engine
from app.services import conversations_memory as mem
from app.services import datasets as datasets_store
from app.services import folders as folders_store
from app.services import query
from app.services import text2sql
from app.services.query import QueryError, QueryResultRows, QueryTable

logger = logging.getLogger("alibi.conversations")

# ---- suggestions (static reference data) ----


def get_suggestions() -> list[Suggestion]:
    return engine.suggestions()


# ---- conversation data ops (dispatched) ----


def list_conversations(owner_id: str, recent: bool, limit: int) -> list[Conversation]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_conversations(session, owner_id, recent, limit)
    return mem.list_conversations(owner_id, recent, limit)


def get_conversation(owner_id: str, conversation_id: str) -> Conversation | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_conversation(session, owner_id, conversation_id)
    return mem.get_conversation(owner_id, conversation_id)


def _require_queryable(dataset: Dataset) -> None:
    """Refuse a dataset whose inferred schema the user hasn't confirmed.

    Ingesta 1.2: an `awaiting_confirmation` table is previewable but not
    queryable. Enforced when the datasets are named *explicitly*, so the user
    hears about it while picking files rather than after asking a question.

    A folder is different — see `_queryable_ids`.
    """
    if datasets_store.is_queryable(dataset):
        return
    raise ApiException(
        409,
        "schema_unconfirmed",
        f"«{dataset.name}» todavía no está lista para consultar. Confirmá primero su esquema.",
    )


def _require_queryable_folder(folder: FolderDetail) -> None:
    """A folder needs at least one confirmed dataset to be worth opening."""
    if any(datasets_store.is_queryable(d) for d in folder.datasets):
        return
    raise ApiException(
        409,
        "schema_unconfirmed",
        f"“{folder.name}” todavía no tiene ningún dataset listo para consultar. "
        "Primero confirmá el esquema inferido de al menos un archivo.",
    )


def create_conversation(owner_id: str, request: CreateConversationRequest) -> Conversation:
    """Open a conversation over one folder.

    A folder's queryable contents are its *confirmed* datasets. An unconfirmed
    one is simply not part of the set yet, rather than an error — refusing the
    whole folder would break an open conversation the moment someone uploads a
    new file into it, which is the opposite of what resolve-on-read is for. Only
    an entirely unqueryable folder is refused, because there is nothing to ask
    about.
    """
    folder_id = request.resolved_folder_id()
    source_type = ConversationSourceType.folder
    # Deep is a Pro feature.
    billing.require_answer_mode(owner_id, request.mode)

    if pool.is_configured():
        with session_scope() as session:
            folder = folders_repo.get_folder(session, owner_id, folder_id)
            if folder is None:
                raise ApiException(404, "not_found", "No se encontró la carpeta.")
            _require_queryable_folder(folder)
            return repo.create_conversation(
                session, owner_id, source_type, folder_id=folder_id, mode=request.mode
            )

    folder = folders_store.get_folder(owner_id, folder_id)
    if folder is None:
        raise ApiException(404, "not_found", "No se encontró la carpeta.")
    _require_queryable_folder(folder)
    return mem.create_conversation(
        owner_id,
        source_type,
        [d.id for d in folder.datasets if datasets_store.is_queryable(d)],
        source_id=folder_id,
        source_name=folder.name,
        mode=request.mode,
    )


def update_conversation(
    owner_id: str, conversation_id: str, request: UpdateConversationRequest
) -> Conversation | None:
    """Rename, star and/or set the default depth. `None` means it isn't the caller's."""
    title = request.title.strip() if request.title is not None else None
    if request.mode is not None:
        billing.require_answer_mode(owner_id, request.mode)
    if pool.is_configured():
        with session_scope() as session:
            return repo.update_conversation(
                session, owner_id, conversation_id,
                title=title, favorite=request.favorite, mode=request.mode,
            )
    return mem.update_conversation(
        owner_id, conversation_id, title=title, favorite=request.favorite, mode=request.mode
    )


def delete_conversation(owner_id: str, conversation_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.delete_conversation(session, owner_id, conversation_id)
    return mem.delete_conversation(owner_id, conversation_id)


def get_assistant_message(owner_id: str, message_id: str) -> AssistantMessage | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_assistant_message(session, owner_id, message_id)
    return mem.get_assistant_message(owner_id, message_id)


def conversation_exists(owner_id: str, conversation_id: str) -> bool:
    return _conversation_exists(owner_id, conversation_id)


def _conversation_exists(owner_id: str, conversation_id: str) -> bool:
    if pool.is_configured():
        with session_scope() as session:
            return repo.conversation_exists(session, owner_id, conversation_id)
    return mem.conversation_exists(owner_id, conversation_id)


def _append_assistant_message(
    owner_id: str, conversation_id: str, message: AssistantMessage
) -> None:
    if pool.is_configured():
        with session_scope() as session:
            repo.append_assistant_message(session, owner_id, conversation_id, message)
        return
    mem.append_assistant_message(owner_id, conversation_id, message)


def _autotitle(owner_id: str, conversation_id: str, question: str) -> None:
    """Name a conversation after the first question asked in it.

    Only while it still carries the placeholder: renaming on every question
    would make a History entry move under the user as they keep asking. Purely
    cosmetic, so a failure here is logged rather than allowed to break the
    request that triggered it.
    """
    if not question.strip():
        return
    conversation = get_conversation(owner_id, conversation_id)
    if conversation is None or conversation.title != engine.DEFAULT_TITLE:
        return
    title = engine.title_from_question(question)
    try:
        if pool.is_configured():
            with session_scope() as session:
                repo.set_title(session, owner_id, conversation_id, title)
            return
        mem.set_title(owner_id, conversation_id, title)
    except Exception:  # noqa: BLE001 - cosmetic; never worth failing the answer
        logger.warning("could not title conversation %s", conversation_id, exc_info=True)


# ---- prepare / execute cycle (orchestration over the dispatched data ops) ----


def _dataset_context(owner_id: str, conversation_id: str) -> list[tuple[str, DatasetSchema]]:
    """(dataset id, DatasetSchema) for *every* table the conversation queries.

    The id — not the display name — is what becomes the SQL table identifier; see
    `text2sql.ddl.table_name`.

    Empty when nothing usable is available (no datasets on the conversation, no
    inferred schema yet) — the caller then falls back to the fixture SQL rather
    than prompting the model with an empty schema, which would only produce a
    hallucinated query. Datasets whose schema isn't ready are skipped rather than
    failing the whole call: with a folder of ten files, one still-processing
    upload should not block questions about the other nine.
    """
    conversation = get_conversation(owner_id, conversation_id)
    if conversation is None or not conversation.datasetIds:
        return []
    context: list[tuple[str, DatasetSchema]] = []
    for dataset_id in conversation.datasetIds:
        # An unconfirmed dataset never reaches the prompt: describing a table the
        # model may not query would only invite SQL the guardrail then rejects.
        if not datasets_store.is_queryable(datasets_store.get_dataset(owner_id, dataset_id)):
            continue
        schema = datasets_store.get_schema(owner_id, dataset_id)
        if schema is not None and schema.columns:
            context.append((dataset_id, schema))
    return context


def _generate_sql(owner_id: str, conversation_id: str, question: str) -> str:
    """Real Text-to-SQL, or an error. There is no third outcome.

    Every failure here is surfaced rather than absorbed. Serving canned SQL as if
    the model had answered is the worst available option: the user confirms and
    saves a query that has nothing to do with their question, and nothing on
    screen says so.
    """
    if not text2sql.is_configured():
        logger.error("ALIBI_TEXT2SQL_* is not configured — cannot answer questions")
        raise ApiException(
            503,
            "text2sql_unavailable",
            "El modelo de consultas no está disponible en este momento. Probá de nuevo en unos minutos.",
        )

    context = _dataset_context(owner_id, conversation_id)
    if not context:
        raise ApiException(
            409,
            "no_queryable_data",
            "Esta carpeta todavía no tiene ningún archivo listo para consultar. "
            "Primero confirmá el esquema inferido de al menos un archivo.",
        )

    try:
        generated = text2sql.generate(question, context)
    except text2sql.LlmUnavailable as err:
        logger.error("Text-to-SQL endpoint unavailable: %s", err)
        raise ApiException(
            503,
            "text2sql_unavailable",
            "El modelo de consultas no está disponible en este momento. Probá de nuevo en unos minutos.",
        ) from err
    except ValueError as err:
        # Schema with no queryable columns — a data problem, not a model failure.
        logger.warning("cannot build a prompt for conversation %s: %s", conversation_id, err)
        raise ApiException(
            409,
            "no_queryable_data",
            "Los archivos de esta carpeta no tienen columnas disponibles para consultar.",
        ) from err

    logger.info(
        "generated SQL for conversation %s (tables=%d, format=%s, tokens=%d/%d)",
        conversation_id,
        len(context),
        generated.format_name,
        generated.prompt_tokens,
        generated.completion_tokens,
    )
    return generated.sql


def prepare(
    owner_id: str, conversation_id: str, question: str | None, sql: str | None
) -> PreparedMessage | GuardResult:
    if not _conversation_exists(owner_id, conversation_id):
        raise ApiException(404, "not_found", "No se encontró la conversación.")
    # Before any model call is spent: a question past the plan's quota (and
    # past the overage the user allowed) never reaches Module A.
    billing.require_query_allowance(owner_id)

    # Named here, not on the way out of `execute`: a question that fails — the
    # write guard, an unreachable model, SQL that doesn't run — is exactly the
    # thread the user wants to find again, and it would have stayed "New query"
    # forever. Whatever they typed is the subject of the conversation whether or
    # not it produced an answer.
    _autotitle(owner_id, conversation_id, question or sql or "")

    if question is not None:
        # Refuse a stated intent to mutate data before spending a model call.
        # The real security boundary is the AST guardrail below/inside text2sql.
        if engine.is_write(question):
            return engine.blocked(question)
        with usage_meter.metering() as meter:
            try:
                generated = _generate_sql(owner_id, conversation_id, question)
            except text2sql.SqlRejected as rejected:
                # The model produced SQL that isn't safe or doesn't fit the folder.
                # Surface it as a guard message with the specific reason.
                logger.warning("generated SQL rejected: %s", rejected.detail or rejected.message)
                return engine.blocked(question, rejected.message)
        return engine.make_draft(question, generated, usage=tuple(meter.calls))

    # Hand-edited SQL path: the user typed the query themselves, so it gets the
    # same AST guardrail as the model's output — no weaker check for a source
    # that is, if anything, less trustworthy.
    assert sql is not None
    # Hand-edited SQL is scoped to the conversation's own tables by
    # `_validate_edited_sql`, so it runs for real like a generated one.
    # `make_sql_draft`, not `make_draft`: there is no natural-language question
    # here, and passing the SQL as one puts it in the narrative prompt.
    return engine.make_sql_draft(sql, _validate_edited_sql(owner_id, conversation_id, sql))


def _validate_edited_sql(owner_id: str, conversation_id: str, sql: str) -> str:
    """Guardrail + DuckDB normalization for user-authored SQL.

    Scoped to every dataset on the conversation when schemas are available;
    otherwise the read-only checks still run, only the table/column allow-list is
    skipped. Hand-written JOINs across a folder's tables pass for the same reason
    generated ones do: the allow-list is the union over all of them.
    """
    from app.services.text2sql import ddl as ddl_module
    from app.services.text2sql import sql as sql_module

    allowed_tables: set[str] | None = None
    allowed_columns: set[str] | None = None
    context = _dataset_context(owner_id, conversation_id)
    if context:
        allowed_tables, allowed_columns = ddl_module.allowed_names(context)

    try:
        checked = sql_module.check(
            sql,
            read_dialect=text2sql.prompt_format().sql_dialect_in,
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
        )
    except sql_module.SqlRejected as rejected:
        raise ApiException(422, "sql_validation_failed", rejected.message) from rejected
    return checked.sql


def _query_tables(owner_id: str, conversation_id: str) -> list[QueryTable]:
    """The DuckDB views for every confirmed dataset in the conversation's folder.

    All or nothing on purpose. Running a JOIN over the subset that happens to be
    ready would answer a question about N files using fewer — silently, which is
    precisely the wrong-answer failure this whole design is built to avoid. A
    dataset with no materialised Parquet is therefore an error, not a reason to
    quietly answer with less.
    """
    conversation = get_conversation(owner_id, conversation_id)
    if conversation is None or not conversation.datasetIds:
        raise ApiException(
            409, "no_queryable_data", "Esta carpeta todavía no tiene ningún archivo listo para consultar."
        )
    tables: list[QueryTable] = []
    for dataset_id in conversation.datasetIds:
        try:
            # `require_confirmed`: the last line of defence for Ingesta 1.2. The
            # sources are already filtered upstream, so reaching this means a
            # dataset changed state between opening the conversation and running
            # the query.
            tables.append(
                datasets_store.query_table(owner_id, dataset_id, require_confirmed=True)
            )
        except QueryError as exc:
            raise ApiException(409, exc.code, exc.message) from exc
    if not tables:
        raise ApiException(
            409, "no_queryable_data", "Esta carpeta todavía no tiene ningún archivo listo para consultar."
        )
    return tables


def _column_labels(owner_id: str, conversation_id: str) -> dict[str, str]:
    """Business names for the columns a result might contain.

    Feeds the orchestrator's narrative node, which must not put an internal
    identifier in a sentence written for a non-technical reader. Both keys are
    mapped to the same label because a query can name a column either way: the
    *effective* name (what Module B's semantic inference settled on, and what the
    generated SQL normally uses) and the *source* name (the raw header from the
    uploaded file, which a hand-edited query may still use).

    Best-effort by design — an unmapped column simply falls back to a mechanical
    prettifying of whatever the result called it, so a missing schema costs
    polish, not correctness.

    `SchemaColumn.description` is preferred over the name when Module B's
    semantic enrichment produced one. That is the whole reason this function
    exists: mechanically un-snake-casing `fch_vcto` yields "Fch vcto", which is
    still an internal identifier wearing a space, and the narrative validator's
    `_is_machine_name` check will reject a sentence containing it. Only the
    description carries the real business name ("Due date").
    """
    labels: dict[str, str] = {}
    for _, schema in _dataset_context(owner_id, conversation_id):
        for column in schema.columns:
            described = (column.description or "").strip()
            if described:
                pretty = described
            else:
                pretty = column.name.replace("_", " ").strip()
                pretty = pretty[:1].upper() + pretty[1:]
            labels[column.name] = pretty
            if column.sourceName:
                labels.setdefault(column.sourceName, pretty)
    return labels


def _source_label(owner_id: str, conversation_id: str) -> str | None:
    """What the numbers are about, in the user's own words ("Q1 sales export").

    Grounds the subject of the narrative's sentence. The node uses it only as
    context and never quotes it as a file name, so the conversation's own source
    name — the dataset the user picked, or the folder they opened — is exactly
    the right string: it is the label they already see at the top of the screen.

    `explain_result` has accepted this since it was written and nothing ever
    supplied it, so every narrative so far has been composed with no idea what
    the data was about.
    """
    conversation = get_conversation(owner_id, conversation_id)
    if conversation is None or conversation.source is None:
        return None
    return (conversation.source.name or "").strip() or None


def execute(owner_id: str, conversation_id: str, draft_id: str) -> ExecuteMessageResponse:
    if not _conversation_exists(owner_id, conversation_id):
        raise ApiException(404, "not_found", "No se encontró la conversación.")
    draft = engine.pop_draft(draft_id)
    if draft is None:
        raise ApiException(
            404, "not_found", "El borrador no existe o venció. Prepará la consulta de nuevo."
        )

    # The confirmed SQL is carried on the draft, never regenerated: the user
    # approved one specific query.
    rows = _run_query(owner_id, conversation_id, draft.sql)
    message, receipt = _finish(owner_id, conversation_id, draft, rows)
    return ExecuteMessageResponse(message=message, receipt=receipt)


def _run_query(owner_id: str, conversation_id: str, sql: str) -> QueryResultRows:
    tables = _query_tables(owner_id, conversation_id)
    try:
        # Validated again here, deliberately, even though prepare already ran
        # `text2sql.sql.check` over this exact string. The two checks are not the
        # same check: this one is the data plane's, it knows the table names
        # actually registered on *this* connection, and it is the last thing to
        # run before DuckDB sees the query.
        #
        # A previous `validate=False` made execution trust a verdict reached
        # elsewhere, by different rules, minutes earlier — which is how a
        # table-valued function (`FROM read_parquet('s3://…')`) used to reach
        # DuckDB with nothing on the request path having inspected it. The cost
        # of not trusting it is one sqlglot parse, against a query that is about
        # to read Parquet over the network.
        # The plan's rows per query (Free 1.000), under the platform ceiling.
        return query.run_sql(sql, tables, limit=billing.row_limit_for(owner_id))
    except QueryError as exc:
        logger.warning("execution failed for conversation %s: %s", conversation_id, exc)
        raise ApiException(409, exc.code, exc.message) from exc


def _finish(owner_id: str, conversation_id: str, draft, rows: QueryResultRows):  # noqa: ANN001, ANN202
    """Narrate and chart the rows, store the answer, bill it. Returns (message, receipt).

    The ledger row is written here, after the answer is stored, because an
    answered question is what a plan's "query" counts: a refusal or a failure
    along the way costs the user nothing.
    """
    with usage_meter.metering() as meter:
        narrative, chart = _narrate_and_visualize(
            owner_id,
            conversation_id,
            question=draft.nl_question,
            sql=draft.sql,
            rows=rows,
        )
    message, receipt = engine.build_assistant_message(
        draft.question, draft.sql, rows, narrative, chart
    )
    _append_assistant_message(owner_id, conversation_id, message)
    billing.record_query(
        owner_id,
        calls=[*draft.usage, *meter.calls],
        rows_returned=len(rows.rows),
        source_name=_source_label(owner_id, conversation_id),
    )
    return message, receipt


StepFn = Callable[..., AbstractContextManager[RunStep]]


def answer(
    owner_id: str,
    conversation_id: str,
    *,
    question: str | None,
    sql: str | None,
    step: StepFn,
    deep: bool = False,
) -> GuardResult | ExecuteMessageResponse:
    """What an answer run executes, as reportable steps.

    `step(label, purpose=…)` is a context manager supplied by the run: it records
    the step as running, then done or failed, so a polling client sees progress.
    Errors propagate as the same `ApiException`s the synchronous endpoints raise;
    the run turns them into its `error`.

    * **A question** goes to the agent harness (`services/investigation.py`): a
      planner decides which queries to run, a bigger model writes the answer
      across their results, Quick or Deep (`deep`) setting the budget.
    * **Hand-written SQL** runs as written — prepare/execute's own steps, no
      planner. The user already decided what to ask; planning around their query
      would answer a different one.
    """
    if question is not None:
        if not _conversation_exists(owner_id, conversation_id):
            raise ApiException(404, "not_found", "No se encontró la conversación.")
        _autotitle(owner_id, conversation_id, question)
        # Refuse a stated intent to mutate data before spending a model call; the
        # real boundary is still the AST guardrail on every generated query.
        if engine.is_write(question):
            return engine.blocked(question)
        from app.services import investigation

        _wake_fine_tuned_models(step, wait=True)
        return investigation.answer_question(
            owner_id, conversation_id, question, deep=deep, step=step
        )

    _wake_fine_tuned_models(step, wait=False)
    with step("Revisando tu SQL"):
        prepared = prepare(owner_id, conversation_id, None, sql)
    if isinstance(prepared, GuardResult):
        return prepared
    draft = engine.pop_draft(prepared.draftId)
    if draft is None:  # pragma: no cover - popped in the same process, microseconds later
        raise ApiException(500, "internal_error", "El borrador desapareció antes de ejecutarse.")

    with step("Consultando los datos", purpose=RunStepPurpose.insight) as current:
        current.sql = draft.sql
        rows = _run_query(owner_id, conversation_id, draft.sql)
        current.rowCount = len(rows.rows)

    with step("Redactando la respuesta y el gráfico"):
        message, receipt = _finish(owner_id, conversation_id, draft, rows)
    return ExecuteMessageResponse(message=message, receipt=receipt)


_WAKING_LABEL = "Iniciando el modelo de consultas"
# Measured 2026-10-15: a cold copy takes ~10 min to load the 27B base from
# HuggingFace and quantise it, plus ~40 s of warm-up. Once loaded it answers in
# seconds, so the user is told both.
_WAKING_THOUGHT = (
    "Estaba en reposo y se está cargando en la GPU desde cero: tarda unos 10 minutos. "
    "Después responde en segundos y las próximas preguntas no esperan."
)
_MODEL_WAKING = (
    "El modelo de consultas se está iniciando y todavía no respondió. "
    "Probá de nuevo en unos minutos."
)
_MODEL_NO_CAPACITY = (
    "No hay capacidad de GPU disponible para iniciar el modelo de consultas en este momento. "
    "Probá de nuevo en unos minutos."
)


def _wake_fine_tuned_models(step: StepFn, *, wait: bool) -> None:
    """Make sure the fine-tuned models' endpoint is up before a run needs it.

    The endpoint scales to zero when idle (`app/core/sagemaker.py`), and coming
    back takes minutes. Done here, once, at the top of the run, so the wait is a
    visible step ("Iniciando el modelo…") instead of the first query hanging —
    and only shown when there actually is a wait.

    `wait=True` (a question): Module A writes every query, so the run waits for
    it, up to `model_wake_wait_seconds`, and fails with 503 `model_waking` after
    that. `wait=False` (hand-written SQL): only Module C's chart needs the
    model, and the chart is the optional half of an answer — not worth minutes
    of the user's time. The wake is requested so the next answer has it, and
    this one goes on, without a chart if the model is still asleep.
    """
    from contextlib import ExitStack

    from app.core import sagemaker
    from app.core.config import ModelProvider

    settings = get_settings()
    sql_needs_it = wait and settings.text2sql_provider is ModelProvider.sagemaker
    chart_needs_it = (
        settings.visualization_enabled
        and settings.visualization_provider is ModelProvider.sagemaker
    )
    target = sagemaker.target_from_settings(settings)
    if target is None or not (sql_needs_it or chart_needs_it):
        return

    if not sql_needs_it:
        try:
            sagemaker.wake(target)
        except sagemaker.SageMakerUnavailable as err:
            logger.warning("could not wake the chart model: %s", err)
        return

    with ExitStack() as waiting:
        try:
            sagemaker.ensure_awake(
                target,
                wait_seconds=settings.model_wake_wait_seconds,
                on_waking=lambda: waiting.enter_context(step(_WAKING_LABEL, thought=_WAKING_THOUGHT)),
            )
        except sagemaker.ModelAsleep as err:
            logger.error("fine-tuned model still asleep after the wait: %s", err)
            raise ApiException(503, "model_waking", _MODEL_WAKING) from err
        except sagemaker.ModelNoCapacity as err:
            logger.error("fine-tuned model has no GPU capacity: %s", err)
            raise ApiException(503, "model_no_capacity", _MODEL_NO_CAPACITY) from err
        except sagemaker.SageMakerUnavailable as err:
            logger.error("fine-tuned model endpoint unavailable: %s", err)
            raise ApiException(
                503,
                "text2sql_unavailable",
                "El modelo de consultas no está disponible en este momento. Probá de nuevo en unos minutos.",
            ) from err


def _narrate_and_visualize(
    owner_id: str,
    conversation_id: str,
    *,
    question: str,
    sql: str,
    rows: QueryResultRows,
) -> tuple[str, ChartSpec | None]:
    """The sentence and the chart, asked for at the same time.

    Two model calls that do not depend on each other, so they are two threads
    rather than two waits. The saving is real: both are network-bound against
    the same Bedrock model with 12–15s timeouts, and `execute` has already
    spent part of API Gateway's 29s on the query itself. Sequentially the worst
    case exceeds the ceiling and the user gets a gateway timeout instead of an
    answer; concurrently the worst case is the slower of the two.

    Threads rather than `asyncio`: the route is a plain `def`, the SDK call is
    a blocking boto3 client, and both nodes are otherwise synchronous
    top-to-bottom. Two threads that each make one HTTP request need none of
    what an event loop would cost to introduce here.

    The gather is asymmetric on purpose. `_narrate` raising is allowed to
    propagate — an answer with no sentence is not an answer — while the chart
    thread is written to return `None` for every failure it can have, so the
    only thing that reaches this point from it is a chart or its absence.
    """
    from concurrent.futures import ThreadPoolExecutor

    labels = _column_labels(owner_id, conversation_id)
    source = _source_label(owner_id, conversation_id)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="answer") as pool_:
        # `run_in_context`: worker threads start with an empty context, and
        # the cost meter lives in it.
        narrative_task = pool_.submit(
            usage_meter.run_in_context(_narrate),
            question=question,
            sql=sql,
            rows=rows,
            column_labels=labels,
            source_label=source,
        )
        chart_task = pool_.submit(
            usage_meter.run_in_context(_visualize),
            conversation_id,
            question=question,
            sql=sql,
            rows=rows,
            column_labels=labels,
        )
        # The chart is resolved first and never raises, so a narrative failure
        # can propagate without leaving the other future unread.
        chart = chart_task.result()
        narrative = narrative_task.result()

    if chart is None:
        logger.info("no chart for conversation %s; answering with narrative + table", conversation_id)
    return narrative, chart


def _visualize(
    conversation_id: str,
    *,
    question: str,
    sql: str,
    rows: QueryResultRows,
    column_labels: dict[str, str],
) -> ChartSpec | None:
    """Module C's chart for this result, or `None`. Never raises."""
    return _visualize_with_outcome(
        conversation_id, question=question, sql=sql, rows=rows, column_labels=column_labels
    )[0]


def _visualize_with_outcome(
    conversation_id: str,
    *,
    question: str,
    sql: str,
    rows: QueryResultRows,
    column_labels: dict[str, str],
) -> tuple[ChartSpec | None, str]:
    """`_visualize`, plus Module C's reason (`nodes.visualize.Outcome`). Never raises.

    Every failure the node can have already comes back as `None`; this catches
    anything it could not anticipate for the same reason — the chart is the
    optional half of the answer, and an unexpected error inside it must not cost
    the user a result they already paid to compute.
    """
    from app.orchestrator.contracts import VisualizeInput
    from app.orchestrator.nodes.visualize import visualize_with_outcome

    try:
        return visualize_with_outcome(
            VisualizeInput(
                question=question,
                sql_query=sql,
                columns=rows.columns,
                rows=rows.rows,
                row_count=len(rows.rows),
                truncated=rows.truncated,
                column_labels=column_labels,
            )
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("visualization failed for conversation %s", conversation_id)
        return None, "unavailable"


def _narrate(
    *,
    question: str,
    sql: str,
    rows: QueryResultRows,
    column_labels: dict[str, str],
    source_label: str | None,
) -> str:
    """The orchestrator's sentence about this result, or a 503.

    The query already ran and the user's numbers are correct, so failing the
    whole response for want of prose looks like the harsher choice. It isn't: a
    results table with no sentence above it is not the product — the sentence is
    the thing a non-technical user actually reads, and the deterministic template
    that used to stand in for it was indistinguishable from a real answer while
    being written by nobody. Reporting the failure lets the user retry; serving a
    fallback silently spends their trust instead.
    """
    from app.orchestrator.contracts import ExplainInput
    from app.orchestrator.nodes.explain_result import NarrativeFailed, explain_result

    payload = ExplainInput(
        question=question,
        sql_query=sql,
        columns=rows.columns,
        rows=rows.rows,
        row_count=len(rows.rows),
        truncated=rows.truncated,
        column_labels=column_labels,
        source_label=source_label,
    )
    try:
        return explain_result(payload).narrative
    except NarrativeFailed as err:
        logger.error("narrative failed: %s", err)
        raise ApiException(
            503,
            "narrative_unavailable",
            "Ejecutamos tu pregunta pero no pudimos redactar la respuesta. Probá de nuevo en un momento.",
        ) from err
