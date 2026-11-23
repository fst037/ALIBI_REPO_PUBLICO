from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_datasets_requires_auth() -> None:
    # No bearer token → 401 (the datasets endpoints are owner-scoped).
    res = client.get("/api/v1/datasets")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_list_datasets(auth_headers: dict[str, str]) -> None:
    res = client.get("/api/v1/datasets", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert {d["id"] for d in body} >= {"sales", "customers", "budget"}
    # Wire shape must be camelCase for the generated TS client.
    assert "fileType" in body[0]
    assert "updatedAt" in body[0]


def test_get_dataset_found(auth_headers: dict[str, str]) -> None:
    res = client.get("/api/v1/datasets/sales", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["name"] == "sales_2024.csv"


def test_get_dataset_not_found_uses_error_envelope(auth_headers: dict[str, str]) -> None:
    res = client.get("/api/v1/datasets/nope", headers=auth_headers)
    assert res.status_code == 404
    assert res.json() == {"error": {"code": "not_found", "message": "No se encontró el dataset."}}


def test_get_schema(auth_headers: dict[str, str]) -> None:
    res = client.get("/api/v1/datasets/sales/schema", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["datasetId"] == "sales"
    assert body["columns"][0]["name"] == "date"
    assert body["columns"][4]["type"] == "Whole number"


def test_get_schema_not_ready_when_no_schema(auth_headers: dict[str, str]) -> None:
    # `stock` has a dataset row but no schema fixture → 409 schema_not_ready.
    res = client.get("/api/v1/datasets/inventory/schema", headers=auth_headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "schema_not_ready"


def test_rename_dataset(auth_headers: dict[str, str]) -> None:
    res = client.patch(
        "/api/v1/datasets/budget", json={"name": "budget_2024.xlsx"}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["name"] == "budget_2024.xlsx"


def test_set_dataset_favorite(auth_headers: dict[str, str]) -> None:
    res = client.patch("/api/v1/datasets/budget", json={"favorite": True}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["favorite"] is True


def test_update_dataset_empty_422(auth_headers: dict[str, str]) -> None:
    # The patch must carry at least one of name / favorite.
    res = client.patch("/api/v1/datasets/budget", json={}, headers=auth_headers)
    assert res.status_code == 422


def test_confirm_schema_marks_processed(auth_headers: dict[str, str]) -> None:
    schema = client.get("/api/v1/datasets/customers/schema", headers=auth_headers).json()
    res = client.put(
        "/api/v1/datasets/customers/schema",
        json={"columns": schema["columns"]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "processed"
    assert body["columns"] == len(schema["columns"])


def test_delete_dataset(auth_headers: dict[str, str]) -> None:
    assert client.delete("/api/v1/datasets/report", headers=auth_headers).status_code == 204
    assert client.get("/api/v1/datasets/report", headers=auth_headers).status_code == 404
