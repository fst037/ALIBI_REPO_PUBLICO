"""Answer runs: `POST …/runs` returns at once, a worker answers, the client polls.

Pinned here:

* **The same answer as the synchronous path.** A run executes `prepare` →
  `execute`'s own code as steps, so its message is the one `execute` returns.
* **Every ending is recorded.** Answer, guard, and each failure the synchronous
  endpoints raise as an HTTP error all end the run with a readable outcome —
  nothing leaves it `running`.
* **Progress is visible.** Steps appear in order with their SQL and row count.
* **Owner and conversation scoping**, and staleness when a worker died.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.services.run_jobs import get_run_runner
from tests.test_chat_execution import _dataset


def _start(client: TestClient, headers: dict, conversation_id: str, **body) -> dict:
    started = client.post(f"/api/v1/conversations/{conversation_id}/runs", json=body, headers=headers)
    assert started.status_code == 202, started.text
    return started.json()


def _finished(client: TestClient, headers: dict, conversation_id: str, run_id: str) -> dict:
    get_run_runner().wait_all(timeout=10)
    polled = client.get(f"/api/v1/conversations/{conversation_id}/runs/{run_id}", headers=headers)
    assert polled.status_code == 200, polled.text
    return polled.json()


class TestARunAnswers:
    def test_it_returns_queued_at_once_and_ends_with_the_answer(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        started = _start(client, auth_headers, conversation_id,
                         sql=f"SELECT ciudad, monto FROM {dataset['id']} ORDER BY monto DESC")
        assert started["status"] == "queued" and started["isSql"] is True

        run = _finished(client, auth_headers, conversation_id, started["id"])
        assert run["status"] == "done", run
        rows = run["message"]["result"]["table"]["rows"]
        assert [r["ciudad"] for r in rows] == ["Mendoza", "Córdoba", "Rosario"]
        assert run["receipt"]["rows"] == 3

        # And it is in the conversation, like a synchronous answer.
        conversation = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers).json()
        assert run["message"]["id"] in [m["id"] for m in conversation["messages"]]

    def test_the_steps_show_the_work_in_order(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        sql = f"SELECT ciudad FROM {dataset['id']}"

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, sql=sql)["id"])

        steps = run["steps"]
        assert [s["label"] for s in steps] == [
            "Revisando tu SQL",
            "Consultando los datos",
            "Redactando la respuesta y el gráfico",
        ]
        assert all(s["status"] == "done" and s["finishedAt"] for s in steps)
        query_step = steps[1]
        assert query_step["purpose"] == "insight"
        assert query_step["rowCount"] == 3
        assert dataset["id"] in query_step["sql"]


class TestEveryEndingIsRecorded:
    def test_a_write_intent_ends_as_a_guard_not_a_failure(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="borrá todas las ventas")["id"])

        assert run["status"] == "done"
        assert run["guard"]["status"] == "blocked"
        assert run["message"] is None

    def test_an_unconfigured_sql_model_fails_with_the_same_code_as_prepare(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="ventas por ciudad")["id"])

        assert run["status"] == "failed"
        assert run["error"]["code"] == "text2sql_unavailable"
        # Checked before the planner spends a call: nothing was started.
        assert run["steps"] == []

    def test_a_query_error_fails_the_query_step(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id,
                               sql=f"SELECT columna_que_no_existe FROM {dataset['id']}")["id"])

        assert run["status"] == "failed"
        assert run["error"]["code"]
        assert [s["status"] for s in run["steps"]][-1] == "failed"

    def test_an_unexpected_crash_still_ends_the_run(
        self, client: TestClient, auth_headers: dict, conversation_over, monkeypatch
    ) -> None:
        from app.services import conversations

        def boom(*_a, **_k):
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr(conversations, "answer", boom)
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id, question="hola")["id"])

        assert run["status"] == "failed"
        assert run["error"]["code"] == "internal_error"


class TestScoping:
    def test_exactly_one_of_question_or_sql(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])
        for body in ({}, {"question": "a", "sql": "SELECT 1"}):
            response = client.post(f"/api/v1/conversations/{conversation_id}/runs", json=body, headers=auth_headers)
            assert response.status_code == 422, body

    def test_an_unknown_conversation_is_a_404(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post("/api/v1/conversations/conv_nope/runs", json={"question": "a"}, headers=auth_headers)
        assert response.status_code == 404

    def test_a_run_is_only_visible_under_its_own_conversation(
        self, client: TestClient, auth_headers: dict, conversation_over
    ) -> None:
        dataset = _dataset(client, auth_headers)
        first = conversation_over(auth_headers, [dataset["id"]], name="Uno")
        second = conversation_over(auth_headers, [dataset["id"]], name="Dos")
        run_id = _start(client, auth_headers, first, sql=f"SELECT ciudad FROM {dataset['id']}")["id"]
        get_run_runner().wait_all(timeout=10)

        assert client.get(f"/api/v1/conversations/{second}/runs/{run_id}", headers=auth_headers).status_code == 404


class TestStaleness:
    def test_a_run_whose_worker_died_reads_as_failed(self) -> None:
        from app.schemas.runs import Run, RunStatus
        from app.services import runs, runs_memory

        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        runs_memory.save_run("u1", Run(id="run_x", conversationId="c1", question="q",
                                       status=RunStatus.running, createdAt=old, updatedAt=old))

        run = runs.get_run("u1", "c1", "run_x")
        assert run.status is RunStatus.failed
        assert run.error.code == "run_timeout"
        # Read-time only: nothing was written.
        assert runs_memory.get_run("u1", "run_x").status is RunStatus.running

    def test_a_second_delivery_of_the_same_run_is_ignored(self, monkeypatch) -> None:
        from app.schemas.runs import Run, RunStatus
        from app.services import conversations, runs, runs_memory

        calls = []
        monkeypatch.setattr(conversations, "answer", lambda *a, **k: calls.append(1))
        now = datetime.now(timezone.utc).isoformat()
        runs_memory.save_run("u1", Run(id="run_y", conversationId="c1", question="q",
                                       status=RunStatus.done, createdAt=now, updatedAt=now))

        runs.execute_run("u1", "run_y")
        assert calls == []
