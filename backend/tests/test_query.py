"""The DuckDB query layer and its read-only guard.

Module A generates this SQL with a language model and the UI lets the user edit
it before running, so both inputs are untrusted. The guard tests below are the
security boundary for the data plane — particularly `read_parquet` on an
arbitrary path, which is the shortest route from a generated query to another
tenant's file.
"""

import tempfile

import pytest

from app.ingest.pipeline import run_pipeline
from app.ingest.storage import LocalFileStore, dataset_parquet_key
from app.ingest.writer import to_parquet_bytes
from app.services.query import (
    QueryError,
    QueryTable,
    preview,
    run_sql,
    table_for_dataset,
    validate_sql,
)


@pytest.fixture
def store() -> LocalFileStore:
    return LocalFileStore(tempfile.mkdtemp())


@pytest.fixture
def ventas(store: LocalFileStore) -> QueryTable:
    outcome = run_pipeline(
        "ventas.csv",
        (
            "ciudad;monto;fecha\n"
            "Córdoba;1.234,56;01/02/2024\n"
            "Rosario;980,00;15/03/2024\n"
            "Mendoza;2.500,10;28/12/2024\n"
        ).encode(),
    )
    key = dataset_parquet_key("u_ana", "ds_ventas")
    store.put(key, to_parquet_bytes(outcome.tables[0].typed.frame))
    return QueryTable(name="ventas", uri=store.uri_for(key))


@pytest.fixture
def clientes(store: LocalFileStore) -> QueryTable:
    outcome = run_pipeline(
        "clientes.csv", "ciudad,vendedor\nCórdoba,Ana\nRosario,Beto\n".encode()
    )
    key = dataset_parquet_key("u_ana", "ds_clientes")
    store.put(key, to_parquet_bytes(outcome.tables[0].typed.frame))
    return QueryTable(name="clientes", uri=store.uri_for(key))


class TestExecution:
    def test_reads_the_parquet_with_real_types(self, ventas: QueryTable) -> None:
        result = run_sql("SELECT ciudad, monto FROM ventas ORDER BY monto DESC", [ventas])
        assert result.columns == ["ciudad", "monto"]
        assert result.rows[0] == {"ciudad": "Mendoza", "monto": 2500.10}

    def test_aggregation_works_because_numbers_are_numbers(self, ventas: QueryTable) -> None:
        # If typing had left `monto` as text this would fail — which is the
        # whole reason the es-AR number handling exists.
        result = run_sql("SELECT SUM(monto) AS total FROM ventas", [ventas])
        assert round(result.rows[0]["total"], 2) == 4714.66

    def test_dates_are_json_safe(self, ventas: QueryTable) -> None:
        result = run_sql("SELECT fecha FROM ventas ORDER BY fecha", [ventas])
        # A date object would blow up at response encoding, far from here.
        assert result.rows[0]["fecha"] == "2024-02-01"

    def test_join_across_two_datasets(self, ventas: QueryTable, clientes: QueryTable) -> None:
        # A folder-scoped conversation (decision D6) is exactly this.
        result = run_sql(
            "SELECT v.ciudad, c.vendedor FROM ventas v "
            "JOIN clientes c ON c.ciudad = v.ciudad ORDER BY v.ciudad",
            [ventas, clientes],
        )
        assert result.rows == [
            {"ciudad": "Córdoba", "vendedor": "Ana"},
            {"ciudad": "Rosario", "vendedor": "Beto"},
        ]

    def test_row_limit_reports_truncation_without_editing_the_query(
        self, ventas: QueryTable
    ) -> None:
        result = run_sql("SELECT * FROM ventas", [ventas], limit=2)
        assert len(result.rows) == 2
        assert result.truncated is True

    def test_preview(self, ventas: QueryTable) -> None:
        result = preview(ventas, limit=2)
        assert len(result.rows) == 2
        assert "ciudad" in result.columns

    def test_a_confirmed_rename_is_applied_as_a_projection(self, store: LocalFileStore) -> None:
        # Confirming a schema must not rewrite the Parquet: the rename is a
        # view projection, so it stays cheap and reversible.
        outcome = run_pipeline("v.csv", b"ciudad,monto\nCordoba,100\n")
        key = dataset_parquet_key("u_ana", "ds_p")
        store.put(key, to_parquet_bytes(outcome.tables[0].typed.frame))
        table = QueryTable(
            name="ventas",
            uri=store.uri_for(key),
            projection={"localidad": "ciudad", "importe": "monto"},
        )
        result = run_sql("SELECT localidad, importe FROM ventas", [table])
        assert result.rows == [{"localidad": "Cordoba", "importe": 100}]


class TestGuard:
    """Everything here must be rejected before it reaches DuckDB."""

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE ventas",
            "DELETE FROM ventas",
            "UPDATE ventas SET monto = 0",
            "INSERT INTO ventas VALUES ('x', 1, NULL)",
            "CREATE TABLE evil AS SELECT 1",
            "ALTER TABLE ventas RENAME TO otra",
            "COPY ventas TO 'salida.csv'",
            "ATTACH 'otra.db' AS otra",
            "INSTALL httpfs",
            "SET s3_access_key_id = 'x'",
            "PRAGMA database_list",
        ],
    )
    def test_writes_and_commands_are_blocked(self, sql: str) -> None:
        with pytest.raises(QueryError) as exc:
            validate_sql(sql, {"ventas"})
        assert exc.value.code in {"write_blocked", "sql_parse_error", "multiple_statements"}

    @pytest.mark.parametrize("op", ["UNION", "UNION ALL", "INTERSECT", "EXCEPT"])
    def test_set_operations_are_reads(self, op: str) -> None:
        """INTERSECT/EXCEPT used to be rejected as writes — the fine-tune emits them."""
        validate_sql(f"SELECT ciudad FROM ventas {op} SELECT ciudad FROM ventas", {"ventas"})

    @pytest.mark.parametrize("op", ["INTERSECT", "EXCEPT", "UNION"])
    def test_a_write_inside_a_set_operation_is_still_blocked(self, op: str) -> None:
        with pytest.raises(QueryError):
            validate_sql(f"SELECT 1 {op} SELECT * FROM read_parquet('s3://otro/tenant.parquet')", {"ventas"})

    def test_a_stacked_statement_is_blocked(self) -> None:
        with pytest.raises(QueryError) as exc:
            validate_sql("SELECT * FROM ventas; DROP TABLE ventas", {"ventas"})
        assert exc.value.code in {"multiple_statements", "write_blocked"}

    def test_reading_an_arbitrary_file_is_blocked(self) -> None:
        # The shortest path from a generated query to another tenant's data.
        with pytest.raises(QueryError) as exc:
            validate_sql("SELECT * FROM read_parquet('s3://otro/tenant.parquet')", {"ventas"})
        assert exc.value.code in {"forbidden_function", "unknown_table"}

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            "SELECT read_text('/etc/passwd')",
            "SELECT * FROM glob('/**')",
            "SELECT * FROM postgres_scan('host=x', 'public', 'users')",
        ],
    )
    def test_every_file_reaching_function_is_blocked(self, sql: str) -> None:
        with pytest.raises(QueryError):
            validate_sql(sql, {"ventas"})

    def test_an_unregistered_table_is_blocked(self) -> None:
        # The load-bearing rule: correctness doesn't depend on having listed
        # every dangerous function, only on knowing this request's tables.
        with pytest.raises(QueryError) as exc:
            validate_sql("SELECT * FROM ventas_de_otro_usuario", {"ventas"})
        assert exc.value.code == "unknown_table"

    def test_a_join_onto_an_unregistered_table_is_blocked(self) -> None:
        with pytest.raises(QueryError):
            validate_sql("SELECT * FROM ventas JOIN secretos ON true", {"ventas"})

    def test_a_subquery_cannot_smuggle_a_table_in(self) -> None:
        with pytest.raises(QueryError):
            validate_sql(
                "SELECT * FROM ventas WHERE monto > (SELECT max(x) FROM otra)", {"ventas"}
            )

    def test_empty_sql(self) -> None:
        with pytest.raises(QueryError) as exc:
            validate_sql("   ", {"ventas"})
        assert exc.value.code == "empty_sql"

    def test_the_guard_runs_before_execution(self, ventas: QueryTable) -> None:
        with pytest.raises(QueryError):
            run_sql("DROP TABLE ventas", [ventas])


class TestGuardAllowsRealQueries:
    """A guard that blocks legitimate analysis is a broken guard."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM ventas",
            "SELECT ciudad, SUM(monto) FROM ventas GROUP BY ciudad HAVING SUM(monto) > 10",
            "WITH top AS (SELECT * FROM ventas ORDER BY monto DESC LIMIT 5) SELECT * FROM top",
            "SELECT ciudad, monto, RANK() OVER (ORDER BY monto DESC) FROM ventas",
            "SELECT * FROM ventas WHERE fecha BETWEEN DATE '2024-01-01' AND DATE '2024-06-30'",
            "SELECT date_trunc('month', fecha) AS mes, AVG(monto) FROM ventas GROUP BY 1",
            "SELECT * FROM ventas UNION ALL SELECT * FROM ventas",
            "SELECT COUNT(*) FROM ventas",
        ],
    )
    def test_analytical_sql_passes(self, sql: str) -> None:
        validate_sql(sql, {"ventas"})

    def test_a_cte_is_a_legitimate_table_reference(self) -> None:
        validate_sql(
            "WITH resumen AS (SELECT ciudad FROM ventas) SELECT * FROM resumen", {"ventas"}
        )

    def test_a_real_cte_query_executes(self, ventas: QueryTable) -> None:
        result = run_sql(
            "WITH grandes AS (SELECT * FROM ventas WHERE monto > 1000) "
            "SELECT ciudad FROM grandes ORDER BY ciudad",
            [ventas],
        )
        assert [r["ciudad"] for r in result.rows] == ["Córdoba", "Mendoza"]


class TestTableNaming:
    def test_a_bad_identifier_is_refused(self, store: LocalFileStore) -> None:
        # This string is interpolated into DDL, so it is checked even though it
        # is generated rather than user-supplied.
        with pytest.raises(QueryError):
            table_for_dataset("u_ana", "ds_1", 'ventas"; DROP TABLE x; --', store=store)

    def test_a_generated_name_is_accepted(self, store: LocalFileStore) -> None:
        table = table_for_dataset("u_ana", "ds_1", "ventas_2024", store=store)
        assert table.name == "ventas_2024"
        assert table.uri.endswith("data.parquet")
