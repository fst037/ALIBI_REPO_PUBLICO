"""Reopening a conversation from History or the sidebar has to actually work.

Two defects made it not work, and these tests pin both.

**Every conversation kept the placeholder title.** `create_conversation` writes
"New query" and nothing ever replaced it, so the sidebar's Recent list was a
column of identical rows. A conversation is now named after the first question
asked in it — and only that one, or a History entry would rename itself under
the user as they keep asking.

The name is set at `prepare`, not on the way out of `execute`, so a question
that *fails* still names its thread. Otherwise the conversations left as
"New query" would be exactly the ones the user most wants to find again, and two
of them over the same folder would be indistinguishable in the sidebar.

**History pointed nowhere.** In fixture mode it came from a standalone
`history.json` whose ids matched no conversation; on the database path an inner
join to the datasets table dropped every folder-sourced conversation, which is
the only shape that can be created since the folder-only decision. Both now
derive History from the conversations themselves, so a row always resolves.
"""

from fastapi.testclient import TestClient

from tests.test_chat_execution import _ask_sql, _dataset


def _history_items(client: TestClient, headers: dict) -> list[dict]:
    response = client.get("/api/v1/history", headers=headers)
    assert response.status_code == 200, response.text
    return [item for group in response.json() for item in group["items"]]


def _answered(client: TestClient, headers: dict, conversation_over) -> tuple[str, str, str]:
    """A conversation with one answer on it: (id, folder name, SQL asked)."""
    dataset = _dataset(client, headers)
    conversation_id = conversation_over(headers, [dataset["id"]], "Ventas")
    sql = f"SELECT ciudad FROM {dataset['id']}"
    assert _ask_sql(client, headers, conversation_id, sql).status_code == 201
    return conversation_id, "Ventas", sql


def test_first_question_names_the_conversation(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    dataset = _dataset(client, auth_headers)
    conversation_id = conversation_over(auth_headers, [dataset["id"]])
    opened = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()
    assert opened["title"] == "New query"

    sql = f"SELECT ciudad FROM {dataset['id']}"
    assert _ask_sql(client, auth_headers, conversation_id, sql).status_code == 201

    titled = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()
    assert titled["title"] == sql


def test_later_questions_keep_the_first_title(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    conversation_id, _, first = _answered(client, auth_headers, conversation_over)
    dataset_id = first.split(" FROM ")[1]
    assert _ask_sql(
        client, auth_headers, conversation_id, f"SELECT monto FROM {dataset_id}"
    ).status_code == 201

    titled = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()
    assert titled["title"] == first


def test_history_rows_point_at_a_real_conversation(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    conversation_id, folder_name, sql = _answered(client, auth_headers, conversation_over)

    history = client.get("/api/v1/history", headers=auth_headers)
    assert history.status_code == 200
    items = [item for group in history.json() for item in group["items"]]
    row = next(item for item in items if item["id"] == conversation_id)
    assert row["title"] == sql
    # The folder, not a dataset name: that is what the conversation runs over.
    assert row["source"] == folder_name
    assert row["questionCount"] == 1

    # The id on the row is what the canvas deep-links to, so it has to resolve —
    # carrying the thread, or reopening lands on an empty canvas.
    reopened = client.get(f"/api/v1/conversations/{row['id']}", headers=auth_headers)
    assert reopened.status_code == 200
    assert [m["role"] for m in reopened.json()["messages"]] == ["assistant"]


def test_history_has_no_rows_that_cannot_be_reopened(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    """The `h_1`…`h_7` fixture failure, pinned generally."""
    _answered(client, auth_headers, conversation_over)

    items = [item for group in client.get("/api/v1/history", headers=auth_headers).json() for item in group["items"]]
    assert items
    for item in items:
        assert (
            client.get(f"/api/v1/conversations/{item['id']}", headers=auth_headers).status_code
            == 200
        ), f"History row {item['id']} opens nothing"


def test_a_failed_question_still_names_its_conversation(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    """Two untitled threads over one folder used to be indistinguishable."""
    dataset = _dataset(client, auth_headers)
    conversation_id = conversation_over(auth_headers, [dataset["id"]], "Aves")

    # Refused before any SQL is generated: nothing is ever executed here.
    rejected = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/prepare",
        json={"question": "delete the duplicate rows"},
        headers=auth_headers,
    )
    assert rejected.status_code == 200
    assert "draftId" not in rejected.json()

    titled = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()
    assert titled["title"] == "delete the duplicate rows"


class TestFavorite:
    def test_starring_shows_up_in_history(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        conversation_id, _, _ = _answered(client, auth_headers, conversation_over)

        patched = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"favorite": True},
            headers=auth_headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["favorite"] is True

        items = _history_items(client, auth_headers)
        assert next(i for i in items if i["id"] == conversation_id)["favorite"] is True

        unstarred = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"favorite": False},
            headers=auth_headers,
        )
        assert unstarred.json()["favorite"] is False
        items = _history_items(client, auth_headers)
        assert next(i for i in items if i["id"] == conversation_id)["favorite"] is False

    def test_an_empty_patch_is_refused(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """A 200 with nothing changed is indistinguishable from a star that failed."""
        conversation_id, _, _ = _answered(client, auth_headers, conversation_over)
        response = client.patch(
            f"/api/v1/conversations/{conversation_id}", json={}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_patching_an_unknown_conversation_is_a_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        response = client.patch(
            "/api/v1/conversations/conv_nope", json={"favorite": True}, headers=auth_headers
        )
        assert response.status_code == 404


class TestDelete:
    def test_deleting_removes_it_from_history_and_recents(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        conversation_id, _, _ = _answered(client, auth_headers, conversation_over)

        deleted = client.delete(
            f"/api/v1/conversations/{conversation_id}", headers=auth_headers
        )
        assert deleted.status_code == 204

        assert (
            client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).status_code
            == 404
        )
        assert conversation_id not in {i["id"] for i in _history_items(client, auth_headers)}
        recents = client.get(
            "/api/v1/conversations?recent=true&limit=50", headers=auth_headers
        ).json()
        assert conversation_id not in {c["id"] for c in recents}

    def test_deleting_twice_is_a_404(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """The second click of a double-click must not report success."""
        conversation_id, _, _ = _answered(client, auth_headers, conversation_over)
        assert client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).status_code == 204
        assert client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).status_code == 404

    def test_the_folder_and_its_datasets_survive(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """Deleting a conversation deletes a transcript, not the user's data."""
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]], "Keep me")
        client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)

        assert (
            client.get(f"/api/v1/datasets/{dataset['id']}", headers=auth_headers).status_code == 200
        )
        names = {f["name"] for f in client.get("/api/v1/folders", headers=auth_headers).json()}
        assert "Keep me" in names


def test_recent_conversations_carry_their_titles(
    client: TestClient, auth_headers: dict, conversation_over
) -> None:
    conversation_id, _, sql = _answered(client, auth_headers, conversation_over)

    recents = client.get("/api/v1/conversations?recent=true&limit=6", headers=auth_headers).json()
    assert {c["id"]: c["title"] for c in recents}[conversation_id] == sql
