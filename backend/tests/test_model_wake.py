"""A run on the fine-tuned models, when their endpoint has scaled to zero.

Pinned here:

* **A wait is a visible step**, shown only when there is one, and the run then
  answers normally.
* **A model that never comes back ends the run** with 503 `model_waking`, not a
  hang and not a fallback to another model.
* **Hand-written SQL does not wait for a chart**: the wake is requested and the
  answer goes on without one.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core import sagemaker
from app.core.config import get_settings
from app.services.conversations import _WAKING_LABEL
from tests.test_chat_execution import _dataset
from tests.test_harness import _ask, _finish, models  # noqa: F401 - fixture
from tests.test_runs import _finished, _start


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch):
    """Both modules on SageMaker; an endpoint that wakes after `polls_to_wake` polls."""
    monkeypatch.setenv("ALIBI_TEXT2SQL_PROVIDER", "sagemaker")
    monkeypatch.setenv("ALIBI_TEXT2SQL_PROMPT_FORMAT", "qwen-bird-spider-v1")
    monkeypatch.setenv("ALIBI_VISUALIZATION_PROVIDER", "sagemaker")
    monkeypatch.setenv("ALIBI_SAGEMAKER_ENDPOINT_NAME", "model-a-dev")
    monkeypatch.setenv("ALIBI_SAGEMAKER_INFERENCE_COMPONENT", "alibi-models-ic")
    monkeypatch.setenv("ALIBI_MODEL_WAKE_WAIT_SECONDS", "600")
    get_settings.cache_clear()

    state = {"polls_to_wake": 0, "wakes": 0, "invoked": []}

    def _wake(_target):
        state["wakes"] += 1
        if state["polls_to_wake"] is None:
            return "waking"
        if state["polls_to_wake"] > 0:
            state["polls_to_wake"] -= 1
            return "waking"
        return "ready"

    def _invoke(_target, payload):
        state["invoked"].append(payload["task"])
        if payload["task"] == "ping":
            return {"task": "ping", "ready": True}
        if payload["task"] == "chart":
            return {"task": "chart", "raw": '{"chart_type": "none", "spec": null}'}
        table = re.search(r"CREATE TABLE (\w+)", payload["schema"]).group(1)
        return {"task": "sql", "sql": f"SELECT ciudad, monto FROM {table} ORDER BY monto DESC"}

    clock = {"now": 0.0}
    monkeypatch.setattr(sagemaker, "wake_status", lambda target: (_wake(target), 1))
    monkeypatch.setattr(sagemaker, "invoke", _invoke)
    monkeypatch.setattr(sagemaker, "_sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    monkeypatch.setattr(sagemaker, "_now", lambda: clock["now"])
    yield state
    get_settings.cache_clear()


def _question_run(client: TestClient, auth_headers: dict, conversation_over, models) -> dict:  # noqa: F811
    conversation_id = conversation_over(auth_headers, [_dataset(client, auth_headers)["id"]])
    models["planner"] = [_ask(1, "insight", "amount per city", "Comparando ciudades"), _finish("r1")]
    return _finished(client, auth_headers, conversation_id,
                     _start(client, auth_headers, conversation_id, question="¿Qué ciudad vendió más?")["id"])


class TestAQuestionRun:
    def test_an_awake_model_adds_no_step(
        self, client: TestClient, auth_headers: dict, conversation_over, models, endpoint  # noqa: F811
    ) -> None:
        run = _question_run(client, auth_headers, conversation_over, models)

        assert run["status"] == "done", run
        assert _WAKING_LABEL not in [s["label"] for s in run["steps"]]
        assert endpoint["invoked"][:2] == ["ping", "sql"]

    def test_an_asleep_model_is_a_visible_wait_then_an_answer(
        self, client: TestClient, auth_headers: dict, conversation_over, models, endpoint  # noqa: F811
    ) -> None:
        endpoint["polls_to_wake"] = 3

        run = _question_run(client, auth_headers, conversation_over, models)

        assert run["status"] == "done", run
        labels = [s["label"] for s in run["steps"]]
        assert labels[0] == _WAKING_LABEL
        assert run["steps"][0]["status"] == "done"
        assert labels.count(_WAKING_LABEL) == 1
        assert [r["ciudad"] for r in run["message"]["result"]["table"]["rows"]] == ["Mendoza", "Córdoba", "Rosario"]

    def test_a_model_that_never_wakes_ends_the_run_as_model_waking(
        self, client: TestClient, auth_headers: dict, conversation_over, models, endpoint  # noqa: F811
    ) -> None:
        endpoint["polls_to_wake"] = None

        run = _question_run(client, auth_headers, conversation_over, models)

        assert run["status"] == "failed"
        assert run["error"]["code"] == "model_waking"
        assert run["steps"][0]["label"] == _WAKING_LABEL and run["steps"][0]["status"] == "failed"
        assert "sql" not in endpoint["invoked"]


class TestAHandWrittenSqlRun:
    def test_does_not_wait_for_a_chart(
        self, client: TestClient, auth_headers: dict, conversation_over, endpoint
    ) -> None:
        endpoint["polls_to_wake"] = None
        dataset = _dataset(client, auth_headers)
        conversation_id = conversation_over(auth_headers, [dataset["id"]])

        run = _finished(client, auth_headers, conversation_id,
                        _start(client, auth_headers, conversation_id,
                               sql=f"SELECT ciudad, monto FROM {dataset['id']}")["id"])

        assert run["status"] == "done", run
        assert endpoint["wakes"] == 1
        assert _WAKING_LABEL not in [s["label"] for s in run["steps"]]
