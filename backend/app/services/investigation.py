"""Answering a question with the agent harness — the service side.

`conversations.answer()` hands a natural-language question here. This module
owns what the orchestrator deliberately does not: the conversation's folder, its
tables, Module A and the data plane. It gives the planner loop
(`orchestrator/harness.py`) an `ask_sql` tool over exactly those, then writes the
answer from what the loop kept:

    investigate ─► findings ─┬─► synthesize (Haiku / Sonnet) ─► summary + a sentence per finding
                             └─► Module C, once per finding   ─► a chart each, or none
                                       (concurrently)
                 ─► one assistant message: summary, findings, trail

`ask_sql` is the same path a one-query answer takes — Module A, the read-only
guardrail, DuckDB over the folder's Parquet — so no rule exists twice. What
differs is how a failure is reported: a query the guardrail rejects or DuckDB
cannot run is a `ToolFailed` the planner may correct (with one automatic retry
carrying the error), while an unreachable model or a folder with nothing to query
ends the run with the same 503/409 the synchronous endpoints answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack
from typing import Any

from app.core import usage_meter
from app.core.errors import ApiException
from app.orchestrator import harness
from app.orchestrator.nodes.synthesize import SynthesisFailed, SynthesisInput, synthesize
from app.orchestrator.nodes.visualize import served_by as served_by_chart
from app.orchestrator.results import StoredResult
from app.schemas.charts import TrailStep
from app.schemas.chat import ExecuteMessageResponse, GuardResult
from app.schemas.datasets import DatasetSchema
from app.services import billing
from app.services import conversations as conv
from app.services import conversations_engine as engine
from app.services import datasets as datasets_store
from app.services import query, text2sql
from app.services.query import QueryError, QueryResultRows, QueryTable

logger = logging.getLogger("alibi.investigation")

_MODEL_DOWN = "El modelo de consultas no está disponible en este momento. Probá de nuevo en unos minutos."
_PLANNER_DOWN = "El asistente no está disponible en este momento. Probá de nuevo en unos minutos."
_SYNTHESIS_DOWN = (
    "Investigamos tu pregunta pero no pudimos redactar la respuesta. Probá de nuevo en un momento."
)


def _overview(owner_id: str, context: list[tuple[str, DatasetSchema]]) -> str:
    """The tables, as the planner sees them: names, columns, types, meanings.

    Never DDL and never the SQL identifiers' quoting rules — the planner does not
    write SQL. It needs to know what can be asked about, in business terms.
    """
    lines = ["TABLES:"]
    for dataset_id, schema in context:
        dataset = datasets_store.get_dataset(owner_id, dataset_id)
        name = dataset.name if dataset is not None else dataset_id
        head = f'- "{name}" ({schema.rowCount} rows)'
        if schema.tableDescription:
            head += f" — {schema.tableDescription}"
        lines.append(head)
        for column in schema.columns:
            if not column.included:
                continue
            line = f"  - {column.name} ({column.type.value})"
            if column.description:
                line += f" — {column.description}"
            if column.sampleValues:
                line += f"; e.g. {column.sampleValues[:80]}"
            lines.append(line)
    return "\n".join(lines)


def _first_line(text: str) -> str:
    """An error's first line: DuckDB's carry a caret diagram after it."""
    lines = text.strip().splitlines()
    return " ".join(lines[0].split())[:300] if lines else text


def _ask_sql(
    context: list[tuple[str, DatasetSchema]], tables: list[QueryTable], row_limit: int
) -> harness.AskSql:
    def ask(question: str, critique: str | None, hints: str | None) -> harness.SqlOutcome:
        evidence = hints or ""
        if critique:
            # The one retry: Module A sees why its last query failed. It has no
            # conversation of its own, so the critique travels with the hints —
            # the external-knowledge slot the fine-tune was trained to read —
            # and the question stays the one the planner wrote.
            evidence = (f"{evidence} " if evidence else "") + (
                f"A previous query for this question failed with: {_first_line(critique)}. Avoid that."
            )
        try:
            generated = text2sql.generate(question, context, evidence or None)
        except text2sql.LlmUnavailable as err:
            logger.error("Text-to-SQL endpoint unavailable: %s", err)
            raise ApiException(503, "text2sql_unavailable", _MODEL_DOWN) from err
        except text2sql.SqlRejected as rejected:
            raise harness.ToolFailed(
                "sql_rejected",
                rejected.detail or rejected.message,
                rejected.raw_sql,
                user_message=rejected.message,
            ) from rejected
        except ValueError as err:
            raise ApiException(
                409, "no_queryable_data",
                "Los archivos de esta carpeta no tienen columnas disponibles para consultar.",
            ) from err
        try:
            # The plan's rows per query (Free 1.000), under the platform
            # ceiling — the same limit the one-query path applies.
            rows = query.run_sql(generated.sql, tables, limit=row_limit)
        except QueryError as exc:
            raise harness.ToolFailed(exc.code, exc.message, generated.sql) from exc
        return harness.SqlOutcome(
            sql=generated.sql,
            columns=rows.columns,
            rows=rows.rows,
            truncated=rows.truncated,
            tokens=generated.prompt_tokens + generated.completion_tokens,
        )

    return ask


def _rows(result: StoredResult) -> QueryResultRows:
    return QueryResultRows(columns=result.columns, rows=result.rows, truncated=result.truncated)


#: What the user reads for each `visualize.Outcome`, on the chart's step.
_CHART_OUTCOME = {
    "declined": "Sin gráfico: el modelo juzgó que la tabla lo muestra mejor.",
    "rejected": "Sin gráfico: lo que propuso el modelo no era un gráfico válido para estos datos.",
    "unavailable": "Sin gráfico: el modelo de gráficos no respondió.",
    "off": "Sin gráfico: no hay datos para dibujar.",
}
_CHART_NAMES = {
    "bar": "barras", "line": "líneas", "area": "área", "point": "puntos",
    "arc": "torta", "rect": "mapa de calor",
}


def _chart_detail(chart: Any, outcome: str) -> str:
    if chart is not None:
        kind = getattr(chart.chartType, "value", str(chart.chartType))
        return f"Gráfico de {_CHART_NAMES.get(kind, kind)}."
    return _CHART_OUTCOME.get(outcome, _CHART_OUTCOME["rejected"])


def _value(field: Any) -> Any:
    return field.value if field is not None and hasattr(field, "value") else field


def _trail(attempts: list[Any]) -> list[TrailStep]:
    return [
        TrailStep(
            label=a.label,
            purpose=_value(a.purpose),
            thought=a.thought,
            hints=getattr(a, "hints", None),
            tool=_value(a.tool),
            model=a.model,
            detail=a.detail,
            question=a.question,
            sql=a.sql,
            rowCount=a.rowCount,
            status="failed" if _value(a.status) == "failed" else "done",
            error=a.error,
        )
        for a in attempts
    ]


def _chart_request(question: str, form: str | None) -> str:
    """The finding's question as Module C was trained to be asked it.

    nvBench — the adapter's training data — is a request *with its form*: 100% of
    the line targets and 91% of the bar ones say "in a line chart" / "bar chart"
    (measured on `model_c` eval, 2026-10-16). The adapter learned to draw the
    chart it is asked for, very precisely, and falls back to bar when none is
    named. So the planner names one at `finish` and it is asked in nvBench's
    own words. With no form named, the question goes as written.
    """
    if not form:
        return question
    return f"{question.rstrip().rstrip('.?!')}, in a {form} chart."


def _label_of(attempts: list[Any], finding: StoredResult) -> str:
    """The label of the query step that produced a finding, for its chart step."""
    for attempt in reversed(attempts):
        if attempt.question == finding.question and attempt.sql == finding.sql:
            return str(attempt.label).removeprefix("Reintentando: ")
    return finding.question[:80]


def answer_question(
    owner_id: str,
    conversation_id: str,
    question: str,
    *,
    deep: bool,
    step: Callable[..., AbstractContextManager[Any]],
) -> ExecuteMessageResponse | GuardResult:
    """Investigate, synthesise, chart, store. Raises the run's `ApiException`s.

    A `GuardResult` when every query the planner asked for was refused by the
    guardrail — the same outcome the one-query path has for a refused query.

    Billing is the one-query path's (`conversations.prepare` / `_finish`): the
    quota is checked again here, before any model call, for a run queued just
    before it ran out; and the answered question is **one** ledger row however
    many queries the planner ran or which mode answered it, written after the answer is stored, with the cost of every
    model call the run made. A refusal or a failure costs the user nothing.
    """
    billing.require_query_allowance(owner_id)
    if not text2sql.is_configured():
        logger.error("ALIBI_TEXT2SQL_* is not configured — cannot answer questions")
        raise ApiException(503, "text2sql_unavailable", _MODEL_DOWN)
    context = conv._dataset_context(owner_id, conversation_id)
    if not context:
        raise ApiException(
            409, "no_queryable_data",
            "Esta carpeta todavía no tiene ningún archivo listo para consultar. "
            "Primero confirmá el esquema inferido de al menos un archivo.",
        )
    tables = conv._query_tables(owner_id, conversation_id)

    with usage_meter.metering() as meter:
        return _investigate_and_answer(
            owner_id, conversation_id, question, context, tables, deep=deep, step=step, meter=meter
        )


def _investigate_and_answer(
    owner_id: str,
    conversation_id: str,
    question: str,
    context: list[tuple[str, DatasetSchema]],
    tables: list[QueryTable],
    *,
    deep: bool,
    step: Callable[..., AbstractContextManager[Any]],
    meter: usage_meter.Meter,
) -> ExecuteMessageResponse | GuardResult:
    try:
        found = harness.investigate(
            question,
            data_overview=_overview(owner_id, context),
            ask_sql=_ask_sql(context, tables, billing.row_limit_for(owner_id)),
            step=step,
            deep=deep,
            sql_model=text2sql.served_by(),
        )
    except harness.PlannerUnavailable as err:
        logger.error("planner unavailable: %s", err)
        raise ApiException(503, "planner_unavailable", _PLANNER_DOWN) from err

    if not found.findings:
        failure = found.last_failure
        if failure is not None and failure.code == "sql_rejected":
            # As on the one-query path: what Module A wrote was refused by the
            # guardrail, and the user is told why as a guard message.
            return engine.blocked(question, failure.user_message)
        if failure is not None:
            raise ApiException(409, failure.code, failure.user_message)
        raise ApiException(
            409, "no_answer",
            "No encontramos cómo responder esa pregunta con los datos de esta carpeta. "
            "Probá reformularla.",
        )

    findings = found.findings
    background = [r for r in found.store.all() if r not in findings]
    labels = conv._column_labels(owner_id, conversation_id)

    chart_model = served_by_chart()
    chart_questions = [_chart_request(f.question, found.chart_forms.get(f.handle)) for f in findings]
    # One visible step per chart — Module C is a tool call like Module A's, and
    # the user should see it called and what it answered. The steps are opened
    # (and saved) here on the main thread; the workers only compute.
    with ExitStack() as chart_steps, step("Redactando la respuesta"):
        steps_for_charts = [
            chart_steps.enter_context(
                step(f"Armando el gráfico: {_label_of(found.attempts, f)}", tool="chart",
                     model=chart_model, question=asked)
            )
            for f, asked in zip(findings, chart_questions)
        ]
        with ThreadPoolExecutor(max_workers=1 + len(findings), thread_name_prefix="answer") as pool_:
            charts = [
                # `run_in_context`: a worker thread starts with an empty
                # context, and Module C's calls belong on this answer's bill.
                pool_.submit(
                    usage_meter.run_in_context(conv._visualize_with_outcome),
                    conversation_id,
                    question=asked,
                    sql=f.sql,
                    rows=_rows(f),
                    column_labels=labels,
                )
                for f, asked in zip(findings, chart_questions)
            ]
            try:
                synthesis = synthesize(
                    SynthesisInput(
                        question=question,
                        findings=findings,
                        background=background,
                        shown_figures=found.store.shown_figures(),
                        cut_short=found.cut_short,
                        column_labels=labels,
                        source_label=conv._source_label(owner_id, conversation_id),
                        deep=deep,
                    )
                )
            except SynthesisFailed as err:
                logger.error("synthesis failed: %s", err)
                raise ApiException(503, "narrative_unavailable", _SYNTHESIS_DOWN) from err
            # `_visualize_with_outcome` never raises: a chart is the optional
            # half of a finding.
            outcomes = [c.result() for c in charts]
        chart_specs = [chart for chart, _ in outcomes]
        for current, (chart, outcome) in zip(steps_for_charts, outcomes):
            current.detail = _chart_detail(chart, outcome)

    parts = [
        engine.FindingParts(
            text=synthesis.finding_texts.get(f.handle, ""),
            question=f.question,
            sql=f.sql,
            rows=_rows(f),
            chart=chart,
        )
        for f, chart in zip(findings, chart_specs)
    ]
    message, receipt = engine.build_investigation_message(
        question, synthesis.summary, parts, _trail([*found.attempts, *steps_for_charts])
    )
    conv._append_assistant_message(owner_id, conversation_id, message)
    billing.record_query(
        owner_id,
        calls=list(meter.calls),
        rows_returned=max(len(f.rows) for f in findings),
        source_name=conv._source_label(owner_id, conversation_id),
    )
    logger.info(
        "investigation for conversation %s: %d queries, %d findings, %s, tokens=%d+%d",
        conversation_id, len(found.attempts), len(findings),
        "cut short" if found.cut_short else "complete",
        found.tokens, synthesis.prompt_tokens + synthesis.completion_tokens,
    )
    return ExecuteMessageResponse(message=message, receipt=receipt)


__all__ = ["answer_question"]
