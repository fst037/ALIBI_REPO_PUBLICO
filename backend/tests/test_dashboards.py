"""Dashboards: named boards over a folder that recompute when opened.

The property under test throughout is that **a board stores an idea, not a
result**. `test_a_chart_reflects_data_uploaded_after_it_was_added` is the one
that would fail if anyone ever "optimised" this by snapshotting rows at add
time, and it is the reason the feature exists.

The rest cover the edges that make a recomputing board survivable: a query that
has gone stale marks its own card instead of taking the board down, layout is
the user's arrangement rather than a per-device preference, and a chart with no
reproducible query is refused at the door rather than added and left broken.
"""

from fastapi.testclient import TestClient

from tests.test_chat_execution import _ask_sql, _dataset, _stub_models


def _folder(client: TestClient, headers: dict, dataset_ids: list[str], name: str) -> str:
    created = client.post(
        "/api/v1/folders", json={"name": name, "datasetIds": dataset_ids}, headers=headers
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _board(client: TestClient, headers: dict, folder_id: str, name: str = "Sales") -> dict:
    created = client.post(
        "/api/v1/dashboards", json={"name": name, "folderId": folder_id}, headers=headers
    )
    assert created.status_code == 201, created.text
    return created.json()


def _answer_with_chart(
    client: TestClient, headers: dict, conversation_id: str, dataset_id: str, monkeypatch
) -> str:
    """Ask something that produces a chart; return the assistant message id."""
    spec = {
        "title": "Cities",
        "mark": "bar",
        "encoding": {
            "x": {"field": "ciudad", "type": "nominal", "title": "City"},
            "y": {"aggregate": "count", "type": "quantitative", "title": "Entries"},
        },
    }
    _stub_models(monkeypatch, chart={"chart_type": "bar", "spec": spec})
    executed = _ask_sql(
        client, headers, conversation_id, f"SELECT ciudad FROM {dataset_id}"
    )
    assert executed.status_code == 201, executed.text
    return executed.json()["message"]["id"]


class TestBoards:
    def test_a_board_is_created_over_a_folder_and_starts_empty(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")

        board = _board(client, auth_headers, folder_id)

        assert board["folderId"] == folder_id
        assert board["folderName"] == "Q1"
        assert board["chartCount"] == 0
        listed = client.get("/api/v1/dashboards", headers=auth_headers).json()
        assert [b["id"] for b in listed] == [board["id"]]

    def test_a_board_over_a_folder_that_does_not_exist_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        created = client.post(
            "/api/v1/dashboards",
            json={"name": "Nope", "folderId": "fold_missing"},
            headers=auth_headers,
        )
        assert created.status_code == 404

    def test_deleting_a_board_takes_its_charts_with_it(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )

        deleted = client.delete(f"/api/v1/dashboards/{board['id']}", headers=auth_headers)

        assert deleted.status_code == 204
        assert client.get(f"/api/v1/dashboards/{board['id']}", headers=auth_headers).status_code == 404


class TestAddingCharts:
    def test_an_answers_chart_is_copied_onto_the_board_as_sql_plus_spec(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )

        assert added.status_code == 201, added.text
        chart = added.json()
        assert chart["sql"].startswith("SELECT")
        assert chart["spec"]["mark"] == "bar"
        assert chart["title"] == "Cities", "the spec's own title beats the raw question"
        # The idea, and nothing else: no rows, no narrative.
        assert "table" not in chart and "naturalResponse" not in chart

    def test_new_charts_stack_at_the_bottom_rather_than_being_placed_cleverly(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        board = _board(client, auth_headers, folder_id)

        placements = []
        for _ in range(3):
            message_id = _answer_with_chart(
                client, auth_headers, conversation_id, dataset["id"], monkeypatch
            )
            added = client.post(
                f"/api/v1/dashboards/{board['id']}/charts",
                json={"messageId": message_id},
                headers=auth_headers,
            )
            placements.append(added.json()["layout"])

        assert [p["y"] for p in placements] == [0, 4, 8]
        assert {p["x"] for p in placements} == {0}

    def test_a_default_sized_chart_leaves_room_for_two_more_beside_it(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The default width has to divide the grid into (at least) three, or
        a board never shows more than two charts per row without the user
        resizing everything by hand first."""
        from app.schemas.dashboards import GRID_COLUMNS

        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )

        w = added.json()["layout"]["w"]
        assert w * 3 <= GRID_COLUMNS

    def test_a_request_naming_both_sources_is_refused(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"savedChartId": "sc_1", "messageId": "m_1"},
            headers=auth_headers,
        )
        assert added.status_code == 422

    def test_an_answer_with_no_chart_cannot_be_added(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """Module C declining is a normal outcome; there is simply nothing to pin."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        _stub_models(monkeypatch)  # defaults to chart_type: "none"
        executed = _ask_sql(
            client, auth_headers, conversation_id, f"SELECT ciudad FROM {dataset['id']}"
        )
        message_id = executed.json()["message"]["id"]
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )
        assert added.status_code == 422
        assert added.json()["error"]["code"] == "no_chart_to_add"

    def test_the_pool_only_offers_pins_that_can_be_rebuilt(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """A chart pinned before Module C is a picture, not a query — putting one
        on a board that recomputes would make a card that can never refresh."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        pinned = client.post(
            f"/api/v1/datasets/{dataset['id']}/charts",
            json={"messageId": message_id, "chartType": "bar"},
            headers=auth_headers,
        )
        assert pinned.status_code == 201, pinned.text
        board = _board(client, auth_headers, folder_id)

        pool = client.get(
            f"/api/v1/dashboards/{board['id']}/available-charts", headers=auth_headers
        ).json()

        assert pinned.json()["id"] in [c["id"] for c in pool]
        assert all(c["result"]["chart"] is not None for c in pool)

    def test_a_chart_from_the_pool_lands_on_the_board(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        saved_id = client.post(
            f"/api/v1/datasets/{dataset['id']}/charts",
            json={"messageId": message_id, "chartType": "bar"},
            headers=auth_headers,
        ).json()["id"]
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"savedChartId": saved_id},
            headers=auth_headers,
        )

        assert added.status_code == 201, added.text
        assert added.json()["spec"]["mark"] == "bar"


class TestAddingAlsoPins:
    """Landing on a board is a stronger signal than the chat's own "Save"
    toggle — the user built something around the chart. `add_chart` treats it
    that way for the ask-a-question path, which is the one that has no pin
    yet; a chart added from the pool is already pinned."""

    def test_adding_from_a_fresh_answer_also_pins_it_to_the_folder(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)

        before = client.get(
            f"/api/v1/datasets/{dataset['id']}/charts", headers=auth_headers
        ).json()
        assert before == []

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )
        assert added.status_code == 201, added.text

        after = client.get(
            f"/api/v1/datasets/{dataset['id']}/charts", headers=auth_headers
        ).json()
        assert len(after) == 1
        # `_answer_with_chart` goes through the hand-edited-SQL path, where
        # there is no natural-language question — `question` carries the SQL
        # itself (see `conversations_engine.make_sql_draft`).
        assert after[0]["question"] == f"SELECT ciudad FROM {dataset['id']}"
        assert after[0]["result"]["chart"]["spec"]["mark"] == "bar"

    def test_adding_from_the_pool_does_not_duplicate_the_pin(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The chart is already in the pool — it's what's being added *from*."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        saved_id = client.post(
            f"/api/v1/datasets/{dataset['id']}/charts",
            json={"messageId": message_id, "chartType": "bar"},
            headers=auth_headers,
        ).json()["id"]
        board = _board(client, auth_headers, folder_id)

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"savedChartId": saved_id},
            headers=auth_headers,
        )
        assert added.status_code == 201, added.text

        pinned = client.get(
            f"/api/v1/datasets/{dataset['id']}/charts", headers=auth_headers
        ).json()
        assert len(pinned) == 1

    def test_a_pin_that_cannot_be_attributed_to_a_dataset_does_not_block_the_add(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The board chart is the action the caller asked for; the pin is a
        convenience on top of it and must not be able to fail that action."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)

        # The folder loses its only dataset between asking and adding, so
        # `_pin_for_folder` has nothing to attribute the save to.
        client.delete(
            f"/api/v1/folders/{folder_id}/datasets/{dataset['id']}", headers=auth_headers
        )

        added = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        )

        assert added.status_code == 201, added.text
        board_state = client.get(f"/api/v1/dashboards/{board['id']}", headers=auth_headers).json()
        assert len(board_state["charts"]) == 1


class TestLiveRecomputation:
    def test_a_chart_returns_the_rows_its_query_finds_now(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        chart_id = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        ).json()["id"]

        data = client.get(
            f"/api/v1/dashboards/{board['id']}/charts/{chart_id}/data", headers=auth_headers
        )

        assert data.status_code == 200, data.text
        body = data.json()
        assert body["status"] == "ok"
        assert body["rowCount"] == 3
        # A set: the SQL has no ORDER BY, so the order DuckDB returns is not a
        # promise and asserting one would make this test fail for the wrong
        # reason one day.
        assert {r["ciudad"] for r in body["table"]["rows"]} == {"Córdoba", "Mendoza", "Rosario"}

    def test_a_chart_reflects_the_parquet_changing_under_it(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The whole feature, in one test.

        The chart goes on the board, the file behind the folder gains a row, and
        the same card returns the new count without anything being re-pinned.
        Snapshotting rows at add time — the obvious "optimisation" — returns 3
        here and fails this test, which is exactly what it is for.
        """
        import duckdb

        from app.services import datasets as datasets_store

        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        chart_id = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        ).json()["id"]

        def rows_now() -> int:
            body = client.get(
                f"/api/v1/dashboards/{board['id']}/charts/{chart_id}/data",
                headers=auth_headers,
            ).json()
            assert body["status"] == "ok", body
            return body["rowCount"]

        assert rows_now() == 3

        # A fourth row lands in the Parquet the folder reads from — the ordinary
        # case of "the data was refreshed", with no ALIBI involvement at all.
        # The real owner id: the Parquet key is tenant-scoped, so an empty one
        # resolves to a path nothing was ever written to.
        owner_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["id"]
        uri = datasets_store.query_table(owner_id, dataset["id"], require_confirmed=True).uri
        con = duckdb.connect()
        con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{uri}')")
        con.execute("INSERT INTO t SELECT * FROM t LIMIT 1")
        con.execute(f"COPY t TO '{uri}' (FORMAT PARQUET)")
        con.close()

        assert rows_now() == 4, "the board must recompute, not replay"

    def test_a_query_that_no_longer_runs_fails_only_its_own_card(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """A stale chart is the expected failure on a board that recomputes, so
        it is reported on the card rather than as an error for the request."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        good_id = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        ).json()["id"]

        # Detaching the dataset leaves the folder with nothing to query, so the
        # chart's SQL names a table that is no longer registered.
        client.delete(
            f"/api/v1/folders/{folder_id}/datasets/{dataset['id']}", headers=auth_headers
        )

        data = client.get(
            f"/api/v1/dashboards/{board['id']}/charts/{good_id}/data", headers=auth_headers
        )

        assert data.status_code == 200, "the request succeeded; the chart did not"
        assert data.json()["status"] == "failed"
        assert data.json()["message"]

    def test_asking_for_a_chart_that_is_not_on_the_board_is_a_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        board = _board(client, auth_headers, folder_id)

        data = client.get(
            f"/api/v1/dashboards/{board['id']}/charts/dc_nope/data", headers=auth_headers
        )
        assert data.status_code == 404


class TestLayout:
    def test_dragging_is_persisted_so_a_board_looks_the_same_on_every_screen(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        chart_id = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        ).json()["id"]

        saved = client.put(
            f"/api/v1/dashboards/{board['id']}/layout",
            json={"layout": {chart_id: {"x": 6, "y": 2, "w": 4, "h": 6}}},
            headers=auth_headers,
        )

        assert saved.status_code == 200, saved.text
        reopened = client.get(f"/api/v1/dashboards/{board['id']}", headers=auth_headers).json()
        assert reopened["charts"][0]["layout"] == {"x": 6, "y": 2, "w": 4, "h": 6}

    def test_a_chart_the_layout_does_not_mention_keeps_its_place(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """A layout sent while another card is being added must not reset it."""
        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        board = _board(client, auth_headers, folder_id)
        ids = []
        for _ in range(2):
            message_id = _answer_with_chart(
                client, auth_headers, conversation_id, dataset["id"], monkeypatch
            )
            ids.append(
                client.post(
                    f"/api/v1/dashboards/{board['id']}/charts",
                    json={"messageId": message_id},
                    headers=auth_headers,
                ).json()["id"]
            )

        client.put(
            f"/api/v1/dashboards/{board['id']}/layout",
            json={"layout": {ids[0]: {"x": 0, "y": 0, "w": 12, "h": 3}}},
            headers=auth_headers,
        )

        charts = client.get(f"/api/v1/dashboards/{board['id']}", headers=auth_headers).json()[
            "charts"
        ]
        untouched = next(c for c in charts if c["id"] == ids[1])
        assert untouched["layout"]["y"] == 4

    def test_a_chart_can_be_widened_past_the_old_twelve_column_grid(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The grid grew from 12 to 24 columns; a width the old grid would
        have rejected outright (>12) has to be accepted now, or the frontend's
        own `COLS.lg` and the API's bound have quietly drifted apart."""
        from app.schemas.dashboards import GRID_COLUMNS

        dataset = _dataset(client, auth_headers)
        folder_id = _folder(client, auth_headers, [dataset["id"]], "Q1")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        message_id = _answer_with_chart(
            client, auth_headers, conversation_id, dataset["id"], monkeypatch
        )
        board = _board(client, auth_headers, folder_id)
        chart_id = client.post(
            f"/api/v1/dashboards/{board['id']}/charts",
            json={"messageId": message_id},
            headers=auth_headers,
        ).json()["id"]

        full_width = client.put(
            f"/api/v1/dashboards/{board['id']}/layout",
            json={"layout": {chart_id: {"x": 0, "y": 0, "w": GRID_COLUMNS, "h": 4}}},
            headers=auth_headers,
        )
        assert full_width.status_code == 200, full_width.text

        too_wide = client.put(
            f"/api/v1/dashboards/{board['id']}/layout",
            json={"layout": {chart_id: {"x": 0, "y": 0, "w": GRID_COLUMNS + 1, "h": 4}}},
            headers=auth_headers,
        )
        assert too_wide.status_code == 422
