"""The fine-tuned models' transport (`app/core/sagemaker.py`).

Pinned here:

* **Asleep is read from the copy count, not from error text.** An invocation
  that fails while the inference component has 0 copies is `ModelAsleep`; the
  same failure with a copy running is an ordinary `SageMakerUnavailable`.
* **Waking is idempotent.** A copy is asked for only when none is desired.
* **`ensure_awake` shows a wait only when there is one**, waits it out, and
  gives up with `ModelAsleep` once the budget is spent.
* **The bridge carries "asleep" as a code**, so it survives the relay.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from app.core import sagemaker
from app.core.sagemaker import ModelAsleep, SageMakerUnavailable, Target

TARGET = Target(endpoint_name="model-a-dev", inference_component="alibi-models-ic")


class _Runtime:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def invoke_endpoint(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"Body": io.BytesIO(json.dumps(nxt).encode())}


class _Control:
    def __init__(self, current: int = 1, desired: int = 1) -> None:
        self.current, self.desired = current, desired
        self.instances = 1
        self.updates: list[dict] = []

    def describe_inference_component(self, **_kwargs: Any) -> dict:
        return {"RuntimeConfig": {"CurrentCopyCount": self.current, "DesiredCopyCount": self.desired}}

    def describe_endpoint(self, **_kwargs: Any) -> dict:
        return {"ProductionVariants": [{"CurrentInstanceCount": self.instances}]}

    def update_inference_component_runtime_config(self, **kwargs: Any) -> dict:
        self.updates.append(kwargs)
        self.desired = kwargs["DesiredRuntimeConfig"]["CopyCount"]
        return {}


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch):
    def _install(runtime: _Runtime, control: _Control) -> tuple[_Runtime, _Control]:
        monkeypatch.setattr(sagemaker, "_runtime", lambda *_a: runtime)
        monkeypatch.setattr(sagemaker, "_control", lambda *_a: control)
        return runtime, control

    return _install


class TestInvoke:
    def test_names_the_inference_component_and_returns_the_reply(self, clients) -> None:
        runtime, _ = clients(_Runtime([{"task": "sql", "sql": "SELECT 1"}]), _Control())

        reply = sagemaker.invoke(TARGET, {"task": "sql", "schema": "s", "question": "q"})

        assert reply["sql"] == "SELECT 1"
        call = runtime.calls[0]
        assert call["EndpointName"] == "model-a-dev"
        assert call["InferenceComponentName"] == "alibi-models-ic"
        assert json.loads(call["Body"]) == {"task": "sql", "schema": "s", "question": "q"}

    def test_a_classic_endpoint_names_no_component(self, clients) -> None:
        runtime, _ = clients(_Runtime([{"task": "sql", "sql": "SELECT 1"}]), _Control())
        sagemaker.invoke(Target(endpoint_name="model-a-dev"), {"task": "sql"})
        assert "InferenceComponentName" not in runtime.calls[0]

    def test_a_failure_at_zero_copies_is_asleep(self, clients) -> None:
        clients(_Runtime([RuntimeError("no capacity")]), _Control(current=0, desired=0))
        with pytest.raises(ModelAsleep):
            sagemaker.invoke(TARGET, {"task": "sql"})

    def test_a_failure_with_a_copy_running_is_not_asleep(self, clients) -> None:
        clients(_Runtime([RuntimeError("model error")]), _Control(current=1))
        with pytest.raises(SageMakerUnavailable) as raised:
            sagemaker.invoke(TARGET, {"task": "sql"})
        assert not isinstance(raised.value, ModelAsleep)

    def test_a_handler_refusal_is_unavailable(self, clients) -> None:
        clients(_Runtime([{"task": "sql", "error": "no 'sql' adapter loaded"}]), _Control())
        with pytest.raises(SageMakerUnavailable, match="adapter"):
            sagemaker.invoke(TARGET, {"task": "sql"})

    def test_a_chart_reply_with_an_error_is_still_the_models_answer(self, clients) -> None:
        # `error` on a chart reply means the model's text was not JSON: that is
        # for visualize() to judge, not a transport failure.
        reply = {"task": "chart", "spec": None, "valid_json": False, "error": "bad json", "raw": "{"}
        clients(_Runtime([reply]), _Control())
        assert sagemaker.invoke(TARGET, {"task": "chart"})["raw"] == "{"


class TestWake:
    def test_asks_for_one_copy_when_none_is_desired(self, clients) -> None:
        _, control = clients(_Runtime([]), _Control(current=0, desired=0))
        assert sagemaker.wake(TARGET) == "waking"
        assert control.updates == [
            {"InferenceComponentName": "alibi-models-ic", "DesiredRuntimeConfig": {"CopyCount": 1}}
        ]

    def test_does_not_ask_again_while_a_copy_is_coming(self, clients) -> None:
        _, control = clients(_Runtime([]), _Control(current=0, desired=1))
        assert sagemaker.wake(TARGET) == "waking"
        assert control.updates == []

    def test_ready_when_a_copy_runs(self, clients) -> None:
        _, control = clients(_Runtime([]), _Control(current=1, desired=1))
        assert sagemaker.wake(TARGET) == "ready"
        assert control.updates == []


class TestEnsureAwake:
    def test_an_awake_model_returns_without_announcing_a_wait(self, clients) -> None:
        clients(_Runtime([{"task": "ping", "ready": True}]), _Control(current=1))
        waits: list[int] = []
        sagemaker.ensure_awake(TARGET, wait_seconds=60, on_waking=lambda: waits.append(1), poll_seconds=0)
        assert waits == []

    def test_an_old_handler_without_ping_counts_as_ready(self, clients) -> None:
        clients(_Runtime([{"task": "ping", "error": "unknown task 'ping'"}]), _Control(current=1))
        sagemaker.ensure_awake(TARGET, wait_seconds=60, poll_seconds=0)

    def test_an_asleep_model_is_woken_announced_once_and_waited_for(
        self, clients, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        control = _Control(current=0, desired=0)
        # Loading after the copy comes up: one failed probe, then an answer.
        runtime = _Runtime([RuntimeError("still loading"), {"task": "ping", "ready": True}])
        clients(runtime, control)
        polls = iter([0, 1, 1])

        def _sleep(_seconds: float) -> None:
            control.current = next(polls)

        monkeypatch.setattr(sagemaker, "_sleep", _sleep)
        waits: list[int] = []

        sagemaker.ensure_awake(TARGET, wait_seconds=600, on_waking=lambda: waits.append(1), poll_seconds=1)

        assert waits == [1]
        assert len(control.updates) == 1
        assert len(runtime.calls) == 2

    def test_gives_up_asleep_when_the_budget_runs_out(self, clients, monkeypatch: pytest.MonkeyPatch) -> None:
        clients(_Runtime([]), _Control(current=0, desired=0))
        clock = iter(range(0, 10_000, 100))
        monkeypatch.setattr(sagemaker, "_now", lambda: next(clock))
        monkeypatch.setattr(sagemaker, "_sleep", lambda _s: None)

        with pytest.raises(ModelAsleep):
            sagemaker.ensure_awake(TARGET, wait_seconds=300, poll_seconds=20)

    def test_gives_up_early_when_no_instance_ever_comes(self, clients, monkeypatch: pytest.MonkeyPatch) -> None:
        control = _Control(current=0, desired=1)
        control.instances = 0
        clients(_Runtime([]), control)
        clock = iter(range(0, 10_000, 60))
        monkeypatch.setattr(sagemaker, "_now", lambda: next(clock))
        monkeypatch.setattr(sagemaker, "_sleep", lambda _s: None)

        with pytest.raises(sagemaker.ModelNoCapacity):
            sagemaker.ensure_awake(TARGET, wait_seconds=720, poll_seconds=20, no_instance_seconds=360)

    def test_a_loading_model_with_an_instance_keeps_waiting(self, clients, monkeypatch: pytest.MonkeyPatch) -> None:
        clients(_Runtime([]), _Control(current=0, desired=1))  # instances = 1
        clock = iter(range(0, 10_000, 100))
        monkeypatch.setattr(sagemaker, "_now", lambda: next(clock))
        monkeypatch.setattr(sagemaker, "_sleep", lambda _s: None)

        with pytest.raises(ModelAsleep) as raised:
            sagemaker.ensure_awake(TARGET, wait_seconds=720, poll_seconds=20, no_instance_seconds=360)
        assert not isinstance(raised.value, sagemaker.ModelNoCapacity)

    def test_an_always_on_endpoint_that_fails_is_not_waited_for(self, clients) -> None:
        clients(_Runtime([RuntimeError("broken")]), _Control())
        with pytest.raises(SageMakerUnavailable):
            sagemaker.ensure_awake(Target(endpoint_name="model-a-dev"), wait_seconds=600, poll_seconds=0)


class TestBridgeRelay:
    def test_asleep_survives_the_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core import llm_bridge_transport
        from app.core.config import get_settings

        monkeypatch.setenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", "alibi-llm-bridge-dev")
        get_settings.cache_clear()
        sent: list[dict] = []

        def _call(function_name, payload, **_kwargs):
            sent.append(payload)
            return {"ok": False, "error": "no running copy", "code": "model_asleep"}

        monkeypatch.setattr(llm_bridge_transport, "call", _call)
        try:
            with pytest.raises(ModelAsleep):
                sagemaker.invoke(TARGET, {"task": "sql"})
        finally:
            get_settings.cache_clear()
        assert sent[0]["kind"] == "sagemaker" and sent[0]["op"] == "invoke"
        assert sent[0]["target"]["inference_component"] == "alibi-models-ic"

    def test_the_bridge_handler_reports_asleep_as_a_code(self, clients) -> None:
        from app.llm_bridge import bridge_handler

        clients(_Runtime([RuntimeError("no capacity")]), _Control(current=0, desired=0))
        body = bridge_handler(
            {"kind": "sagemaker", "op": "invoke", "target": vars(TARGET) | {}, "payload": {"task": "sql"}}
        )
        assert body["ok"] is False and body["code"] == "model_asleep"

    def test_the_bridge_handler_wakes(self, clients) -> None:
        from app.llm_bridge import bridge_handler

        _, control = clients(_Runtime([]), _Control(current=0, desired=0))
        body = bridge_handler({"kind": "sagemaker", "op": "wake", "target": vars(TARGET) | {}})
        assert body == {"ok": True, "completion": {"state": "waking", "instances": 1}}
        assert control.updates
