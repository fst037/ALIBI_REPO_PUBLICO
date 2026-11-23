"""Opening a conversation. The source is always a folder.

`dataset` (one file) and `datasets` (an explicit multi-selection) were removed:
the scope a question is answered against is now exactly one folder the user
chose. Module A never sees a single isolated table, and never sees everything the
user owns.

The folder is the right unit because it is the only one *resolved on every read*.
An explicit dataset list is frozen when the conversation opens, so a file
uploaded into the working set afterwards stayed invisible to a conversation
already in progress — the opposite of what a user expects after dropping a new
export into the folder they are asking about. `test_a_file_added_later_joins_an_open_conversation`
is the test that pins the difference.

Fixture mode (no database), which is the configuration local dev runs in. The DB
path stores the source differently — see `db.models.ConversationDataset` — and is
covered by `test_db_models`.
"""

from fastapi.testclient import TestClient


def _folder(client: TestClient, headers: dict, name: str, dataset_ids: list[str]) -> dict:
    response = client.post(
        "/api/v1/folders", json={"name": name, "datasetIds": dataset_ids}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _open(client: TestClient, headers: dict, **body) -> dict:
    response = client.post("/api/v1/conversations", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestFolderSource:
    def test_a_folder_expands_to_its_datasets(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        folder = _folder(client, auth_headers, "Q1", ["sales", "budget"])
        conversation = _open(client, auth_headers, folderId=folder["id"])
        assert conversation["source"]["type"] == "folder"
        assert conversation["source"]["name"] == "Q1"
        assert sorted(conversation["datasetIds"]) == ["budget", "sales"]

    def test_a_folder_of_one_file_is_still_a_folder(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """Asking about one file is a folder containing one file, not a special case."""
        folder = _folder(client, auth_headers, "Just sales", ["sales"])
        conversation = _open(client, auth_headers, folderId=folder["id"])
        assert conversation["source"]["type"] == "folder"
        assert conversation["datasetIds"] == ["sales"]

    def test_the_selection_survives_a_reread(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # Creating and reading go through different code paths; the tables have
        # to be the same through both or the prompt changes between turns.
        folder = _folder(client, auth_headers, "Q2", ["sales", "budget"])
        created = _open(client, auth_headers, folderId=folder["id"])
        fetched = client.get(
            f"/api/v1/conversations/{created['id']}", headers=auth_headers
        ).json()
        assert sorted(fetched["datasetIds"]) == ["budget", "sales"]

    def test_a_file_added_later_joins_an_open_conversation(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """The reason the source is a folder and not a frozen list of ids."""
        folder = _folder(client, auth_headers, "Growing", ["sales"])
        conversation = _open(client, auth_headers, folderId=folder["id"])
        assert conversation["datasetIds"] == ["sales"]

        attached = client.post(
            f"/api/v1/folders/{folder['id']}/datasets",
            json={"datasetIds": ["budget"]},
            headers=auth_headers,
        )
        assert attached.status_code == 200, attached.text

        reread = client.get(
            f"/api/v1/conversations/{conversation['id']}", headers=auth_headers
        ).json()
        assert sorted(reread["datasetIds"]) == ["budget", "sales"]


class TestRefusedSources:
    def test_an_unknown_folder_is_a_404(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/conversations", json={"folderId": "fld_nope"}, headers=auth_headers
        )
        assert response.status_code == 404

    def test_no_source_at_all_is_rejected(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post("/api/v1/conversations", json={}, headers=auth_headers)
        assert response.status_code == 422

    def test_naming_datasets_directly_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """Not silently reinterpreted as a folder.

        An older client sending `datasetIds` is asking for a scope the product no
        longer has. Guessing one for it would answer questions against a set the
        caller never chose, which is the exact class of silent-wrong-scope bug
        this design removes.
        """
        response = client.post(
            "/api/v1/conversations",
            json={"datasetIds": ["sales", "budget"]},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_the_legacy_source_id_alias_still_opens_a_folder(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """`sourceType`/`sourceId` are kept as aliases so an older client works."""
        folder = _folder(client, auth_headers, "Legacy", ["sales"])
        conversation = _open(
            client, auth_headers, sourceType="folder", sourceId=folder["id"]
        )
        assert conversation["source"]["id"] == folder["id"]
