"""Module A (Text-to-SQL) unit tests — no network, no model.

Covers the three pieces that must be right before any model call is worth making:
the prompt format matching the fine-tune byte-for-byte, the read-only guardrail,
and the DuckDB dialect normalization. The model call itself is exercised via a
stubbed client so the pipeline is testable without credentials or credit.
"""

import pytest

from app.schemas.datasets import ColumnType, DatasetSchema, InferenceConfidence, SchemaColumn
from app.services import text2sql
from app.services.text2sql import ddl, formats, sql
from app.services.text2sql.client import Completion

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _col(name: str, ctype: ColumnType = ColumnType.TEXT, included: bool = True) -> SchemaColumn:
    return SchemaColumn(
        name=name,
        type=ctype,
        sampleValues="",
        included=included,
        confidence=InferenceConfidence.HIGH,
    )


@pytest.fixture
def sales_schema() -> DatasetSchema:
    return DatasetSchema(
        datasetId="ds_1",
        rowCount=1200,
        confidence=InferenceConfidence.HIGH,
        columns=[
            _col("Product"),
            _col("Sales", ColumnType.NUMBER),
            _col("Order date", ColumnType.DATE),
            _col("Quantity", ColumnType.WHOLE_NUMBER),
            _col("Internal notes", included=False),
        ],
    )


# ---------------------------------------------------------------------------
# prompt format — must match the fine-tune exactly
# ---------------------------------------------------------------------------


def test_format_matches_finetune_template() -> None:
    """The recovered template, verified against all 74,649 training rows."""
    fmt = formats.get_format("sql-create-context-v1")
    messages = fmt.build_messages("CREATE TABLE t (a VARCHAR)", "How many rows?")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == (
        "You are a text-to-SQL assistant. Given a database schema and a question in "
        "natural language, generate a single valid SQL query that answers the question. "
        "Respond with only the SQL query, no explanation."
    )
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == (
        "### Schema:\nCREATE TABLE t (a VARCHAR)\n\n### Question:\nHow many rows?"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1;  ", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("<think>hmm, count them</think>\nSELECT 1", "SELECT 1"),
    ],
)
def test_parse_completion_strips_wrappers(raw: str, expected: str) -> None:
    """Training targets were bare SQL; qwen3 can still emit reasoning or fences."""
    assert formats.get_format("sql-create-context-v1").parse_completion(raw) == expected


def test_unknown_format_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown prompt format"):
        formats.get_format("does-not-exist")


# ---------------------------------------------------------------------------
# DDL rendering
# ---------------------------------------------------------------------------


def test_ddl_is_single_line_and_omits_excluded_columns(sales_schema: DatasetSchema) -> None:
    # First element is the dataset ID, not its display name — see ddl.table_name.
    rendered = ddl.render([("ds_sales24", sales_schema)])

    assert "\n" not in rendered  # 0 of 74,649 training schemas had a newline
    assert not rendered.endswith(";")
    assert rendered == (
        "CREATE TABLE ds_sales24 "
        "(product VARCHAR, sales REAL, order_date DATE, quantity INTEGER)"
    )
    assert "notes" not in rendered


@pytest.fixture
def customers_schema() -> DatasetSchema:
    return DatasetSchema(
        datasetId="ds_2",
        rowCount=80,
        confidence=InferenceConfidence.HIGH,
        columns=[_col("Product"), _col("Seller")],
    )


class TestMultiTableSchema:
    """A conversation over several files (a selection or a folder, D6).

    Without every table in the prompt the model cannot write a JOIN — and if it
    guessed one anyway the guardrail would reject the name — so these two
    assertions are what make multi-file querying work at all.
    """

    def test_every_dataset_reaches_the_prompt(
        self, sales_schema: DatasetSchema, customers_schema: DatasetSchema
    ) -> None:
        rendered = ddl.render([("ds_sales24", sales_schema), ("ds_cust", customers_schema)])

        # Semicolon-separated on ONE line. 0 of the fine-tune's 74,649 training
        # schemas contained a newline (see formats.py), so a multi-line schema
        # block is a shape this model has never seen.
        assert "\n" not in rendered
        assert rendered == (
            "CREATE TABLE ds_sales24 "
            "(product VARCHAR, sales REAL, order_date DATE, quantity INTEGER); "
            "CREATE TABLE ds_cust (product VARCHAR, seller VARCHAR)"
        )

    def test_a_join_across_both_tables_passes_the_guardrail(
        self, sales_schema: DatasetSchema, customers_schema: DatasetSchema
    ) -> None:
        tables, columns = ddl.allowed_names(
            [("ds_sales24", sales_schema), ("ds_cust", customers_schema)]
        )
        assert tables == {"ds_sales24", "ds_cust"}
        assert {"sales", "seller"} <= columns

        checked = sql.check(
            "SELECT s.product, c.seller FROM ds_sales24 s "
            "JOIN ds_cust c ON c.product = s.product",
            read_dialect="mysql",
            allowed_tables=tables,
            allowed_columns=columns,
        )
        assert "JOIN" in checked.sql.upper()

    def test_a_table_outside_the_conversation_is_still_refused(
        self, sales_schema: DatasetSchema
    ) -> None:
        # Widening to N tables must not widen to *any* table.
        tables, columns = ddl.allowed_names([("ds_sales24", sales_schema)])
        with pytest.raises(sql.SqlRejected):
            sql.check(
                "SELECT * FROM ds_sales24 JOIN ds_other ON 1=1",
                read_dialect="mysql",
                allowed_tables=tables,
                allowed_columns=columns,
            )

    def test_an_unusable_dataset_does_not_take_the_others_down(
        self, sales_schema: DatasetSchema
    ) -> None:
        # One still-processing file in a folder of ten must not block questions
        # about the other nine.
        empty = DatasetSchema(
            datasetId="ds_3",
            rowCount=0,
            confidence=InferenceConfidence.LOW,
            columns=[_col("a", included=False)],
        )
        rendered = ddl.render([("ds_sales24", sales_schema), ("ds_empty", empty)])
        assert "ds_empty" not in rendered
        assert "ds_sales24" in rendered

    def test_nothing_queryable_at_all_is_an_error(self) -> None:
        empty = DatasetSchema(
            datasetId="ds_3",
            rowCount=0,
            confidence=InferenceConfidence.LOW,
            columns=[_col("a", included=False)],
        )
        with pytest.raises(ValueError, match="no dataset"):
            ddl.render([("ds_empty", empty)])


def test_table_name_is_stable_across_renames() -> None:
    """Deriving the table from the mutable display name would break saved queries."""
    assert ddl.table_name("ds_a8Kf2") == "ds_a8kf2"


def test_identifiers_strip_accents_and_fix_leading_digits() -> None:
    assert ddl.sql_identifier("Año de compra") == "ano_de_compra"
    assert ddl.sql_identifier("2024 sales") == "t_2024_sales"
    assert ddl.sql_identifier("!!!") == "dataset"


def test_schema_with_no_included_columns_is_rejected() -> None:
    schema = DatasetSchema(
        datasetId="ds_2",
        rowCount=0,
        confidence=InferenceConfidence.LOW,
        columns=[_col("a", included=False)],
    )
    # `render_one`, not `render`: the plural form deliberately skips an unusable
    # dataset so it doesn't take a folder's other files down with it, and reports
    # a different error only when nothing at all is left.
    with pytest.raises(ValueError, match="no included columns"):
        ddl.render_one("empty", schema)


# ---------------------------------------------------------------------------
# guardrail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        "DROP TABLE sales",
        "DELETE FROM sales",
        "UPDATE sales SET product = 'x'",
        "INSERT INTO sales VALUES (1)",
        "TRUNCATE TABLE sales",
        "ALTER TABLE sales ADD COLUMN x INT",
        "CREATE TABLE evil (a INT)",
        "SELECT 1; DROP TABLE sales",  # multi-statement injection
        "WITH x AS (DELETE FROM sales RETURNING *) SELECT * FROM x",  # nested write
    ],
)
def test_guardrail_rejects_writes(malicious: str) -> None:
    with pytest.raises(sql.SqlRejected):
        sql.check(malicious, read_dialect="mysql")


def test_guardrail_rejects_tables_outside_the_dataset() -> None:
    """A hallucinated or injected reference to the app's own tables must not pass."""
    with pytest.raises(sql.SqlRejected) as caught:
        sql.check(
            "SELECT email FROM users",
            read_dialect="mysql",
            allowed_tables={"sales"},
            allowed_columns={"product"},
        )
    # `.message` is the user-facing copy; str(exc) carries the technical detail.
    assert "tablas" in caught.value.message
    assert "users" in caught.value.detail


def test_guardrail_rejects_unknown_columns() -> None:
    with pytest.raises(sql.SqlRejected) as caught:
        sql.check(
            "SELECT password FROM sales",
            read_dialect="mysql",
            allowed_tables={"sales"},
            allowed_columns={"product"},
        )
    assert "columnas" in caught.value.message
    assert "password" in caught.value.detail


def test_guardrail_allows_ctes_and_aliases() -> None:
    checked = sql.check(
        "WITH top AS (SELECT product AS name FROM sales) SELECT name FROM top",
        read_dialect="mysql",
        allowed_tables={"sales"},
        allowed_columns={"product"},
    )
    assert checked.sql  # CTE name and alias are locally defined, not outside references


@pytest.mark.parametrize(
    "set_op",
    [
        "SELECT a FROM t WHERE b = 1 UNION SELECT a FROM t WHERE b = 2",
        "SELECT a FROM t WHERE b = 1 INTERSECT SELECT a FROM t WHERE b = 2",
        "SELECT a FROM t WHERE b = 1 EXCEPT SELECT a FROM t WHERE b = 2",
    ],
)
def test_guardrail_allows_set_operations(set_op: str) -> None:
    """UNION/INTERSECT/EXCEPT are read-only; the fine-tune emits all three."""
    checked = sql.check(
        set_op, read_dialect="mysql", allowed_tables={"t"}, allowed_columns={"a", "b"}
    )
    assert checked.sql


def test_guardrail_rejects_empty_and_unparseable() -> None:
    with pytest.raises(sql.SqlRejected):
        sql.check("   ", read_dialect="mysql")


# ---------------------------------------------------------------------------
# dialect normalization — the fine-tune's output is not valid DuckDB as-is
# ---------------------------------------------------------------------------


def test_double_quoted_literals_become_single_quoted() -> None:
    """83.3% of the fine-tune's training targets look like this.

    DuckDB reads `"joyce bernal"` as an identifier and fails with
    'Referenced column not found'. Reading as MySQL and writing as DuckDB fixes it.
    """
    checked = sql.check(
        'SELECT title FROM movies WHERE director = "joyce bernal"',
        read_dialect="mysql",
        allowed_tables={"movies"},
        allowed_columns={"title", "director"},
    )
    assert "'joyce bernal'" in checked.sql
    assert '"joyce bernal"' not in checked.sql
    # The untouched model output is preserved for debugging and evaluation.
    assert checked.raw == 'SELECT title FROM movies WHERE director = "joyce bernal"'


def test_reserved_words_get_quoted() -> None:
    """The training data emits `SELECT order FROM ...`, which DuckDB rejects."""
    checked = sql.check(
        "SELECT order FROM t",
        read_dialect="mysql",
        allowed_tables={"t"},
        allowed_columns={"order"},
    )
    assert '"order"' in checked.sql


# ---------------------------------------------------------------------------
# end-to-end pipeline with a stubbed model
# ---------------------------------------------------------------------------


def test_generate_end_to_end(
    monkeypatch: pytest.MonkeyPatch, sales_schema: DatasetSchema
) -> None:
    prompts: list[list[dict[str, str]]] = []

    def fake_complete(config, messages, **kwargs):  # noqa: ANN001, ANN202, ARG001
        prompts.append(messages)
        # Mimic the fine-tune's real output style: double-quoted literal, no ';'.
        return Completion(
            text='SELECT product FROM ds_sales24 WHERE product = "chair"',
            model="qwen3-32b-ft",
            prompt_tokens=120,
            completion_tokens=18,
        )

    monkeypatch.setattr(text2sql, "complete", fake_complete)
    monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "qwen3-32b-ft")
    text2sql.get_settings.cache_clear()

    try:
        result = text2sql.generate("Which products are chairs?", [("ds_sales24", sales_schema)])
    finally:
        text2sql.get_settings.cache_clear()

    assert result.sql == "SELECT product FROM ds_sales24 WHERE product = 'chair'"
    assert result.raw_sql.endswith('"chair"')  # raw model output kept intact
    assert result.format_name == "sql-create-context-v1"
    assert result.prompt_tokens == 120
    assert prompts[0][1]["content"].startswith("### Schema:\nCREATE TABLE ds_sales24")


def test_is_configured_is_false_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credentials -> callers fall back to fixtures, same as ALIBI_DATABASE_URL."""
    for var in ("ALIBI_TEXT2SQL_MODEL_ID",):
        monkeypatch.delenv(var, raising=False)
    text2sql.get_settings.cache_clear()
    try:
        assert text2sql.is_configured() is False
    finally:
        text2sql.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# wiring: the conversations routes actually go through the model
# ---------------------------------------------------------------------------


def _login() -> dict[str, str]:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"})
    return {"Authorization": f"Bearer {login.json()['accessToken']}"}


@pytest.fixture
def api():  # noqa: ANN201
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app), _login()


@pytest.fixture
def model_configured(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Point the settings at a fake endpoint and hand back a reply-setter."""
    monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "qwen3-32b-ft")
    text2sql.get_settings.cache_clear()

    captured: dict[str, object] = {}

    def set_reply(sql_text: str) -> None:
        def fake_complete(config, messages, **kwargs):  # noqa: ANN001, ANN202, ARG001
            captured["messages"] = messages
            return Completion(
                text=sql_text, model="qwen3-32b-ft", prompt_tokens=100, completion_tokens=20
            )

        monkeypatch.setattr(text2sql, "complete", fake_complete)

    yield set_reply, captured
    text2sql.get_settings.cache_clear()


def test_prepare_uses_the_model_and_the_real_dataset_schema(api, model_configured) -> None:  # noqa: ANN001
    """The prompt must carry the fixture dataset's actual DDL, not a canned one."""
    client, auth = api
    set_reply, captured = model_configured
    set_reply('SELECT region, SUM(sales) FROM sales GROUP BY region')

    res = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"question": "Total sales by region"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "prepared"
    assert body["sql"] == "SELECT region, SUM(sales) FROM sales GROUP BY region"

    # The DDL sent to the model comes from the `sales` fixture schema, and the
    # table is named after the dataset ID (stable across renames).
    sent = captured["messages"][1]["content"]
    assert "CREATE TABLE sales (" in sent
    assert "region VARCHAR" in sent
    assert "date DATE" in sent
    assert "units INTEGER" in sent
    assert sent.endswith("### Question:\nTotal sales by region")


def test_the_draft_carries_the_generated_sql(api, model_configured) -> None:  # noqa: ANN001
    """What the user is asked to confirm is what the model actually wrote.

    This used to assert the same thing through `execute`, over the seeded
    `conv_sales_region`. It cannot any more, and the reason is the point: that
    conversation names datasets with no materialised Parquet, so there is nothing
    to run and `execute` now reports rather than answering from a fixture. The
    round trip through a real execution is covered in `test_chat_execution.py`.
    """
    client, auth = api
    set_reply, _ = model_configured
    generated = "SELECT product FROM sales WHERE region = 'Salta'"
    set_reply(generated)

    prepared = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"question": "Which products in the Salta region?"},
    )

    assert prepared.status_code == 200
    assert prepared.json()["sql"] == generated


def test_model_output_is_normalized_before_the_user_sees_it(api, model_configured) -> None:  # noqa: ANN001
    """The fine-tune's double-quoted literals must never reach the UI or DuckDB."""
    client, auth = api
    set_reply, _ = model_configured
    set_reply('SELECT product FROM sales WHERE region = "Salta"')

    res = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"question": "products in Salta"},
    )

    assert res.status_code == 200
    assert res.json()["sql"] == "SELECT product FROM sales WHERE region = 'Salta'"


def test_hallucinated_table_becomes_a_guard_message(api, model_configured) -> None:  # noqa: ANN001
    """Reaching outside the dataset is refused, and the user is told why."""
    client, auth = api
    set_reply, _ = model_configured
    set_reply("SELECT email FROM users")

    res = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"question": "list the emails"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "blocked"
    assert "tablas" in body["message"]


def test_endpoint_down_is_a_503_not_silent_fixture_sql(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """A configured-but-unreachable model must fail visibly."""
    client, auth = api
    monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "qwen3-32b-ft")
    text2sql.get_settings.cache_clear()

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise text2sql.LlmUnavailable("connection refused")

    monkeypatch.setattr(text2sql, "complete", boom)
    try:
        res = client.post(
            "/api/v1/conversations/conv_sales_region/messages/prepare",
            headers=auth,
            json={"question": "total sales"},
        )
    finally:
        text2sql.get_settings.cache_clear()

    assert res.status_code == 503
    assert res.json()["error"]["code"] == "text2sql_unavailable"


def test_hand_edited_sql_gets_the_same_ast_guardrail(api) -> None:  # noqa: ANN001
    """No model needed: user-authored SQL is validated and scoped too."""
    client, auth = api

    ok = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"sql": "SELECT region FROM sales"},
    )
    assert ok.status_code == 200

    outside = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"sql": "SELECT email FROM users"},
    )
    assert outside.status_code == 422

    write = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=auth,
        json={"sql": "DROP TABLE sales"},
    )
    assert write.status_code == 422
