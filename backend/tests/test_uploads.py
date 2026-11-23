"""The upload surface, end to end and for real.

These are not contract-shape tests. The file really goes into the object store,
the real pipeline parses it, real Parquet comes out, and the preview really
comes back through DuckDB — the whole Module B flow with no AWS, no database
and no model, which is exactly the configuration a developer and the demo run
in.
"""

import pytest
from fastapi.testclient import TestClient

from app.ingest.jobs import get_job_runner
from app.ingest.storage import get_object_store, raw_upload_key
from app.schemas.auth import User

CSV = (
    "ciudad;monto;fecha\n"
    "Córdoba;1.234,56;01/02/2024\n"
    "Rosario;980,00;15/03/2024\n"
    "Mendoza;2.500,10;28/12/2024\n"
).encode()


def _upload(client: TestClient, headers: dict, filename: str, data: bytes, folder_id=None) -> dict:
    """Register, PUT the bytes, wait for the job. Returns the finished upload."""
    body = {"filename": filename, "sizeBytes": len(data)}
    if folder_id:
        body["folderId"] = folder_id
    created = client.post("/api/v1/uploads", json=body, headers=headers)
    assert created.status_code == 201, created.text
    payload = created.json()

    put = client.put(payload["uploadUrl"], content=data, headers=headers)
    assert put.status_code == 204, put.text

    get_job_runner().wait_all(timeout=30)
    return client.get(f"/api/v1/uploads/{payload['upload']['id']}", headers=headers).json()


class TestPresign:
    def test_the_api_never_receives_the_file(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "ventas.csv", "sizeBytes": len(CSV)},
            headers=auth_headers,
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["method"] == "PUT"
        assert payload["uploadUrl"]
        assert payload["upload"]["status"] == "pending"

    def test_an_unsupported_extension_is_refused_before_any_transfer(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "presentacion.pptx", "sizeBytes": 100},
            headers=auth_headers,
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_file_type"

    def test_an_oversized_file_is_refused_before_any_transfer(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "enorme.csv", "sizeBytes": 500 * 1024 * 1024},
            headers=auth_headers,
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"

    def test_uploading_requires_auth(self, client: TestClient) -> None:
        assert client.post("/api/v1/uploads", json={"filename": "a.csv", "sizeBytes": 1}).status_code == 401


class TestLocalReceiver:
    def test_a_key_outside_your_own_prefix_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The local receiver has no signature to rely on, so the prefix check
        # is what stops a valid token writing into another user's namespace.
        response = client.put(
            "/api/v1/uploads/local/uploads/otro-usuario/up_x/ventas.csv",
            content=CSV,
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_an_unauthenticated_put_is_refused(self, client: TestClient) -> None:
        response = client.put("/api/v1/uploads/local/uploads/u1/up_1/a.csv", content=CSV)
        assert response.status_code == 401


class TestProcessing:
    def test_a_csv_becomes_a_queryable_dataset(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)

        assert upload["status"] == "ready"
        assert upload["detectedTables"] == 1
        dataset = upload["datasets"][0]
        # Not `processed`: the table exists but nobody has confirmed the
        # inferred schema, so it must not be queryable yet (Ingesta 1.2).
        assert dataset["status"] == "awaiting_confirmation"
        assert dataset["tableName"] == "ventas"
        assert dataset["rows"] == 3

    def test_the_raw_document_is_deleted_once_processing_succeeds(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        user = _current_user(client, auth_headers)
        key = raw_upload_key(user.id, upload["id"], "ventas.csv")
        # The product rule: we keep the resulting table, never the document.
        assert not get_object_store().exists(key)

    def test_the_inferred_schema_survives_to_the_api(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()

        types = {c["name"]: c["type"] for c in schema["columns"]}
        # The es-AR *input* typing has to hold all the way through Parquet, not
        # just in the unit tests: "1.234,56" is a number and "01/02/2024" is a
        # date. The type names themselves are the English `ColumnType` values.
        assert types == {"ciudad": "Text", "monto": "Number", "fecha": "Date"}
        assert schema["confirmed"] is False

    def test_preview_reads_the_parquet_back(self, client: TestClient, auth_headers: dict) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        preview = client.get(
            f"/api/v1/datasets/{dataset_id}/preview", headers=auth_headers
        ).json()

        assert [c["key"] for c in preview["columns"]] == ["ciudad", "monto", "fecha"]
        assert preview["rows"][0]["ciudad"] == "Córdoba"
        assert preview["rows"][0]["monto"] == 1234.56
        assert preview["rows"][0]["fecha"] == "2024-02-01"
        # "num" is what the frontend's DataTable understands, and matches
        # charts.ResultColumn — the preview must not need its own adapter.
        assert [c["align"] for c in preview["columns"]] == ["left", "num", "left"]

    def test_a_multi_table_workbook_yields_one_dataset_per_table(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "reporte.xlsx", _workbook())

        assert upload["detectedTables"] == 2
        assert len(upload["datasets"]) == 2
        # Distinguishable at a glance in the confirmation screen — otherwise
        # the user is asked to confirm two identical-looking rows.
        labels = {d["sourceLabel"] for d in upload["datasets"]}
        assert len(labels) == 2
        names = {d["tableName"] for d in upload["datasets"]}
        assert len(names) == 2
        assert any("Encontramos 2 tablas" in w for w in upload["warnings"])

    def test_two_uploads_of_the_same_file_get_distinct_table_names(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        first = _upload(client, auth_headers, "ventas.csv", CSV)
        second = _upload(client, auth_headers, "ventas.csv", CSV)
        # Both are named after the file, but a table name is what Module A puts
        # in a FROM clause, so it has to be unambiguous.
        assert first["datasets"][0]["tableName"] == "ventas"
        assert second["datasets"][0]["tableName"] == "ventas_2"

    def test_an_unreadable_file_fails_in_spanish_and_keeps_the_raw_object(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "roto.csv", b"\x00\x01\x02 sin tabla alguna")

        assert upload["status"] == "error"
        assert upload["error"]
        # No stack trace, no library name — Ingesta 1.1 is explicit about this.
        assert "Traceback" not in upload["error"]
        user = _current_user(client, auth_headers)
        # Kept so `retry` has something to retry.
        assert get_object_store().exists(raw_upload_key(user.id, upload["id"], "roto.csv"))

    def test_retrying_a_failed_upload_reprocesses_it(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "roto.csv", b"\x00\x01\x02 sin tabla alguna")
        assert upload["status"] == "error"

        response = client.post(f"/api/v1/uploads/{upload['id']}/retry", headers=auth_headers)
        assert response.status_code == 202
        get_job_runner().wait_all(timeout=30)
        # Same bytes, so it fails the same way — but it genuinely ran again.
        again = client.get(f"/api/v1/uploads/{upload['id']}", headers=auth_headers).json()
        assert again["status"] == "error"

    def test_retrying_a_successful_upload_is_a_conflict(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        response = client.post(f"/api/v1/uploads/{upload['id']}/retry", headers=auth_headers)
        assert response.status_code == 409

    def test_cancelling_discards_the_unconfirmed_datasets(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]

        assert client.delete(f"/api/v1/uploads/{upload['id']}", headers=auth_headers).status_code == 204
        assert client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers).status_code == 404

    def test_cancelling_keeps_datasets_the_user_already_confirmed(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        client.put(
            f"/api/v1/datasets/{dataset_id}/schema",
            json={"columns": schema["columns"]},
            headers=auth_headers,
        )

        client.delete(f"/api/v1/uploads/{upload['id']}", headers=auth_headers)
        # A confirmed dataset belongs to the user, not to the upload it came
        # from — cancelling the upload must not take it away.
        assert client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers).status_code == 200


class TestConfirmation:
    def test_confirming_makes_the_dataset_processed(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/schema",
            json={"columns": schema["columns"], "name": "Ventas 2024"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert response.json()["name"] == "Ventas 2024"

    def test_a_rename_becomes_a_projection_not_a_rewrite(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        columns = schema["columns"]
        columns[0]["name"] = "localidad"

        client.put(
            f"/api/v1/datasets/{dataset_id}/schema",
            json={"columns": columns},
            headers=auth_headers,
        )
        preview = client.get(
            f"/api/v1/datasets/{dataset_id}/preview", headers=auth_headers
        ).json()
        assert [c["key"] for c in preview["columns"]][0] == "localidad"
        assert preview["rows"][0]["localidad"] == "Córdoba"

    def test_renaming_twice_still_points_at_the_right_column(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The regression this guards: if the projection were computed from the
        # *previous* name rather than the stored one, a second rename would
        # look for a column that no longer exists under that name.
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]

        for new_name in ("localidad", "plaza"):
            schema = client.get(
                f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers
            ).json()
            columns = schema["columns"]
            columns[0]["name"] = new_name
            client.put(
                f"/api/v1/datasets/{dataset_id}/schema",
                json={"columns": columns},
                headers=auth_headers,
            )

        preview = client.get(
            f"/api/v1/datasets/{dataset_id}/preview", headers=auth_headers
        ).json()
        assert preview["rows"][0]["plaza"] == "Córdoba"

    def test_an_excluded_column_disappears_from_the_table(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=auth_headers).json()
        columns = schema["columns"]
        columns[2]["included"] = False

        client.put(
            f"/api/v1/datasets/{dataset_id}/schema",
            json={"columns": columns},
            headers=auth_headers,
        )
        preview = client.get(
            f"/api/v1/datasets/{dataset_id}/preview", headers=auth_headers
        ).json()
        # Unchecking a column has to mean a generated query cannot reach it,
        # not merely that the UI hides it.
        assert [c["key"] for c in preview["columns"]] == ["ciudad", "monto"]


class TestPreviewErrors:
    def test_previewing_a_dataset_with_no_table_is_a_conflict(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The seeded fixtures predate the data plane and have no Parquet, which
        # is the same situation as a dataset still being processed. The id has to
        # be one that exists in `app/fixtures/datasets.json`, or this asserts a
        # 409 against a plain 404 and stops testing the conflict path at all.
        response = client.get("/api/v1/datasets/sales/preview", headers=auth_headers)
        assert response.status_code == 409

    def test_previewing_a_missing_dataset_is_a_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        assert client.get("/api/v1/datasets/nope/preview", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_user(client: TestClient, headers: dict) -> User:
    token = headers["Authorization"].split(" ", 1)[1]
    from app.services import auth as auth_store

    user = auth_store.get_user_by_token(token)
    assert user is not None
    return user


def _workbook() -> bytes:
    """A two-sheet workbook, written with the same library that reads it."""
    import io

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    ventas = book.active
    ventas.title = "Ventas"
    for row in [["ciudad", "monto"], ["Córdoba", 100], ["Rosario", 200]]:
        ventas.append(row)
    costos = book.create_sheet("Costos")
    for row in [["rubro", "importe"], ["Flete", 50], ["Insumos", 75]]:
        costos.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class TestDuplicateUploads:
    """Re-uploading a file, or one whose name collides, must not hang or collide.

    The reported symptom was the ingest screen spinning forever. Two independent
    defects fed it, both covered here.
    """

    def test_the_same_file_twice_gets_distinct_table_names(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        first = _upload(client, auth_headers, "repetido.csv", CSV)
        second = _upload(client, auth_headers, "repetido.csv", CSV)
        assert first["status"] == "ready" and second["status"] == "ready"
        names = [first["datasets"][0]["tableName"], second["datasets"][0]["tableName"]]
        assert names == ["repetido", "repetido_2"]

    def test_the_second_upload_warns_about_the_duplicate_name(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The warning lands in the confirmation screen, where the name field is
        # editable — actionable, unlike a refusal that would also make it
        # impossible to re-upload a file to refresh its data.
        _upload(client, auth_headers, "repetido.csv", CSV)
        second = _upload(client, auth_headers, "repetido.csv", CSV)
        assert any("Ya tenés un dataset" in w for w in second["warnings"])

    def test_a_first_upload_warns_about_nothing(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        only = _upload(client, auth_headers, "unico.csv", CSV)
        assert not any("Ya tenés un dataset" in w for w in only.get("warnings") or [])

    def test_a_commit_failure_is_recorded_instead_of_hanging(
        self, client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect that caused the infinite spinner.

        `commit` was the one step in the pipeline with no error boundary, so a
        failure there left the row in `processing` and nothing ever moved it
        again — the client polled a status that could not change.
        """
        from app.services import uploads as uploads_store

        def boom(*args, **kwargs):
            raise RuntimeError("database went away")

        # `commit_stage2` imports the module inside the function, so patching the
        # module attribute is enough — no need to reach into the caller.
        monkeypatch.setattr(uploads_store, "commit", boom)

        upload = _upload(client, auth_headers, "falla.csv", CSV)
        assert upload["status"] == "error", upload
        assert upload["error"]


class TestDuplicateNamesAreRefused:
    """A name another dataset already uses cannot be confirmed.

    Enforced at confirmation, not at upload registration: the name is derived
    from the filename, so refusing earlier would leave the user nowhere to fix
    it short of renaming the file on disk. The confirmation screen is the one
    place with an editable name field.
    """

    def _upload_and_schema(self, client: TestClient, headers: dict, filename: str) -> tuple[str, list]:
        upload = _upload(client, headers, filename, CSV)
        dataset_id = upload["datasets"][0]["id"]
        schema = client.get(f"/api/v1/datasets/{dataset_id}/schema", headers=headers).json()
        return dataset_id, schema["columns"]

    def _confirm(self, client: TestClient, headers: dict, dataset_id: str, columns, name=None):
        body = {"columns": columns}
        if name is not None:
            body["name"] = name
        return client.put(f"/api/v1/datasets/{dataset_id}/schema", json=body, headers=headers)

    def test_confirming_a_taken_name_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        first_id, first_cols = self._upload_and_schema(client, auth_headers, "informe.csv")
        assert self._confirm(client, auth_headers, first_id, first_cols).status_code == 200

        second_id, second_cols = self._upload_and_schema(client, auth_headers, "informe.csv")
        response = self._confirm(client, auth_headers, second_id, second_cols)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "dataset_name_taken"
        assert "informe" in response.json()["error"]["message"]

    def test_renaming_it_unblocks_the_confirmation(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        first_id, first_cols = self._upload_and_schema(client, auth_headers, "informe.csv")
        self._confirm(client, auth_headers, first_id, first_cols)

        second_id, second_cols = self._upload_and_schema(client, auth_headers, "informe.csv")
        ok = self._confirm(client, auth_headers, second_id, second_cols, name="Informe Q2")
        assert ok.status_code == 200
        assert ok.json()["name"] == "Informe Q2"

    def test_the_comparison_ignores_case_accents_and_spacing(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # Two names a person reads as the same must not both be accepted, or
        # "no duplicates" is a rule you break by accident.
        first_id, first_cols = self._upload_and_schema(client, auth_headers, "ventas.csv")
        self._confirm(client, auth_headers, first_id, first_cols, name="Ventas Anuales")

        second_id, second_cols = self._upload_and_schema(client, auth_headers, "otro.csv")
        response = self._confirm(
            client, auth_headers, second_id, second_cols, name="  ventás   anuales "
        )
        assert response.status_code == 409

    def test_confirming_without_renaming_does_not_clash_with_itself(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # The dataset already carries its name before confirmation, so the check
        # has to exclude the row being confirmed.
        dataset_id, columns = self._upload_and_schema(client, auth_headers, "solo.csv")
        assert self._confirm(client, auth_headers, dataset_id, columns).status_code == 200

    def test_reconfirming_the_same_dataset_still_works(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset_id, columns = self._upload_and_schema(client, auth_headers, "recon.csv")
        self._confirm(client, auth_headers, dataset_id, columns)
        again = self._confirm(client, auth_headers, dataset_id, columns, name="recon")
        assert again.status_code == 200
