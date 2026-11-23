"""Module A on the fine-tuned adapter: format `qwen-bird-spider-v1` over SageMaker.

Pinned here:

* **The prompt is the trained one.** The format's copy of the system prompt and
  user template is compared against `ML/src/common/prompts.py`, the module
  training and the endpoint use — so the two cannot drift apart silently.
* **The schema has the training layout**: a multi-line `CREATE TABLE` per table,
  SQLite type names, blank line between tables.
* **The answer means in DuckDB what it meant in SQLite** — the double-quoted
  string fallback, 8-byte REAL, JULIANDAY — and nothing else changes meaning.
* **`generate` sends structure, not a prompt**, and still runs the guardrail.
* **A provider/format mismatch is "not configured"**, not a worse answer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core import sagemaker
from app.core.config import get_settings
from app.schemas.datasets import ColumnType, DatasetSchema, InferenceConfidence, SchemaColumn
from app.services import text2sql
from app.services.text2sql import ddl, formats, sql
from app.services.text2sql.sql import SqlRejected

FORMAT = formats.get_format("qwen-bird-spider-v1")
_PROMPTS = Path(__file__).resolve().parents[2] / "ML" / "src" / "common" / "prompts.py"


def _col(name: str, ctype: ColumnType = ColumnType.TEXT, included: bool = True) -> SchemaColumn:
    return SchemaColumn(
        name=name, type=ctype, sampleValues="", included=included, confidence=InferenceConfidence.HIGH
    )


def _schema(dataset_id: str, columns: list[SchemaColumn]) -> DatasetSchema:
    return DatasetSchema(datasetId=dataset_id, rowCount=10, confidence=InferenceConfidence.HIGH, columns=columns)


SALES = (
    "ds_sales",
    _schema(
        "ds_sales",
        [
            _col("Region"),
            _col("Revenue", ColumnType.NUMBER),
            _col("Order Date", ColumnType.DATE),
            _col("Units", ColumnType.WHOLE_NUMBER),
            _col("Notes", included=False),
        ],
    ),
)
CUSTOMERS = ("ds_cust", _schema("ds_cust", [_col("Customer ID"), _col("Segment")]))


def _check(raw: str, context=(SALES,)) -> str:
    tables, columns = ddl.allowed_names(list(context))
    return sql.check(raw, read_dialect="sqlite", allowed_tables=tables, allowed_columns=columns).sql


class TestTheTrainedPrompt:
    @pytest.mark.skipif(not _PROMPTS.exists(), reason="ML/ is not beside backend/ in this checkout")
    def test_matches_the_module_training_used(self) -> None:
        spec = importlib.util.spec_from_file_location("ml_common_prompts", _PROMPTS)
        assert spec is not None and spec.loader is not None
        trained = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trained)

        system, user = FORMAT.build_messages("CREATE TABLE t (\n  a TEXT\n);", "How many?")
        assert system == {"role": "system", "content": trained.SQL_SYSTEM_PROMPT}
        assert user == {"role": "user", "content": trained.sql_user("CREATE TABLE t (\n  a TEXT\n);", "How many?")}

    def test_reads_the_answer_as_sqlite(self) -> None:
        assert FORMAT.sql_dialect_in == "sqlite"


class TestTheSchemaLayout:
    def test_one_multiline_block_per_table_with_sqlite_types(self) -> None:
        rendered = FORMAT.render_schema([SALES, CUSTOMERS])
        assert rendered == (
            "CREATE TABLE ds_sales (\n"
            "  region TEXT,\n"
            "  revenue REAL,\n"
            "  order_date DATE,\n"
            "  units INTEGER\n"
            ");\n"
            "\n"
            "CREATE TABLE ds_cust (\n"
            "  customer_id TEXT,\n"
            "  segment TEXT\n"
            ");"
        )

    def test_the_chat_formats_keep_their_one_line_schema(self) -> None:
        assert "\n" not in formats.get_format("generic-chat-v1").render_schema([SALES, CUSTOMERS])


class TestSqliteMeaning:
    def test_an_unresolved_double_quoted_name_is_a_string(self) -> None:
        assert _check('SELECT units FROM ds_sales WHERE region = "North"') == (
            "SELECT units FROM ds_sales WHERE region = 'North'"
        )

    def test_a_double_quoted_real_column_stays_a_column(self) -> None:
        assert _check('SELECT "Revenue" FROM ds_sales') == 'SELECT "Revenue" FROM ds_sales'

    def test_a_double_quoted_alias_stays_a_reference(self) -> None:
        out = _check('SELECT SUM(revenue) AS total FROM ds_sales ORDER BY "total" DESC')
        assert "'total'" not in out

    def test_a_backticked_unknown_name_is_still_a_column_and_rejected(self) -> None:
        # SQLite gives only double quotes the string fallback.
        with pytest.raises(SqlRejected, match="columns"):
            _check("SELECT `profit` FROM ds_sales")

    def test_a_qualified_unknown_name_is_not_turned_into_a_string(self) -> None:
        with pytest.raises(SqlRejected, match="columns"):
            _check('SELECT T1."profit" FROM ds_sales AS T1')

    def test_real_is_an_eight_byte_double(self) -> None:
        out = _check("SELECT CAST(SUM(units) AS REAL) * 100 / COUNT(*) FROM ds_sales")
        executed = out.split(' AS "', 1)[0]  # the alias keeps the model's own text
        assert "AS DOUBLE" in executed and "REAL" not in executed

    def test_julianday_becomes_julian(self) -> None:
        out = _check("SELECT JULIANDAY(MAX(order_date)) - JULIANDAY(MIN(order_date)) FROM ds_sales")
        executed = out.split(' AS "', 1)[0]
        assert executed.count("JULIAN(CAST(") == 2 and "JULIANDAY" not in executed.upper()

    def test_unnamed_columns_get_the_names_sqlite_gives_them(self) -> None:
        out = _check("SELECT region, COUNT(*), SUM(revenue) FROM ds_sales GROUP BY region ORDER BY COUNT(*) DESC")
        assert out == (
            'SELECT region, COUNT(*) AS "COUNT(*)", SUM(revenue) AS "SUM(revenue)" '
            "FROM ds_sales GROUP BY region ORDER BY COUNT(*) DESC"
        )

    def test_a_generated_name_drops_the_joins_table_aliases(self) -> None:
        out = _check(
            "SELECT T2.segment, SUM(T1.units) FROM ds_sales AS T1 JOIN ds_cust AS T2 "
            "ON T1.region = T2.segment GROUP BY T2.segment",
            context=(SALES, CUSTOMERS),
        )
        assert 'SUM(T1.units) AS "SUM(units)"' in out

    def test_named_columns_and_stars_keep_their_names(self) -> None:
        assert _check("SELECT SUM(revenue) AS total, region FROM ds_sales GROUP BY region") == (
            "SELECT SUM(revenue) AS total, region FROM ds_sales GROUP BY region"
        )
        assert _check("SELECT * FROM ds_sales") == "SELECT * FROM ds_sales"

    def test_a_repeated_expression_gets_a_distinct_name(self) -> None:
        out = _check("SELECT SUM(units), SUM(units) FROM ds_sales")
        assert '"SUM(units)"' in out and '"SUM(units)_2"' in out

    def test_text_operators_read_a_date_as_text(self) -> None:
        out = _check("SELECT SUM(units) FROM ds_sales WHERE order_date LIKE '2023%' AND SUBSTR(order_date, 6, 2) = '05'")
        assert "CAST(order_date AS TEXT) ILIKE '2023%'" in out
        assert "SUBSTRING(CAST(order_date AS TEXT), 6, 2)" in out

    def test_like_ignores_case_and_an_existing_text_cast_is_not_doubled(self) -> None:
        out = _check("SELECT units FROM ds_sales WHERE CAST(region AS TEXT) LIKE 'north'")
        assert out == "SELECT units FROM ds_sales WHERE CAST(region AS TEXT) ILIKE 'north'"

    def test_sqlite_functions_translate(self) -> None:
        out = _check("SELECT strftime('%Y', order_date) AS y, SUM(revenue) FROM ds_sales GROUP BY y")
        assert "STRFTIME(CAST(order_date AS TIMESTAMP), '%Y')" in out

    def test_an_inner_join_with_no_on_is_a_cross_join_and_its_ons_move_to_where(self) -> None:
        # The live failure (2026-10-16): an ON naming a table joined after it,
        # then a join with no ON at all — SQLite runs it, DuckDB cannot parse it.
        out = _check(
            "SELECT T2.segment, SUM(T1.units) FROM ds_sales AS T1 INNER JOIN ds_cust AS T2 "
            "ON T1.region = T3.segment INNER JOIN ds_cust AS T3 WHERE T3.segment = 'Retail' OR T3.segment = 'Pyme' "
            "GROUP BY T2.segment",
            context=(SALES, CUSTOMERS),
        )
        assert "INNER JOIN" not in out and " ON " not in out
        assert "WHERE (T3.segment = 'Retail' OR T3.segment = 'Pyme') AND T1.region = T3.segment" in out

    def test_joins_that_all_have_a_condition_are_left_alone(self) -> None:
        raw = "SELECT T1.units FROM ds_sales AS T1 INNER JOIN ds_cust AS T2 ON T1.region = T2.segment"
        assert _check(raw, context=(SALES, CUSTOMERS)) == raw

    def test_next_to_an_outer_join_only_the_bare_join_changes(self) -> None:
        out = _check(
            "SELECT T1.units FROM ds_sales AS T1 LEFT JOIN ds_cust AS T2 ON T1.region = T2.segment "
            "INNER JOIN ds_cust AS T3",
            context=(SALES, CUSTOMERS),
        )
        assert "LEFT JOIN ds_cust AS T2 ON T1.region = T2.segment CROSS JOIN ds_cust AS T3" in out

    def test_the_translations_execute_in_duckdb(self) -> None:
        import duckdb

        con = duckdb.connect()
        con.execute(
            "CREATE TABLE ds_sales AS SELECT * FROM (VALUES "
            "('North', 10.0, DATE '2024-01-01', 1), ('South', 20.0, DATE '2024-01-04', 2)"
            ") t(region, revenue, order_date, units)"
        )
        for raw, expected in [
            ('SELECT SUM(revenue) FROM ds_sales WHERE region = "North"', 10.0),
            ("SELECT CAST(SUM(units) AS REAL) / 3 FROM ds_sales", 1.0),
            ("SELECT JULIANDAY(MAX(order_date)) - JULIANDAY(MIN(order_date)) FROM ds_sales", 3.0),
            # The two live questions that failed before text operators were cast.
            ("SELECT SUM(units) FROM ds_sales WHERE order_date LIKE '2024%' AND region = 'North'", 1),
            ("SELECT COUNT(*) FROM ds_sales WHERE SUBSTR(order_date, 1, 4) = '2024' AND region LIKE 'south'", 1),
            ("SELECT SUM(T1.units) FROM ds_sales AS T1 INNER JOIN ds_sales AS T2 ON T1.region = T3.region "
             "INNER JOIN ds_sales AS T3 WHERE T3.region = 'North'", 2),  # T2 is a cross join, as in SQLite
        ]:
            assert con.execute(_check(raw)).fetchone()[0] == pytest.approx(expected, abs=1e-12)

    def test_other_dialects_are_untouched(self) -> None:
        tables, columns = ddl.allowed_names([SALES])
        out = sql.check(
            'SELECT region FROM ds_sales WHERE region = "North"',
            read_dialect="duckdb",
            allowed_tables=tables,
            allowed_columns=columns | {"north"},
        ).sql
        assert '"North"' in out


@pytest.fixture
def on_sagemaker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALIBI_TEXT2SQL_PROVIDER", "sagemaker")
    monkeypatch.setenv("ALIBI_TEXT2SQL_PROMPT_FORMAT", "qwen-bird-spider-v1")
    monkeypatch.setenv("ALIBI_SAGEMAKER_ENDPOINT_NAME", "model-a-dev")
    monkeypatch.setenv("ALIBI_SAGEMAKER_INFERENCE_COMPONENT", "alibi-models-ic")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGenerateOnSageMaker:
    def test_sends_the_schema_and_question_and_checks_the_reply(
        self, on_sagemaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[tuple[sagemaker.Target, dict]] = []

        def _invoke(target, payload):
            sent.append((target, payload))
            return {"task": "sql", "sql": 'SELECT SUM(revenue) FROM ds_sales WHERE region = "North";', "valid": True}

        monkeypatch.setattr(sagemaker, "invoke", _invoke)

        generated = text2sql.generate("  Revenue in the North?  ", [SALES])

        target, payload = sent[0]
        assert target.endpoint_name == "model-a-dev" and target.inference_component == "alibi-models-ic"
        assert payload == {
            "task": "sql",
            "schema": FORMAT.render_schema([SALES]),
            "question": "Revenue in the North?",
        }
        assert generated.sql == """SELECT SUM(revenue) AS "SUM(revenue)" FROM ds_sales WHERE region = 'North'"""
        assert generated.format_name == "qwen-bird-spider-v1"
        assert generated.model == "sagemaker:model-a-dev"

    def test_hints_go_in_the_external_knowledge_slot_it_was_trained_on(
        self, on_sagemaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[dict] = []
        monkeypatch.setattr(
            sagemaker, "invoke", lambda _t, payload: sent.append(payload) or {"task": "sql", "sql": "SELECT units FROM ds_sales"}
        )
        text2sql.generate("Units in the North?", [SALES], "north refers to region = 'North';\n  units are units")
        # `ML/src/model_a/prepare_data.py`: "-- External knowledge: {evidence}\n\n" + DDL.
        assert sent[0]["schema"] == (
            "-- External knowledge: north refers to region = 'North'; units are units\n\n"
            + FORMAT.render_schema([SALES])
        )
        assert sent[0]["question"] == "Units in the North?"

    def test_the_guardrail_still_runs(self, on_sagemaker, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sagemaker, "invoke", lambda *_a: {"task": "sql", "sql": "DROP TABLE ds_sales"})
        with pytest.raises(SqlRejected):
            text2sql.generate("drop it", [SALES])

    def test_an_unreachable_endpoint_is_unavailable(self, on_sagemaker, monkeypatch: pytest.MonkeyPatch) -> None:
        def _asleep(*_a):
            raise sagemaker.ModelAsleep("no running copy")

        monkeypatch.setattr(sagemaker, "invoke", _asleep)
        with pytest.raises(text2sql.LlmUnavailable):
            text2sql.generate("anything", [SALES])

    def test_is_configured(self, on_sagemaker) -> None:
        assert text2sql.is_configured()


class TestMismatchesAreNotConfigured:
    def test_the_endpoint_with_a_chat_format(self, on_sagemaker, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIBI_TEXT2SQL_PROMPT_FORMAT", "generic-chat-v1")
        get_settings.cache_clear()
        assert not text2sql.is_configured()

    def test_the_fine_tune_format_on_bedrock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "us.anthropic.claude-haiku")
        monkeypatch.setenv("ALIBI_TEXT2SQL_PROMPT_FORMAT", "qwen-bird-spider-v1")
        get_settings.cache_clear()
        try:
            assert not text2sql.is_configured()
        finally:
            get_settings.cache_clear()

    def test_sagemaker_with_no_endpoint(self, on_sagemaker, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIBI_SAGEMAKER_ENDPOINT_NAME", "")
        get_settings.cache_clear()
        assert not text2sql.is_configured()
