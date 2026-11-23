"""ETLite — light cleaning chosen at confirmation, end to end and for real.

Same setup as `test_uploads.py`: a real CSV goes through the real pipeline into
real Parquet, and every assertion reads it back through DuckDB. Before
confirmation a recipe is only previewed; on confirmation the cleaned table is
written over the stored one, so every later reader sees clean data on its own.
"""

from fastapi.testclient import TestClient

from app.ingest.storage import dataset_parquet_key, get_object_store
from app.services import etlite
from app.services.query import QueryTable
from app.schemas.datasets import ColumnCleaning, NullStrategy, SchemaColumn, TableCleaning
from tests.test_uploads import _current_user, _upload

# Row 4 duplicates row 1 exactly; row 5 duplicates row 3 by `ciudad` only once
# its spaces are collapsed. Empty cells in every numeric column, and a
# placeholder ("desconocido") ingestion cannot know about.
CSV = (
    "ciudad;monto;cantidad;fecha\n"
    "Córdoba;1.234,56;3;01/02/2024\n"
    "Rosario;;5;15/03/2024\n"
    "Mendoza   Sur;2.500,10;;28/12/2024\n"
    "Córdoba;1.234,56;3;01/02/2024\n"
    "Mendoza Sur;100,00;2;01/01/2024\n"
    "desconocido;50,00;1;02/01/2024\n"
).encode()


def _setup(client: TestClient, headers: dict) -> tuple[str, list[dict]]:
    upload = _upload(client, headers, "limpieza.csv", CSV)
    dataset_id = upload["datasets"][0]["id"]
    schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=headers).json()
    return dataset_id, schema["columns"]


def _preview(client: TestClient, headers: dict, dataset_id: str, columns, cleaning=None, limit=20):
    body = {"columns": columns}
    if cleaning is not None:
        body["cleaning"] = cleaning
    return client.post(
        f"/api/v1/datasets/{dataset_id}/cleaning-preview?limit={limit}", json=body, headers=headers
    )


def _confirm(client: TestClient, headers: dict, dataset_id: str, columns, cleaning=None):
    body = {"columns": columns}
    if cleaning is not None:
        body["cleaning"] = cleaning
    return client.put(f"/api/v1/datasets/{dataset_id}/schema", json=body, headers=headers)


def _rows(client: TestClient, headers: dict, dataset_id: str) -> list[dict]:
    return client.get(f"/api/v1/datasets/{dataset_id}/preview", headers=headers).json()["rows"]


class TestQuality:
    def test_it_counts_what_cleaning_could_fix(self, client: TestClient, auth_headers: dict) -> None:
        dataset_id, _ = _setup(client, auth_headers)
        quality = client.get(f"/api/v1/datasets/{dataset_id}/quality", headers=auth_headers).json()

        assert quality["rowCount"] == 6
        assert quality["duplicateRows"] == 1
        by_ordinal = {c["ordinal"]: c for c in quality["columns"]}
        assert by_ordinal[0]["kind"] == "text"
        assert by_ordinal[0]["whitespaceCount"] == 1
        assert by_ordinal[1] == {"ordinal": 1, "kind": "number", "nullCount": 1, "whitespaceCount": 0}
        assert by_ordinal[2]["kind"] == "integer"
        assert by_ordinal[2]["nullCount"] == 1
        assert by_ordinal[3]["kind"] == "date"

    def test_a_dataset_with_no_table_is_a_conflict(self, client: TestClient, auth_headers: dict) -> None:
        assert client.get("/api/v1/datasets/sales/quality", headers=auth_headers).status_code == 409

    def test_a_missing_dataset_is_a_404(self, client: TestClient, auth_headers: dict) -> None:
        assert client.get("/api/v1/datasets/nope/quality", headers=auth_headers).status_code == 404


class TestFilling:
    def test_the_mean_of_a_whole_number_column_is_rounded(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[2]["cleaning"] = {"nulls": "mean"}

        preview = _preview(client, auth_headers, dataset_id, columns).json()
        # (3 + 5 + 3 + 2 + 1) / 5 = 2.8 → 3, and the column stays whole numbers.
        assert preview["fills"] == [{"ordinal": 2, "value": "3"}]
        assert [r["cantidad"] for r in preview["preview"]["rows"]] == [3, 5, 3, 3, 2, 1]

    def test_zero_median_and_a_typed_value(self, client: TestClient, auth_headers: dict) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[1]["cleaning"] = {"nulls": "median"}
        columns[2]["cleaning"] = {"nulls": "zero"}

        rows = _preview(client, auth_headers, dataset_id, columns).json()["preview"]["rows"]
        assert rows[1]["monto"] == 1234.56
        assert rows[2]["cantidad"] == 0

        columns[1]["cleaning"] = {"nulls": "value", "fillValue": "1.000,5"}
        rows = _preview(client, auth_headers, dataset_id, columns).json()["preview"]["rows"]
        # es-AR typing, like the rest of the product.
        assert rows[1]["monto"] == 1000.5

    def test_a_value_of_the_wrong_type_is_refused(self, client: TestClient, auth_headers: dict) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[2]["cleaning"] = {"nulls": "value", "fillValue": "2,5"}

        response = _preview(client, auth_headers, dataset_id, columns)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cleaning"

        columns[2]["cleaning"] = None
        columns[3]["cleaning"] = {"nulls": "value", "fillValue": "no es una fecha"}
        assert _preview(client, auth_headers, dataset_id, columns).status_code == 422

    def test_an_operation_that_does_not_fit_the_column_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[1]["cleaning"] = {"trim": True}
        assert _preview(client, auth_headers, dataset_id, columns).status_code == 422

        columns[1]["cleaning"] = None
        columns[0]["cleaning"] = {"nulls": "mean"}
        assert _preview(client, auth_headers, dataset_id, columns).status_code == 422


class TestRows:
    def test_dropping_rows_with_an_empty_cell(self, client: TestClient, auth_headers: dict) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[1]["cleaning"] = {"nulls": "drop_row"}

        preview = _preview(client, auth_headers, dataset_id, columns).json()
        assert preview["rowCount"] == 5
        assert preview["removedRows"] == 1
        assert "Rosario" not in [r["ciudad"] for r in preview["preview"]["rows"]]

    def test_a_placeholder_becomes_empty_and_can_drop_its_row(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[0]["cleaning"] = {"nullTokens": ["Desconocido"], "nulls": "drop_row"}

        rows = _preview(client, auth_headers, dataset_id, columns).json()["preview"]["rows"]
        assert [r["ciudad"] for r in rows][-1] == "Mendoza Sur"
        assert len(rows) == 5

    def test_exact_duplicates_keep_the_first_and_the_file_order(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        preview = _preview(
            client, auth_headers, dataset_id, columns, {"duplicates": "exact"}
        ).json()
        assert preview["removedRows"] == 1
        assert [r["ciudad"] for r in preview["preview"]["rows"]] == [
            "Córdoba", "Rosario", "Mendoza   Sur", "Mendoza Sur", "desconocido",
        ]

    def test_duplicates_by_column_see_the_cleaned_values(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[0]["cleaning"] = {"trim": True, "case": "upper"}
        preview = _preview(
            client, auth_headers, dataset_id, columns,
            {"duplicates": "by_columns", "duplicateColumns": [0]},
        ).json()
        # "Mendoza   Sur" and "Mendoza Sur" are one city once the spaces go.
        assert [r["ciudad"] for r in preview["preview"]["rows"]] == [
            "CÓRDOBA", "ROSARIO", "MENDOZA SUR", "DESCONOCIDO",
        ]

    def test_rows_that_differ_only_in_an_empty_cell_collapse_once_it_is_filled(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        csv = "ciudad;cantidad\nRosario;0\nRosario;\nSalta;4\n".encode()
        upload = _upload(client, auth_headers, "relleno.csv", csv)
        dataset_id = upload["datasets"][0]["id"]
        columns = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()["columns"]
        columns[1]["cleaning"] = {"nulls": "zero"}

        for cleaning in ({"duplicates": "exact"}, {"duplicates": "by_columns", "duplicateColumns": [0, 1]}):
            preview = _preview(client, auth_headers, dataset_id, columns, cleaning).json()
            assert preview["removedRows"] == 1
            assert [r["ciudad"] for r in preview["preview"]["rows"]] == ["Rosario", "Salta"]

    def test_an_excluded_column_cannot_identify_duplicates(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[0]["included"] = False
        response = _preview(
            client, auth_headers, dataset_id, columns,
            {"duplicates": "by_columns", "duplicateColumns": [0]},
        )
        assert response.status_code == 422


class TestConfirming:
    def test_the_cleaned_table_replaces_the_stored_one(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        user = _current_user(client, auth_headers)
        key = dataset_parquet_key(user.id, dataset_id)
        before = get_object_store().get(key)

        columns[0]["name"] = "localidad"
        columns[2]["cleaning"] = {"nulls": "zero"}
        response = _confirm(client, auth_headers, dataset_id, columns, {"duplicates": "exact"})
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == 5
        assert get_object_store().get(key) != before

        rows = _rows(client, auth_headers, dataset_id)
        assert len(rows) == 5
        assert rows[2]["cantidad"] == 0
        assert rows[0]["localidad"] == "Córdoba"

    def test_the_file_holds_the_clean_data_without_any_recipe_on_top(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        user = _current_user(client, auth_headers)
        columns[1]["cleaning"] = {"nulls": "drop_row"}
        _confirm(client, auth_headers, dataset_id, columns)

        # Read straight from the object store, past every view and projection.
        uri = get_object_store().uri_for(dataset_parquet_key(user.id, dataset_id))
        table = QueryTable(name="raw", uri=uri)
        assert etlite.count_rows(table) == 5

    def test_nothing_of_the_recipe_is_kept(self, client: TestClient, auth_headers: dict) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        columns[2]["cleaning"] = {"nulls": "mean"}
        _confirm(client, auth_headers, dataset_id, columns, {"duplicates": "exact"})

        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        assert all(c["cleaning"] is None for c in schema["columns"])
        assert "cleaning" not in schema
        # The schema describes the stored table, which is now the cleaned one.
        assert schema["rowCount"] == 5

    def test_example_values_follow_rewritten_values(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # Module A is shown these; after uppercasing it must not be told "Córdoba".
        dataset_id, columns = _setup(client, auth_headers)
        columns[0]["cleaning"] = {"case": "upper", "trim": True}
        _confirm(client, auth_headers, dataset_id, columns)

        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        assert schema["columns"][0]["sampleValues"] == "CÓRDOBA, ROSARIO, MENDOZA SUR"

    def test_an_excluded_column_stays_in_the_file_untouched(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        user = _current_user(client, auth_headers)
        columns[2]["included"] = False
        _confirm(client, auth_headers, dataset_id, columns, {"duplicates": "exact"})

        uri = get_object_store().uri_for(dataset_parquet_key(user.id, dataset_id))
        raw = etlite.physical_types(uri)
        assert list(raw) == ["ciudad", "monto", "cantidad", "fecha"]

    def test_an_invalid_recipe_leaves_everything_as_it_was(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        user = _current_user(client, auth_headers)
        key = dataset_parquet_key(user.id, dataset_id)
        before = get_object_store().get(key)

        columns[1]["cleaning"] = {"case": "lower"}
        assert _confirm(client, auth_headers, dataset_id, columns).status_code == 422

        dataset = client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers).json()
        assert dataset["status"] == "awaiting_confirmation"
        assert get_object_store().get(key) == before

    def test_confirming_without_any_recipe_does_not_touch_the_file(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = _setup(client, auth_headers)
        user = _current_user(client, auth_headers)
        key = dataset_parquet_key(user.id, dataset_id)
        before = get_object_store().get(key)

        response = _confirm(client, auth_headers, dataset_id, columns)
        assert response.status_code == 200
        assert response.json()["rows"] == 6
        assert get_object_store().get(key) == before


class TestPlan:
    """The compiled SQL, without a table — the shape of the view itself."""

    def _columns(self) -> list[SchemaColumn]:
        return [
            SchemaColumn(name=n, type="Text", sampleValues="", included=True, confidence="high")
            for n in ("a", "b")
        ]

    def test_no_recipe_means_no_plan(self) -> None:
        assert etlite.plan_for(self._columns(), ["a", "b"], None) is None
        columns = self._columns()
        columns[0].cleaning = ColumnCleaning()
        assert etlite.plan_for(columns, ["a", "b"], TableCleaning()) is None

    def test_a_fill_is_a_quoted_literal(self) -> None:
        columns = self._columns()
        columns[0].cleaning = ColumnCleaning(nulls=NullStrategy.value, fillValue="O'Brien")
        plan = etlite.plan_for(columns, ["a", "b"], None)
        assert plan is not None
        assert "COALESCE(\"a\", 'O''Brien')" in plan.source_sql("x.parquet")

    def test_the_view_keeps_file_order(self) -> None:
        plan = etlite.plan_for(self._columns(), ["a", "b"], TableCleaning(duplicates="exact"))
        assert plan is not None
        sql = plan.source_sql("x.parquet")
        assert "PARTITION BY \"a\", \"b\"" in sql
        assert sql.endswith('ORDER BY "__alibi_row"')
