"""The chat answers from the user's data, or it reports that it could not.

Two things are pinned here.

**The answer is real.** `execute` runs the confirmed SQL against the folder's own
Parquet and returns those rows. It used to copy a canned `QueryResult` and
replace only the SQL, so an answer showed the demo's Buenos Aires/Córdoba numbers
whichever file had been uploaded.

**Nothing stands in when a step fails.** There is no fixture path, no template
narrative, no canned chart. A dataset with no materialised table, an unconfigured
Module A, an unreachable narrator — each is an error the user can see, because a
plausible answer that nobody computed is the one failure they cannot detect.

Most tests here go through hand-edited SQL (`prepare` with `sql=`), which
exercises the real execution path without a Text-to-SQL model. The narrative
model is stubbed for the whole suite by `conftest._no_provider_calls`.
"""

from fastapi.testclient import TestClient

from tests.test_uploads import CSV, _upload


def _dataset(client: TestClient, headers: dict, filename: str = "ventas.csv") -> dict:
    """Upload, confirm the inferred schema, and return the activated dataset."""
    upload = _upload(client, headers, filename, CSV)
    dataset = upload["datasets"][0]
    schema = client.get(
        f"/api/v1/datasets/{dataset['id']}/schema", headers=headers
    ).json()
    confirmed = client.put(
        f"/api/v1/datasets/{dataset['id']}/schema",
        json={"columns": schema["columns"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _unconfirmed(client: TestClient, headers: dict, filename: str = "pendiente.csv") -> dict:
    """Upload without confirming: previewable, not queryable (Ingesta 1.2)."""
    dataset = _upload(client, headers, filename, CSV)["datasets"][0]
    assert dataset["status"] == "awaiting_confirmation"
    return dataset


def _stub_models(monkeypatch, *, chart=None, chart_unavailable: bool = False) -> None:
    """Answer the narrative and Module C differently on one shared transport.

    `explain_result` and `visualize` both call `orchestrator.client.complete`,
    which is a module attribute — patching it for one patches it for both. So
    the stub dispatches on the system prompt, exactly as `conftest` does, and a
    test that wants a chart cannot accidentally take the narrative down with it.
    """
    import json

    from app.orchestrator import client as orchestrator_client

    def _complete(config, messages, **kwargs):
        if "Vega-Lite" in (messages[0].get("content", "") if messages else ""):
            if chart_unavailable:
                raise orchestrator_client.LlmUnavailable("the chart model is down")
            body = chart if chart is not None else {"chart_type": "none", "spec": None}
        else:
            body = {
                "narrative": "Here is what the information shows.",
                "key_figures": [],
                "confidence_score": 0.9,
            }
        return orchestrator_client.Completion(
            text=json.dumps(body), model="stub", prompt_tokens=0, completion_tokens=0
        )

    monkeypatch.setattr(orchestrator_client, "complete", _complete)


def _ask_sql(client: TestClient, headers: dict, conversation_id: str, sql: str):
    prepared = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/prepare",
        json={"sql": sql},
        headers=headers,
    )
    assert prepared.status_code == 200, prepared.text
    return client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"draftId": prepared.json()["draftId"]},
        headers=headers,
    )


class TestRealResults:
    def test_the_table_holds_the_uploaded_rows(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        executed = _ask_sql(
            client,
            auth_headers,
            conversation_id,
            f"SELECT ciudad, monto FROM {dataset['id']} ORDER BY monto DESC",
        )
        assert executed.status_code == 201, executed.text
        result = executed.json()["message"]["result"]

        # The CSV's own rows — not the fixture's regions.
        assert [r["ciudad"] for r in result["table"]["rows"]] == [
            "Mendoza",
            "Córdoba",
            "Rosario",
        ]
        assert result["rowCount"] == 3
        assert result["rowsLabel"] == "3 filas"
        assert executed.json()["receipt"]["rows"] == 3

    def test_numeric_columns_are_right_aligned(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        # Same rule the ingestion preview uses, from one shared helper — the same
        # column must not align differently on two screens.
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        executed = _ask_sql(
            client, auth_headers, conversation_id, f"SELECT ciudad, monto FROM {dataset['id']}"
        )
        columns = {
            c["key"]: c["align"]
            for c in executed.json()["message"]["result"]["table"]["columns"]
        }
        assert columns == {"ciudad": "left", "monto": "num"}

    def test_an_aggregate_returns_its_own_shape(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        # The columns are whatever the SELECT produced, including an alias.
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        executed = _ask_sql(
            client, auth_headers, conversation_id, f"SELECT SUM(monto) AS total FROM {dataset['id']}"
        )
        result = executed.json()["message"]["result"]
        assert [c["key"] for c in result["table"]["columns"]] == ["total"]
        assert result["rowCount"] == 1

    def test_a_join_across_two_files_in_the_folder_returns_real_rows(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """The point of scoping a conversation to a folder, visible end to end."""
        first = _dataset(client, auth_headers, "ventas.csv")
        second = _dataset(client, auth_headers, "otras.csv")
        conversation_id = conversation_over(auth_headers, [first["id"], second["id"]])

        executed = _ask_sql(
            client,
            auth_headers,
            conversation_id,
            f"SELECT a.ciudad FROM {first['id']} a "
            f"JOIN {second['id']} b ON b.ciudad = a.ciudad ORDER BY a.ciudad",
        )
        assert executed.status_code == 201, executed.text
        rows = executed.json()["message"]["result"]["table"]["rows"]
        assert [r["ciudad"] for r in rows] == ["Córdoba", "Mendoza", "Rosario"]


class TestTheChart:
    """Module C rides along with the answer, and its absence is not a failure.

    The response used to carry `autoChartType`/`chartData`/`chartTitle`/
    `insights`, chosen by a deterministic classifier in the orchestrator. Those
    are gone for good — the classifier was a second opinion about the same table
    — and what replaced them is `chart`: a Vega-Lite v5 specification produced by
    `orchestrator/nodes/visualize.py` from the executed rows, validated against
    the official schema before it is returned.

    `chart` is null whenever no valid one was produced, and the answer is still
    complete. Nothing is substituted in its place.
    """

    def test_the_legacy_chart_fields_are_still_gone(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        executed = _ask_sql(
            client, auth_headers, conversation_id, f"SELECT ciudad FROM {dataset['id']}"
        )
        result = executed.json()["message"]["result"]
        assert result["rowCount"] == 3
        for gone in ("chartData", "autoChartType", "chartTitle", "insights"):
            assert gone not in result

    def test_the_answer_is_complete_when_module_c_declines(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """The suite's stub answers `chart_type: "none"` — a correct Module C
        answer. Nothing about the rest of the response changes."""
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        sql = f"SELECT ciudad FROM {dataset['id']}"
        result = _ask_sql(client, auth_headers, conversation_id, sql).json()["message"]["result"]
        assert result["chart"] is None
        assert result["naturalResponse"]
        assert result["sql"] == sql
        assert result["table"]["rows"]

    def test_a_validated_spec_reaches_the_response(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        spec = {
            "mark": "bar",
            "encoding": {
                "x": {"field": "ciudad", "type": "nominal", "title": "City"},
                "y": {"aggregate": "count", "type": "quantitative", "title": "Entries"},
            },
        }
        _stub_models(monkeypatch, chart={"chart_type": "bar", "spec": spec})

        sql = f"SELECT ciudad FROM {dataset['id']}"
        result = _ask_sql(client, auth_headers, conversation_id, sql).json()["message"]["result"]

        chart = result["chart"]
        assert chart["chartType"] == "bar"
        assert chart["spec"]["mark"] == "bar"
        # The rows are not shipped twice: the browser injects `table.rows`.
        assert "data" not in chart["spec"]
        assert [c["name"] for c in chart["columns"]] == ["ciudad"]

    def test_a_chart_that_cannot_be_produced_costs_only_the_chart(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The narrative fails loudly (503); the chart fails quietly (null).

        This asymmetry is the whole point of running them side by side: an
        answer with no sentence is not an answer, an answer with no picture is.
        """
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        _stub_models(monkeypatch, chart_unavailable=True)

        executed = _ask_sql(
            client, auth_headers, conversation_id, f"SELECT ciudad FROM {dataset['id']}"
        )
        assert executed.status_code == 201
        result = executed.json()["message"]["result"]
        assert result["chart"] is None
        assert result["naturalResponse"]


class TestNothingIsFaked:
    def test_a_dataset_with_no_materialised_table_is_an_error(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """`sales` is a seeded fixture with no Parquet behind it.

        This used to answer with a canned `QueryResult` — the demo's Buenos
        Aires numbers, under whatever SQL had been prepared. That is the single
        most dangerous thing the product did: visually identical to a real
        answer, and wrong. There is no fixture path now, so it reports instead.
        """
        conversation_id = conversation_over(auth_headers, ["sales"])
        prepared = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"sql": "SELECT 1 AS n FROM sales"},
            headers=auth_headers,
        )
        if prepared.status_code != 200:
            assert prepared.status_code in (409, 422)
            return
        executed = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"draftId": prepared.json()["draftId"]},
            headers=auth_headers,
        )
        assert executed.status_code == 409

    def test_a_question_with_no_model_configured_is_a_503(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """No canned SQL. The user must not confirm a query nobody generated."""
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        from app.services import text2sql

        monkeypatch.setattr(text2sql, "is_configured", lambda: False)
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"question": "which city sold the most?"},
            headers=auth_headers,
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "text2sql_unavailable"

    def test_a_failed_narrative_is_a_503_not_a_template(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        """The query ran; the prose did not. Reporting beats inventing."""
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        from app.orchestrator import client as orchestrator_client

        def boom(*_a, **_k):
            raise orchestrator_client.LlmUnavailable("connection refused")

        monkeypatch.setattr(orchestrator_client, "complete", boom)

        prepared = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"sql": f"SELECT ciudad FROM {dataset['id']}"},
            headers=auth_headers,
        )
        assert prepared.status_code == 200
        executed = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"draftId": prepared.json()["draftId"]},
            headers=auth_headers,
        )
        assert executed.status_code == 503
        assert executed.json()["error"]["code"] == "narrative_unavailable"


class TestExecutionErrors:
    def test_sql_naming_a_table_outside_the_conversation_is_refused(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        # Rejected at prepare by the AST guard, so execution is never reached.
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"sql": "SELECT * FROM some_other_table"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_a_query_that_fails_at_execution_is_reported_not_faked(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """A column that doesn't exist passes the name check but fails DuckDB."""
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        prepared = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"sql": f"SELECT ciudad FROM {dataset['id']} ORDER BY monto + 'x'"},
            headers=auth_headers,
        )
        if prepared.status_code != 200:
            return  # rejected earlier by the guard — also acceptable
        executed = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"draftId": prepared.json()["draftId"]},
            headers=auth_headers,
        )
        assert executed.status_code == 409
        assert executed.json()["error"]["code"] == "execution_failed"


class TestUnconfirmedIsNotQueryable:
    """Ingesta 1.2: an `awaiting_confirmation` table is previewable, not queryable.

    Querying an unconfirmed table would answer using column names and types the
    user never agreed to — and a rename made at confirmation would silently
    change what a saved query means.

    A folder resolves to its *confirmed* datasets, so a pending file inside one
    is simply left out rather than blocking it. Refusing the whole folder would
    break an open conversation the moment someone uploads a new file into it,
    which is the opposite of what resolve-on-read is for. Only a folder with
    nothing confirmed at all is refused, because there is nothing to ask about.
    """

    def test_a_pending_file_is_left_out_rather_than_blocking_the_folder(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        ready = _dataset(client, auth_headers, "lista.csv")
        pending = _unconfirmed(client, auth_headers)
        folder_id = folder_of(auth_headers, [ready["id"], pending["id"]], "Mixta")

        conversation = client.post(
            "/api/v1/conversations", json={"folderId": folder_id}, headers=auth_headers
        )
        assert conversation.status_code == 201
        assert conversation.json()["datasetIds"] == [ready["id"]]

    def test_a_folder_with_nothing_confirmed_is_refused(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        pending = _unconfirmed(client, auth_headers)
        folder_id = folder_of(auth_headers, [pending["id"]], "Sin confirmar")

        response = client.post(
            "/api/v1/conversations", json={"folderId": folder_id}, headers=auth_headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "schema_unconfirmed"

    def test_previewing_it_still_works(self, client: TestClient, auth_headers: dict) -> None:
        # The confirmation screen exists to show exactly this table; blocking the
        # preview too would make the file impossible to confirm.
        dataset = _unconfirmed(client, auth_headers)
        preview = client.get(
            f"/api/v1/datasets/{dataset['id']}/preview", headers=auth_headers
        )
        assert preview.status_code == 200
        assert len(preview.json()["rows"]) == 3

    def test_confirming_makes_it_queryable(
        self, client: TestClient, auth_headers: dict, folder_of
    ) -> None:
        dataset = _unconfirmed(client, auth_headers)
        folder_id = folder_of(auth_headers, [dataset["id"]], "Pendiente")
        assert (
            client.post(
                "/api/v1/conversations", json={"folderId": folder_id}, headers=auth_headers
            ).status_code
            == 409
        )

        schema = client.get(
            f"/api/v1/datasets/{dataset['id']}/schema", headers=auth_headers
        ).json()
        client.put(
            f"/api/v1/datasets/{dataset['id']}/schema",
            json={"columns": schema["columns"]},
            headers=auth_headers,
        )
        response = client.post(
            "/api/v1/conversations", json={"folderId": folder_id}, headers=auth_headers
        )
        assert response.status_code == 201


class TestRenamedColumnsStayQueryable:
    """The schema editor lets a column be renamed to anything; SQL still works.

    The stored name is the *display* name, so it can be `Monto Total`. Module A
    is shown folded identifiers (`monto_total`). The DuckDB view therefore has to
    expose the folded name too — it used to alias the raw one, so the model's SQL
    passed the guardrail (validated against the folded set) and then failed in
    DuckDB with an opaque "column not found".
    """

    def _renamed(self, client: TestClient, headers: dict, new_name: str) -> dict:
        upload = _upload(client, headers, "v.csv", CSV)
        dataset = upload["datasets"][0]
        schema = client.get(
            f"/api/v1/datasets/{dataset['id']}/schema", headers=headers
        ).json()
        schema["columns"][1]["name"] = new_name
        confirmed = client.put(
            f"/api/v1/datasets/{dataset['id']}/schema",
            json={"columns": schema["columns"]},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        return confirmed.json()

    def test_a_renamed_column_is_queryable_under_its_folded_name(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = self._renamed(client, auth_headers, "Monto Total")
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        executed = _ask_sql(
            client,
            auth_headers,
            conversation_id,
            f"SELECT monto_total FROM {dataset['id']} ORDER BY monto_total",
        )
        assert executed.status_code == 201, executed.text
        rows = executed.json()["message"]["result"]["table"]["rows"]
        assert [r["monto_total"] for r in rows] == [980.0, 1234.56, 2500.1]

    def test_the_ddl_and_the_view_agree(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """The actual invariant, asserted directly rather than via a query."""
        from app.services import datasets as ds
        from app.services.text2sql import ddl as ddl_module

        from tests.test_uploads import _current_user

        dataset = self._renamed(client, auth_headers, "Año de Alta")
        user = _current_user(client, auth_headers)
        schema = ds.get_schema(user.id, dataset["id"])
        table = ds.query_table(user.id, dataset["id"], require_confirmed=True)

        _, allowed = ddl_module.allowed_names([(dataset["id"], schema)])
        # Every column the guardrail admits is a column the view actually exposes.
        assert allowed == set(table.projection)
        assert "ano_de_alta" in allowed

    def test_two_names_that_fold_alike_stay_distinct(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # A view cannot expose two columns under one name, so the collision has
        # to be suffixed rather than silently dropping one.
        upload = _upload(client, auth_headers, "v.csv", CSV)
        dataset = upload["datasets"][0]
        schema = client.get(
            f"/api/v1/datasets/{dataset['id']}/schema", headers=auth_headers
        ).json()
        schema["columns"][0]["name"] = "Monto Total"
        schema["columns"][1]["name"] = "monto_total"
        client.put(
            f"/api/v1/datasets/{dataset['id']}/schema",
            json={"columns": schema["columns"]},
            headers=auth_headers,
        )

        from app.services import datasets as ds

        from tests.test_uploads import _current_user

        user = _current_user(client, auth_headers)
        table = ds.query_table(user.id, dataset["id"], require_confirmed=True)
        assert sorted(table.projection)[:2] == ["fecha", "monto_total"]
        assert "monto_total_2" in table.projection
