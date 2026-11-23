"""Folders: one level, and a mapping rather than a hierarchy.

Two product rules carry all the weight here and each has a test that would fail
loudly if the implementation drifted into the obvious-but-wrong version:

* attaching a dataset to a second folder **links** it, it does not move or copy
  it — so a dataset can be in two folders at once and there is still only one
  of it;
* deleting a folder deletes the folder, never the datasets inside it.
"""

from fastapi.testclient import TestClient

from tests.test_uploads import CSV, _upload


def _folder(client: TestClient, headers: dict, name: str, dataset_ids=None) -> dict:
    response = client.post(
        "/api/v1/folders",
        json={"name": name, "datasetIds": dataset_ids or []},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCrud:
    def test_create_and_list(self, client: TestClient, auth_headers: dict) -> None:
        _folder(client, auth_headers, "Ventas 2024")
        folders = client.get("/api/v1/folders", headers=auth_headers).json()
        assert [f["name"] for f in folders] == ["Ventas 2024"]
        assert folders[0]["datasetCount"] == 0

    def test_a_duplicate_name_is_a_conflict(self, client: TestClient, auth_headers: dict) -> None:
        _folder(client, auth_headers, "Ventas")
        response = client.post(
            "/api/v1/folders", json={"name": "ventas"}, headers=auth_headers
        )
        # Case-insensitive: two folders differing only in capitalisation are
        # indistinguishable in a sidebar.
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "folder_name_taken"

    def test_rename(self, client: TestClient, auth_headers: dict) -> None:
        folder = _folder(client, auth_headers, "Borrador")
        response = client.patch(
            f"/api/v1/folders/{folder['id']}", json={"name": "Final"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Final"

    def test_renaming_a_folder_to_its_own_name_is_allowed(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        folder = _folder(client, auth_headers, "Ventas")
        response = client.patch(
            f"/api/v1/folders/{folder['id']}", json={"name": "Ventas"}, headers=auth_headers
        )
        assert response.status_code == 200

    def test_a_missing_folder_is_a_404(self, client: TestClient, auth_headers: dict) -> None:
        assert client.get("/api/v1/folders/nope", headers=auth_headers).status_code == 404
        assert client.delete("/api/v1/folders/nope", headers=auth_headers).status_code == 404

    def test_folders_require_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/folders").status_code == 401

    def test_no_subfolders_exist_to_create(self, client: TestClient, auth_headers: dict) -> None:
        # `parentId` is not a field, so it is ignored rather than nesting
        # anything — "one level" is a property of the schema, not a rule.
        response = client.post(
            "/api/v1/folders", json={"name": "Hija", "parentId": "otra"}, headers=auth_headers
        )
        assert response.status_code == 201
        assert "parentId" not in response.json()


class TestMapping:
    def test_a_dataset_can_live_in_two_folders_at_once(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        first = _folder(client, auth_headers, "Comercial", [dataset_id])
        second = _folder(client, auth_headers, "Dirección")
        before = len(client.get("/api/v1/datasets", headers=auth_headers).json())

        response = client.post(
            f"/api/v1/folders/{second['id']}/datasets",
            json={"datasetIds": [dataset_id]},
            headers=auth_headers,
        )
        assert response.status_code == 200

        dataset = client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers).json()
        assert sorted(dataset["folderIds"]) == sorted([first["id"], second["id"]])
        # Still one dataset in the Biblioteca: attaching links, never copies.
        assert len(client.get("/api/v1/datasets", headers=auth_headers).json()) == before

    def test_attaching_twice_is_idempotent(self, client: TestClient, auth_headers: dict) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id])

        response = client.post(
            f"/api/v1/folders/{folder['id']}/datasets",
            json={"datasetIds": [dataset_id]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["datasetCount"] == 1

    def test_detaching_removes_the_link_not_the_dataset(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id])

        response = client.delete(
            f"/api/v1/folders/{folder['id']}/datasets/{dataset_id}", headers=auth_headers
        )
        assert response.status_code == 204
        assert client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers).status_code == 200
        assert (
            client.get(f"/api/v1/folders/{folder['id']}", headers=auth_headers).json()[
                "datasetCount"
            ]
            == 0
        )

    def test_deleting_a_folder_keeps_its_datasets(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id])

        assert client.delete(f"/api/v1/folders/{folder['id']}", headers=auth_headers).status_code == 204
        dataset = client.get(f"/api/v1/datasets/{dataset_id}", headers=auth_headers)
        assert dataset.status_code == 200
        # The stale link must not survive the folder that owned it.
        assert dataset.json()["folderIds"] == []

    def test_deleting_a_dataset_removes_it_from_its_folders(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id])

        client.delete(f"/api/v1/datasets/{dataset_id}", headers=auth_headers)
        detail = client.get(f"/api/v1/folders/{folder['id']}", headers=auth_headers).json()
        assert detail["datasets"] == []

    def test_an_upload_into_a_folder_lands_there(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        folder = _folder(client, auth_headers, "Comercial")
        upload = _upload(client, auth_headers, "ventas.csv", CSV, folder_id=folder["id"])
        assert upload["datasets"][0]["folderIds"] == [folder["id"]]

    def test_listing_datasets_can_be_scoped_to_a_folder(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id])

        scoped = client.get(
            f"/api/v1/datasets?folderId={folder['id']}", headers=auth_headers
        ).json()
        # This is what makes a folder selectable as a conversation source.
        assert [d["id"] for d in scoped] == [dataset_id]

    def test_attaching_a_dataset_that_is_not_yours_is_silently_skipped(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        folder = _folder(client, auth_headers, "Comercial")
        response = client.post(
            f"/api/v1/folders/{folder['id']}/datasets",
            json={"datasetIds": ["no-existe"]},
            headers=auth_headers,
        )
        # A bulk attach with one stale id shouldn't lose the other nine.
        assert response.status_code == 200
        assert response.json()["datasetCount"] == 0


class TestCountReporting:
    """`datasetCount` is computed, never assumed from the request.

    `create_folder` used to answer with `len(datasetIds)`, which is wrong the
    moment an id in the list isn't the caller's — `attach` skips those, so the
    folder is created with fewer datasets than the response claimed. The
    fixture backend recomputed and the Postgres one didn't, which is exactly
    the kind of divergence that only shows up in production.
    """

    def test_creating_with_an_unknown_id_reports_the_real_count(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id, "no-existe"])
        assert folder["datasetCount"] == 1

    def test_creating_with_a_repeated_id_counts_it_once(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        dataset_id = upload["datasets"][0]["id"]
        folder = _folder(client, auth_headers, "Comercial", [dataset_id, dataset_id])
        assert folder["datasetCount"] == 1

    def test_renaming_preserves_the_count(self, client: TestClient, auth_headers: dict) -> None:
        upload = _upload(client, auth_headers, "ventas.csv", CSV)
        folder = _folder(client, auth_headers, "Borrador", [upload["datasets"][0]["id"]])
        renamed = client.patch(
            f"/api/v1/folders/{folder['id']}", json={"name": "Final"}, headers=auth_headers
        ).json()
        assert renamed["datasetCount"] == 1


class TestUploadingIntoAFolder:
    def test_an_unknown_folder_is_refused_before_any_transfer(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # `uploads.folder_id` is a real foreign key, so without this check an id
        # that isn't the caller's would surface as a 500 at insert time.
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "ventas.csv", "sizeBytes": len(CSV), "folderId": "fld_nope"},
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "folder_not_found"

    def test_no_folder_is_still_fine(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "ventas.csv", "sizeBytes": len(CSV)},
            headers=auth_headers,
        )
        assert response.status_code == 201
