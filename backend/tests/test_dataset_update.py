"""Updating a dataset with a new file — append and replace, end to end and for real.

A real CSV goes through the real upload pipeline into real Parquet and every
assertion reads it back through DuckDB, as in `test_etlite.py`. What matters
here is that the dataset keeps its id and column names (charts and boards read
it by those) and that a file of another structure is refused without a trace.
"""

from fastapi.testclient import TestClient

from tests.test_uploads import _upload

JAN = (
    "ciudad;monto;cantidad\n"
    "Córdoba;100,50;3\n"
    "Rosario;200,00;5\n"
).encode()
FEB = (
    "ciudad;monto;cantidad\n"
    "Mendoza;300,25;2\n"
    "Córdoba;110,00;4\n"
).encode()
# Same headers, other order and casing — still the same structure.
FEB_REORDERED = (
    "Cantidad;Ciudad;Monto\n"
    "2;Mendoza;300,25\n"
).encode()
MISSING = b"ciudad;monto\nSalta;1,00\n"
EXTRA = b"ciudad;monto;cantidad;region\nSalta;1,00;1;NOA\n"
RETYPED = b"ciudad;monto;cantidad\nSalta;mucho;1\n"


def _staged(client: TestClient, headers: dict, name: str, data: bytes) -> str:
    return _upload(client, headers, name, data)["datasets"][0]["id"]


def _confirmed(client: TestClient, headers: dict, name: str, data: bytes, cleaning=None) -> str:
    dataset_id = _staged(client, headers, name, data)
    columns = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=headers).json()["columns"]
    body: dict = {"columns": columns}
    if cleaning is not None:
        body.update(cleaning)
    assert client.put(f"/api/v1/datasets/{dataset_id}/schema", json=body, headers=headers).status_code == 200
    return dataset_id


def _rows(client: TestClient, headers: dict, dataset_id: str) -> list[dict]:
    return client.get(f"/api/v1/datasets/{dataset_id}/preview?limit=200", headers=headers).json()["rows"]


def _update(client, headers, target, staged, mode, path="update", **extra):
    return client.post(
        f"/api/v1/datasets/{target}/{path}",
        json={"stagedDatasetId": staged, "mode": mode, **extra},
        headers=headers,
    )


def _ids(client: TestClient, headers: dict) -> list[str]:
    return [d["id"] for d in client.get("/api/v1/datasets", headers=headers).json()]


class TestAppend:
    def test_the_new_rows_follow_the_old_and_the_dataset_keeps_its_id(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "febrero.csv", FEB)

        response = _update(client, auth_headers, target, staged, "append")

        assert response.status_code == 200, response.text
        assert response.json()["id"] == target
        assert response.json()["rows"] == 4
        assert [r["ciudad"] for r in _rows(client, auth_headers, target)] == [
            "Córdoba", "Rosario", "Mendoza", "Córdoba",
        ]

    def test_the_staged_dataset_is_gone_afterwards(self, client: TestClient, auth_headers: dict) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "febrero.csv", FEB)
        _update(client, auth_headers, target, staged, "append")

        ids = _ids(client, auth_headers)
        assert target in ids
        assert staged not in ids

    def test_header_order_and_casing_do_not_matter(self, client: TestClient, auth_headers: dict) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "feb.csv", FEB_REORDERED)

        assert _update(client, auth_headers, target, staged, "append").status_code == 200
        last = _rows(client, auth_headers, target)[-1]
        assert last["ciudad"] == "Mendoza"
        assert last["cantidad"] == 2

    def test_rows_already_stored_are_counted_and_can_be_skipped(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "otra-vez.csv", JAN)

        preview = _update(client, auth_headers, target, staged, "append", path="update-preview").json()
        assert preview["compatible"] is True
        assert (preview["existingRows"], preview["incomingRows"], preview["overlapRows"]) == (2, 2, 2)
        assert preview["resultRows"] == 4

        skipped = _update(
            client, auth_headers, target, staged, "append", path="update-preview", onOverlap="skip"
        ).json()
        assert skipped["resultRows"] == 2

        applied = _update(client, auth_headers, target, staged, "append", onOverlap="skip")
        assert applied.json()["rows"] == 2


class TestReplace:
    def test_the_file_becomes_the_dataset(self, client: TestClient, auth_headers: dict) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "corregido.csv", FEB)

        response = _update(client, auth_headers, target, staged, "replace")

        assert response.status_code == 200
        assert response.json()["id"] == target
        assert [r["ciudad"] for r in _rows(client, auth_headers, target)] == ["Mendoza", "Córdoba"]

    def test_a_renamed_column_keeps_answering_to_its_new_name(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id = _staged(client, auth_headers, "enero.csv", JAN)
        columns = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()["columns"]
        columns[0]["name"] = "Sucursal"
        client.put(f"/api/v1/datasets/{dataset_id}/schema", json={"columns": columns}, headers=auth_headers)
        staged = _staged(client, auth_headers, "feb.csv", FEB)

        assert _update(client, auth_headers, dataset_id, staged, "replace").status_code == 200
        rows = _rows(client, auth_headers, dataset_id)
        assert [r["sucursal"] for r in rows] == ["Mendoza", "Córdoba"]


class TestStructure:
    def _refused(self, client, headers, data: bytes, expected_kind: str) -> None:
        target = _confirmed(client, headers, "enero.csv", JAN)
        staged = _staged(client, headers, "mal.csv", data)

        preview = _update(client, headers, target, staged, "append", path="update-preview").json()
        assert preview["compatible"] is False
        assert expected_kind in [d["kind"] for d in preview["differences"]]

        response = _update(client, headers, target, staged, "append")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "structure_mismatch"
        # Nothing was written, and the staged table is still there to discard.
        assert len(_rows(client, headers, target)) == 2
        assert staged in _ids(client, headers)

    def test_a_missing_column(self, client: TestClient, auth_headers: dict) -> None:
        self._refused(client, auth_headers, MISSING, "missing")

    def test_an_extra_column(self, client: TestClient, auth_headers: dict) -> None:
        self._refused(client, auth_headers, EXTRA, "extra")

    def test_a_column_that_changed_kind(self, client: TestClient, auth_headers: dict) -> None:
        self._refused(client, auth_headers, RETYPED, "type")

    def test_targets_list_fitting_datasets_first(self, client: TestClient, auth_headers: dict) -> None:
        fits = _confirmed(client, auth_headers, "enero.csv", JAN)
        other = _confirmed(client, auth_headers, "otro.csv", b"a;b\n1;2\n")
        staged = _staged(client, auth_headers, "feb.csv", FEB)

        targets = client.get(f"/api/v1/datasets/{staged}/update-targets", headers=auth_headers).json()

        by_id = {t["datasetId"]: t for t in targets}
        assert by_id[fits]["compatible"] is True
        assert by_id[other]["compatible"] is False
        assert targets[0]["datasetId"] == fits

    def test_an_unconfirmed_dataset_cannot_be_updated(self, client: TestClient, auth_headers: dict) -> None:
        unconfirmed = _staged(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "feb.csv", FEB)
        assert _update(client, auth_headers, unconfirmed, staged, "append").status_code == 409

    def test_a_dataset_cannot_update_itself(self, client: TestClient, auth_headers: dict) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        assert _update(client, auth_headers, target, target, "replace").status_code == 422


class TestCleaningRecipe:
    DEDUPE = {"table": {"duplicates": "exact"}, "columns": []}

    def test_the_recipe_is_remembered_and_offered_again(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        target = _confirmed(
            client, auth_headers, "enero.csv", JAN, cleaning={"cleaning": {"duplicates": "exact"}}
        )
        staged = _staged(client, auth_headers, "feb.csv", FEB)

        preview = _update(client, auth_headers, target, staged, "append", path="update-preview").json()

        assert preview["storedCleaning"]["table"]["duplicates"] == "exact"

    def test_the_recipe_cleans_the_combined_table_and_replaces_the_stored_one(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        target = _confirmed(client, auth_headers, "enero.csv", JAN)
        staged = _staged(client, auth_headers, "otra-vez.csv", JAN)

        response = _update(client, auth_headers, target, staged, "append", cleaning=self.DEDUPE)

        assert response.status_code == 200
        assert response.json()["rows"] == 2
        again = _staged(client, auth_headers, "tercera.csv", FEB)
        preview = _update(client, auth_headers, target, again, "append", path="update-preview").json()
        assert preview["storedCleaning"]["table"]["duplicates"] == "exact"

    def test_no_recipe_this_time_clears_the_stored_one(self, client: TestClient, auth_headers: dict) -> None:
        target = _confirmed(
            client, auth_headers, "enero.csv", JAN, cleaning={"cleaning": {"duplicates": "exact"}}
        )
        staged = _staged(client, auth_headers, "feb.csv", FEB)
        _update(client, auth_headers, target, staged, "append")

        again = _staged(client, auth_headers, "mar.csv", FEB)
        preview = _update(client, auth_headers, target, again, "append", path="update-preview").json()
        assert preview["storedCleaning"] is None
