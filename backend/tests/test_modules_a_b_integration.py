"""Modules B and A end to end: an uploaded file becomes a question's answer.

The one test that proves the two modules are actually consolidated. Everything
between the CSV and the rows is real — Module B's parsing/typing/Parquet, the
stored schema, Module A's DDL, the AST guardrail, the DuckDB view, the execution.
Only the *model* is stubbed, because there is no key in CI.

The stub is deliberately not a canned string. It **parses the DDL it was handed**
and builds its SQL from those identifiers, exactly as the real model would. That
is what makes this a consolidation test rather than a plumbing test: if Module A
ever describes a table or column that Module B did not actually materialise, the
generated SQL names something that doesn't exist and the test fails — which is
precisely the class of bug (`monto_total` vs `"Monto Total"`, `ds_x` vs `ventas`)
that this pairing has produced twice.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.services import text2sql
from app.services.text2sql.client import Completion
from tests.test_uploads import CSV, _upload


def _schema_from_prompt(messages: list[dict]) -> tuple[str, list[str]]:
    """(table, columns) as Module A actually described them to the model."""
    user = next(m["content"] for m in messages if m["role"] == "user")
    ddl = user.split("### Schema:\n", 1)[1].split("\n\n### Question:", 1)[0]
    # First table only; enough to answer, and the shape is the same for the rest.
    match = re.match(r"CREATE TABLE (\w+) \((.*?)\)(?:;|$)", ddl)
    assert match, f"unexpected DDL shape: {ddl!r}"
    table = match.group(1)
    columns = [part.strip().split(" ")[0] for part in match.group(2).split(", ")]
    return table, columns


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch):
    """A model that answers from the schema it was given, and records the prompt."""
    seen: list[dict] = []

    def fake_complete(config, messages, **kwargs):
        seen.append({"messages": messages})
        table, columns = _schema_from_prompt(messages)
        # Double-quoted literal: what this fine-tune emits 83.3% of the time, so
        # the mysql→duckdb transpile in `sql.check` is exercised too.
        return Completion(
            text=f'SELECT {columns[0]} FROM {table} WHERE {columns[0]} != "x"',
            model="stub",
            prompt_tokens=100,
            completion_tokens=20,
        )

    monkeypatch.setattr(text2sql, "complete", fake_complete)
    monkeypatch.setenv("ALIBI_TEXT2SQL_MODEL_ID", "qwen3-32b-ft")
    text2sql.get_settings.cache_clear()
    yield seen
    text2sql.get_settings.cache_clear()


def _ready_dataset(client: TestClient, headers: dict, filename: str = "ventas.csv") -> dict:
    upload = _upload(client, headers, filename, CSV)
    dataset = upload["datasets"][0]
    schema = client.get(f"/api/v1/datasets/{dataset['id']}/schema", headers=headers).json()
    confirmed = client.put(
        f"/api/v1/datasets/{dataset['id']}/schema",
        json={"columns": schema["columns"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _ask(client: TestClient, headers: dict, conversation_id: str, question: str):
    prepared = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/prepare",
        json={"question": question},
        headers=headers,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert "draftId" in body, f"guardrail blocked it: {body}"
    executed = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"draftId": body["draftId"]},
        headers=headers,
    )
    return body, executed


class TestUploadToAnswer:
    def test_a_question_over_an_uploaded_file_returns_its_own_rows(
        self, client: TestClient, auth_headers: dict, model, conversation_over
    ) -> None:
        dataset = _ready_dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        prepared, executed = _ask(client, auth_headers, conversation_id, "Which cities?")
        assert executed.status_code == 201, executed.text

        # The SQL is the model's, transpiled to DuckDB (the double-quoted literal
        # became a single-quoted one, or it would have read as an identifier).
        assert "'x'" in prepared["sql"]
        # The rows are the CSV's.
        rows = executed.json()["message"]["result"]["table"]["rows"]
        assert sorted(r["ciudad"] for r in rows) == ["Córdoba", "Mendoza", "Rosario"]

    def test_the_prompt_describes_what_module_b_really_wrote(
        self, client: TestClient, auth_headers: dict, model, conversation_over
    ) -> None:
        """The consolidation invariant, stated directly."""
        dataset = _ready_dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        _ask(client, auth_headers, conversation_id, "Which cities?")

        table, columns = _schema_from_prompt(model[0]["messages"])
        # The table identifier is the dataset id, and it is what the DuckDB view
        # is registered as — the mismatch that used to make every query fail.
        assert table == dataset["id"].lower()
        assert columns == ["ciudad", "monto", "fecha"]

    def test_a_renamed_column_survives_the_round_trip(
        self, client: TestClient, auth_headers: dict, model, conversation_over
    ) -> None:
        # The schema editor allows any display name; the model must be told the
        # identifier the view actually exposes.
        upload = _upload(client, auth_headers, "v.csv", CSV)
        dataset = upload["datasets"][0]
        schema = client.get(
            f"/api/v1/datasets/{dataset['id']}/schema", headers=auth_headers
        ).json()
        schema["columns"][0]["name"] = "Ciudad de Origen"
        client.put(
            f"/api/v1/datasets/{dataset['id']}/schema",
            json={"columns": schema["columns"]},
            headers=auth_headers,
        )

        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        _, executed = _ask(client, auth_headers, conversation_id, "Which cities?")
        assert executed.status_code == 201, executed.text
        rows = executed.json()["message"]["result"]["table"]["rows"]
        assert len(rows) == 3
        assert "ciudad_de_origen" in rows[0]

    def test_two_files_reach_the_prompt_as_one_schema(
        self, client: TestClient, auth_headers: dict, model, conversation_over
    ) -> None:
        first = _ready_dataset(client, auth_headers, "ventas.csv")
        second = _ready_dataset(client, auth_headers, "otras.csv")
        conversation_id = conversation_over(auth_headers, [first["id"], second["id"]])
        _ask(client, auth_headers, conversation_id, "Which cities?")

        user = next(m["content"] for m in model[0]["messages"] if m["role"] == "user")
        ddl = user.split("### Schema:\n", 1)[1].split("\n\n### Question:", 1)[0]
        assert first["id"].lower() in ddl and second["id"].lower() in ddl
        # One line, semicolon-separated: 0 of the fine-tune's 74,649 training
        # schemas contained a newline.
        assert "\n" not in ddl
        assert ddl.count("CREATE TABLE") == 2


class TestNoCredentialsIsAnError:
    def test_no_credentials_means_a_503(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        """No `model` fixture, so nothing is configured — an empty API key.

        This used to hand back the fixture's SQL: a query naming the demo's
        tables, presented as the answer to the user's question, which they were
        then invited to confirm and save. It is now refused, because a user
        cannot tell a generated query from a canned one by looking at it.
        """
        dataset = _ready_dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        prepared = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/prepare",
            json={"question": "Which cities?"},
            headers=auth_headers,
        )
        assert prepared.status_code == 503
        assert prepared.json()["error"]["code"] == "text2sql_unavailable"
