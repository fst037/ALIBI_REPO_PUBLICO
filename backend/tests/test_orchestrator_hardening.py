"""Regressions for the defects found reviewing the Module A + orchestrator work.

Each class here corresponds to one bug that was live on `feature/orchestrator`.
They are grouped in one file deliberately: they are unrelated as code but they
are one story, and a reader asking "what was wrong with the orchestrator?"
should be able to read the answer in one place.
"""

import os

import pytest

from app.orchestrator.contracts import ExplainInput
from app.services import conversations_engine as engine
from app.services import query
from app.services.query import QueryResultRows
from app.services.text2sql import formats
from app.services.text2sql import sql as text2sql_sql

DATASET_TABLES = {"ds_a8kf2"}
DATASET_COLUMNS = {"region", "revenue"}


def check(sql: str, dialect: str = "mysql"):
    return text2sql_sql.check(
        sql,
        read_dialect=dialect,
        allowed_tables=DATASET_TABLES,
        allowed_columns=DATASET_COLUMNS,
    )


class TestModuleAGuardrailBlocksExternalReads:
    """A generated query must not reach data we never registered.

    The table allow-list alone does not do this. `FROM read_parquet('s3://…')`
    parses as a table node with an *empty* name, so a check that only inspects
    named tables sees a query referencing nothing and lets it through — and
    `execute` then ran it. The reachable version of this is not a malicious
    user: it is a prompt-injection payload inside an uploaded file (P2SQL),
    which reaches the model without passing through anyone's phrasing.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM read_parquet('s3://other-tenant/private.parquet')",
            "SELECT * FROM read_csv('/etc/passwd')",
            "SELECT * FROM read_json_auto('s3://other-tenant/dump.json')",
            "SELECT * FROM postgres_scan('host=db dbname=alibi', 'public', 'users')",
        ],
    )
    def test_a_table_valued_function_is_refused(self, sql: str) -> None:
        with pytest.raises(text2sql_sql.SqlRejected):
            check(sql)

    def test_a_file_function_hidden_in_a_subquery_is_refused(self) -> None:
        with pytest.raises(text2sql_sql.SqlRejected):
            check(
                "SELECT region FROM ds_a8kf2 "
                "WHERE revenue > (SELECT max(x) FROM read_csv('/etc/passwd'))"
            )

    def test_ordinary_queries_still_pass(self) -> None:
        assert check("SELECT region, revenue FROM ds_a8kf2 ORDER BY revenue DESC").sql
        assert check("SELECT region FROM ds_a8kf2 UNION SELECT region FROM ds_a8kf2").sql

    def test_the_two_guardrails_share_one_function_list(self) -> None:
        """Not two hand-kept copies: the drift between them is what was exploitable."""
        assert "read_parquet" in query.FORBIDDEN_FUNCTIONS
        assert text2sql_sql.FORBIDDEN_FUNCTIONS is query.FORBIDDEN_FUNCTIONS


class TestWriteIntentGuardIsPrecise:
    """The phrasing check is a courtesy, so a false positive is the expensive error.

    It refuses the user's question outright with "This query was blocked because
    it modifies data". Firing that on a legitimate read is worse than not firing
    at all — the real boundary is the AST guardrail on the generated SQL.
    """

    @pytest.mark.parametrize(
        "question",
        [
            "How many orders were updated last month?",
            "Which products had modifications in Q3?",
            "Insert-only customers by cohort",
            "Top 10 updated records",
            "What was the delete rate per campaign?",
            "Revenue for customers we removed from the trial",
            "Show revenue by region",
        ],
    )
    def test_a_read_question_is_not_refused(self, question: str) -> None:
        assert not engine.is_write(question)

    @pytest.mark.parametrize(
        "question",
        [
            "delete the duplicates",
            "Delete all rows where region is null",
            "please drop table sales",
            "Can you update the prices to 0",
            "DROP TABLE users",
            "I want to remove the test accounts",
            "truncate everything",
            "Show me sales; delete from orders",
            "borra los duplicados",
            "elimina las filas vacias",
        ],
    )
    def test_a_stated_intent_to_mutate_is_still_refused(self, question: str) -> None:
        assert engine.is_write(question)


class TestHandEditedSqlNeverReachesTheNarrative:
    """`explain_result` guarantees the model is never shown SQL. It was.

    The hand-edited path called `make_draft(sql, checked_sql)`, putting the query
    in the *question* slot — which `build_assistant_message` forwards to
    `ExplainInput.question`, i.e. straight into the prompt as "The user asked:
    SELECT …". It also made every numeric literal in the user's WHERE clause
    count as a grounded figure the narrative was allowed to cite.
    """

    def test_a_sql_draft_carries_no_natural_language_question(self) -> None:
        prepared = engine.make_sql_draft("SELECT 1", "SELECT 1")
        draft = engine.pop_draft(prepared.draftId)
        assert draft is not None
        assert draft.question == "SELECT 1"  # still displayed as the user's turn
        assert draft.nl_question == ""  # but not offered as a question

    def test_a_question_draft_still_carries_one(self) -> None:
        prepared = engine.make_draft("which region sold the most?", "SELECT 1")
        draft = engine.pop_draft(prepared.draftId)
        assert draft is not None
        assert draft.nl_question == "which region sold the most?"

    def test_the_prompt_omits_the_query_when_there_is_no_question(self) -> None:
        from app.orchestrator.nodes.explain_result import _data_block

        payload = ExplainInput(
            question="",
            sql_query="SELECT region FROM ds_a8kf2 WHERE revenue > 9999",
            columns=["region", "revenue"],
            rows=[{"region": "North", "revenue": 10.0}],
            row_count=1,
        )
        block = _data_block(payload, 30)
        assert "SELECT" not in block
        assert "9999" not in block

    def test_a_where_clause_literal_is_not_a_grounded_figure(self) -> None:
        """The bug's real consequence: the validator would have accepted "9999"."""
        from app.orchestrator import validation

        rows = [{"region": "North", "revenue": 10.0}]
        problems = validation.check_grounding(
            "North reached 9999.", rows, question="", row_count=1
        )
        assert problems, "a figure absent from the rows must be rejected"


class TestDraftsDoNotAccumulate:
    """Only `execute` removed an entry, and users abandon drafts constantly."""

    def test_the_store_is_capped(self) -> None:
        engine._drafts.clear()
        for i in range(engine._MAX_DRAFTS + 50):
            engine.make_draft(f"question {i}", "SELECT 1")
        assert len(engine._drafts) <= engine._MAX_DRAFTS

    def test_an_expired_draft_is_dropped(self, monkeypatch) -> None:
        engine._drafts.clear()
        prepared = engine.make_draft("old question", "SELECT 1")
        # Jump past the TTL, then prepare again so the prune runs. Offset from
        # the real clock: `monotonic()` counts from an arbitrary origin, so a
        # small absolute value would read as being *before* the first draft.
        later = engine.time.monotonic() + engine._DRAFT_TTL_SECONDS + 60
        monkeypatch.setattr(engine.time, "monotonic", lambda: later)
        engine.make_draft("new question", "SELECT 1")
        assert engine.pop_draft(prepared.draftId) is None


class TestGenericChatFormat:
    """A general chat model needs its own format, because of the dialect.

    Reading a general model's output as MySQL rewrites a quoted *identifier*
    into a string *literal*. The result parses, passes the guardrail, executes
    without error, and returns the same constant on every row — so nothing
    anywhere reports a problem.
    """

    def test_it_is_registered(self) -> None:
        assert formats.get_format("generic-chat-v1").sql_dialect_in == "duckdb"

    def test_a_quoted_identifier_survives(self) -> None:
        fmt = formats.get_format("generic-chat-v1")
        checked = text2sql_sql.check(
            'SELECT "region" FROM ds_a8kf2',
            read_dialect=fmt.sql_dialect_in,
            allowed_tables=DATASET_TABLES,
            allowed_columns=DATASET_COLUMNS,
        )
        assert '"region"' in checked.sql

    def test_the_fine_tune_format_would_have_corrupted_it(self) -> None:
        """Documents exactly what the misconfiguration does, so it stays fixed."""
        fine_tune = formats.get_format("sql-create-context-v1")
        checked = text2sql_sql.check(
            'SELECT "region" FROM ds_a8kf2',
            read_dialect=fine_tune.sql_dialect_in,
            allowed_tables=DATASET_TABLES,
            allowed_columns=None,
        )
        assert "'region'" in checked.sql  # a constant, not the column

    def test_it_strips_fences_a_chat_model_adds(self) -> None:
        fmt = formats.get_format("generic-chat-v1")
        assert fmt.parse_completion(
            "```sql\nSELECT region FROM ds_a8kf2;\n```"
        ) == "SELECT region FROM ds_a8kf2"


class TestTheSuiteIsIsolatedFromTheDeveloperEnvironment:
    """`.env` must not reach a test run.

    Not a style point: with `ALIBI_TEXT2SQL_*` / `ALIBI_ORCHESTRATOR_*` set
    locally — which is the documented way to run the product — the suite would
    otherwise make real, billed model calls on every invocation, and assert
    against whatever they returned.
    """

    def test_the_env_file_is_disabled(self) -> None:
        assert not os.path.exists(os.environ["ALIBI_ENV_FILE"])

    def test_no_real_credentials_are_present(self) -> None:
        """Asserted on the settings, not on `is_configured()`.

        `conftest._no_provider_calls` deliberately makes the orchestrator client
        *look* configured — it substitutes a stub transport so the node has
        something to talk to, since there is no offline fallback any more.
        Checking `is_configured()` would therefore pass while a real key sat in
        the environment. What must be true is that no credential ever reached
        the settings in the first place.
        """
        from app.core.config import get_settings

        settings = get_settings()
        assert not settings.text2sql_model_id
        assert not settings.orchestrator_model_id
        assert not settings.schema_inference_model_id
        assert not settings.embedding_model_id

    def test_ingestion_runs_in_process(self) -> None:
        """`lambda` would need boto3 and a deployed function to exist."""
        from app.core.config import get_settings

        assert get_settings().job_runner.value == "thread"


class TestTheNarrativeGetsItsContext:
    """Two inputs the nodes accept and nothing ever supplied."""

    def test_a_source_label_reaches_the_prompt(self) -> None:
        from app.orchestrator.nodes.explain_result import _data_block

        payload = ExplainInput(
            question="which region sold the most?",
            sql_query="SELECT 1",
            columns=["region", "revenue"],
            rows=[{"region": "North", "revenue": 10.0}],
            row_count=1,
            source_label="Q1 sales export",
        )
        assert "Q1 sales export" in _data_block(payload, 30)

    def test_a_business_label_replaces_the_internal_name(self) -> None:
        from app.orchestrator.nodes.explain_result import _data_block

        payload = ExplainInput(
            question="when is it due?",
            sql_query="SELECT 1",
            columns=["fch_vcto"],
            rows=[{"fch_vcto": "2026-03-01"}],
            row_count=1,
            column_labels={"fch_vcto": "Due date"},
        )
        block = _data_block(payload, 30)
        assert "Due date" in block
        assert "fch_vcto" not in block


class TestExecutionRevalidates:
    """`execute` used to pass `validate=False`, trusting a verdict from elsewhere.

    The two guards are not the same guard. This one knows which tables are
    actually registered on *this* connection, and it is the last thing to run
    before DuckDB sees the query.
    """

    def test_run_sql_validates_by_default(self) -> None:
        import inspect

        assert inspect.signature(query.run_sql).parameters["validate"].default is True

    def test_a_hostile_query_is_refused_at_execution(self, tmp_path) -> None:
        """Even reaching `run_sql` directly with a draft nothing re-checked."""
        table = query.QueryTable(name="ds_a8kf2", uri=str(tmp_path / "x.parquet"))
        with pytest.raises(query.QueryError) as excinfo:
            query.run_sql(
                "SELECT * FROM read_parquet('s3://other-tenant/private.parquet')",
                [table],
            )
        assert excinfo.value.code == "forbidden_function"

    def test_execute_reaches_it(self, client, auth_headers) -> None:
        """The route still works end to end with validation on."""
        created = client.post(
            "/api/v1/conversations",
            json={"datasetIds": ["sales"]},
            headers=auth_headers,
        )
        if created.status_code != 201:
            pytest.skip("no confirmed fixture dataset to open a conversation over")
        conversation_id = created.json()["id"]
        prepared = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"question": "which region sold the most?"},
            headers=auth_headers,
        )
        assert prepared.status_code == 200
        draft_id = prepared.json().get("draftId")
        assert draft_id
        executed = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"draftId": draft_id},
            headers=auth_headers,
        )
        assert executed.status_code == 201
        assert executed.json()["message"]["result"]["naturalResponse"]
