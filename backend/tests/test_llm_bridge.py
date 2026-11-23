"""The model-call bridge: the boto3 transport, both clients' routing decision,
and the Lambda handler on the other end.

No network and no real boto3 client anywhere here — `llm_bridge_transport._client`
is monkeypatched to a fake with a controlled `.invoke()`, same spirit as
`conftest.py`'s `_no_provider_calls` for the direct-call path.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.core.llm_bridge_transport import invoke as invoke_bridge
from app.llm_bridge import bridge_handler
from app.orchestrator.client import Completion as OrchestratorCompletion
from app.orchestrator.client import LlmConfig
from app.orchestrator.client import LlmUnavailable as OrchestratorUnavailable
from app.orchestrator.client import complete as orchestrator_complete
from app.services.text2sql.client import Completion as Text2sqlCompletion
from app.services.text2sql.client import EndpointConfig
from app.services.text2sql.client import LlmUnavailable as Text2sqlUnavailable
from app.services.text2sql.client import complete as text2sql_complete


class _FakeLambdaClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def invoke(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.response


def _payload_response(body: dict, function_error: bool = False) -> dict:
    response = {"Payload": _Readable(json.dumps(body).encode())}
    if function_error:
        response["FunctionError"] = "Unhandled"
    return response


class _Readable:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# app/core/llm_bridge_transport.py
# ---------------------------------------------------------------------------


class _Unavailable(Exception):
    pass


def test_invoke_returns_the_completion_dict_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.llm_bridge_transport as transport

    fake = _FakeLambdaClient(
        _payload_response({"ok": True, "completion": {"text": "hi", "model": "m", "prompt_tokens": 1, "completion_tokens": 2}})
    )
    monkeypatch.setattr(transport, "_client", lambda *_: fake)

    result = invoke_bridge(
        "alibi-llm-bridge-dev",
        "text2sql",
        EndpointConfig(model_id="d"),
        [{"role": "user", "content": "q"}],
        unavailable_cls=_Unavailable,
        temperature=0.0,
    )

    assert result == {"text": "hi", "model": "m", "prompt_tokens": 1, "completion_tokens": 2}
    call = fake.calls[0]
    assert call["FunctionName"] == "alibi-llm-bridge-dev"
    assert call["InvocationType"] == "RequestResponse"
    sent = json.loads(call["Payload"])
    assert sent["kind"] == "text2sql"
    # The whole config is a model id plus budgets: no key ever crosses the wire.
    assert sent["config"] == {"model_id": "d", "region": "us-east-1", "timeout_seconds": 60.0}
    assert sent["temperature"] == 0.0


def test_invoke_raises_on_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.llm_bridge_transport as transport

    fake = _FakeLambdaClient(_payload_response({"ok": False, "error": "no model configured"}))
    monkeypatch.setattr(transport, "_client", lambda *_: fake)

    with pytest.raises(_Unavailable, match="no model configured"):
        invoke_bridge(
            "fn", "text2sql", EndpointConfig(model_id="d"), [], unavailable_cls=_Unavailable
        )


def test_invoke_raises_on_function_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.llm_bridge_transport as transport

    fake = _FakeLambdaClient(_payload_response({"errorMessage": "boom"}, function_error=True))
    monkeypatch.setattr(transport, "_client", lambda *_: fake)

    with pytest.raises(_Unavailable, match="bridge function crashed"):
        invoke_bridge(
            "fn", "text2sql", EndpointConfig(model_id="d"), [], unavailable_cls=_Unavailable
        )


def test_invoke_raises_when_invoke_itself_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.llm_bridge_transport as transport

    class _Broken:
        def invoke(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("no route to host")

    monkeypatch.setattr(transport, "_client", lambda *_: _Broken())

    with pytest.raises(_Unavailable, match="bridge invoke failed"):
        invoke_bridge(
            "fn", "text2sql", EndpointConfig(model_id="d"), [], unavailable_cls=_Unavailable
        )


# ---------------------------------------------------------------------------
# client-side routing: text2sql.client.complete / orchestrator.client.complete
# ---------------------------------------------------------------------------


def test_text2sql_complete_routes_through_the_bridge_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.llm_bridge_transport as transport

    monkeypatch.setenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", "alibi-llm-bridge-dev")
    get_settings.cache_clear()
    calls = []

    def fake_invoke(function_name, kind, config, messages, *, unavailable_cls, **extra):  # noqa: ANN001
        calls.append((function_name, kind, config, messages, extra))
        return {"text": "SELECT 1", "model": "qwen3", "prompt_tokens": 5, "completion_tokens": 6}

    monkeypatch.setattr(transport, "invoke", fake_invoke)
    try:
        result = text2sql_complete(
            EndpointConfig(model_id="qwen3"),
            [{"role": "user", "content": "q"}],
        )
    finally:
        monkeypatch.delenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", raising=False)
        get_settings.cache_clear()

    assert result == Text2sqlCompletion(text="SELECT 1", model="qwen3", prompt_tokens=5, completion_tokens=6)
    function_name, kind, _config, _messages, _extra = calls[0]
    assert function_name == "alibi-llm-bridge-dev"
    assert kind == "text2sql"


def test_text2sql_complete_still_requires_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bridge branch only matters after `config.is_configured` already passed."""
    monkeypatch.setenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", "alibi-llm-bridge-dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(Text2sqlUnavailable, match="no model configured"):
            text2sql_complete(EndpointConfig(model_id=""), [])
    finally:
        monkeypatch.delenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", raising=False)
        get_settings.cache_clear()


def test_orchestrator_complete_routes_through_the_bridge_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.llm_bridge_transport as transport

    monkeypatch.setenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", "alibi-llm-bridge-dev")
    get_settings.cache_clear()
    calls = []

    def fake_invoke(function_name, kind, config, messages, *, unavailable_cls, **extra):  # noqa: ANN001
        calls.append((function_name, kind, config, messages, extra))
        return {"text": '{"narrative": "ok"}', "model": "gpt-5.4-mini", "prompt_tokens": 3, "completion_tokens": 4}

    monkeypatch.setattr(transport, "invoke", fake_invoke)
    try:
        result = orchestrator_complete(
            LlmConfig(model_id="gpt-5.4-mini"),
            [{"role": "user", "content": "q"}],
        )
    finally:
        monkeypatch.delenv("ALIBI_LLM_BRIDGE_FUNCTION_NAME", raising=False)
        get_settings.cache_clear()

    assert result == OrchestratorCompletion(
        text='{"narrative": "ok"}', model="gpt-5.4-mini", prompt_tokens=3, completion_tokens=4
    )
    function_name, kind, _config, _messages, extra = calls[0]
    assert function_name == "alibi-llm-bridge-dev"
    assert kind == "orchestrator"
    assert extra["json_object"] is True


# ---------------------------------------------------------------------------
# app/llm_bridge.py — the handler on the other end
# ---------------------------------------------------------------------------


def test_bridge_handler_dispatches_text2sql(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.text2sql.client as text2sql_client_module

    monkeypatch.setattr(
        text2sql_client_module,
        "complete",
        lambda config, messages, **kwargs: Text2sqlCompletion(  # noqa: ARG005
            text="SELECT 1", model="qwen3", prompt_tokens=1, completion_tokens=2
        ),
    )

    result = bridge_handler(
        {
            "kind": "text2sql",
            "config": {"model_id": "qwen3"},
            "messages": [{"role": "user", "content": "q"}],
        }
    )

    assert result == {
        "ok": True,
        "completion": {"text": "SELECT 1", "model": "qwen3", "prompt_tokens": 1, "completion_tokens": 2},
    }


def test_bridge_handler_dispatches_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestrator.client as orchestrator_client_module

    monkeypatch.setattr(
        orchestrator_client_module,
        "complete",
        lambda config, messages, **kwargs: OrchestratorCompletion(  # noqa: ARG005
            text='{"narrative": "ok"}', model="gpt-5.4-mini", prompt_tokens=3, completion_tokens=4
        ),
    )

    result = bridge_handler(
        {
            "kind": "orchestrator",
            "config": {"model_id": "gpt-5.4-mini"},
            "messages": [{"role": "user", "content": "q"}],
        }
    )

    assert result["ok"] is True
    assert result["completion"]["text"] == '{"narrative": "ok"}'


def test_bridge_handler_rejects_unknown_kind() -> None:
    result = bridge_handler({"kind": "carrier_pigeon"})
    assert result == {"ok": False, "error": "unknown kind: 'carrier_pigeon'"}


def test_bridge_handler_reports_malformed_events_without_raising() -> None:
    result = bridge_handler({"kind": "text2sql", "config": {"unexpected_field": 1}, "messages": []})
    assert result["ok"] is False
    assert "error" in result


def test_bridge_handler_propagates_llm_unavailable_as_a_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.orchestrator.client as orchestrator_client_module

    def _boom(config, messages, **kwargs):  # noqa: ANN001, ARG001
        raise OrchestratorUnavailable("Timeout: model took too long")

    monkeypatch.setattr(orchestrator_client_module, "complete", _boom)

    result = bridge_handler(
        {
            "kind": "orchestrator",
            "config": {"model_id": "gpt-5.4-mini"},
            "messages": [],
        }
    )

    assert result == {"ok": False, "error": "LlmUnavailable: Timeout: model took too long"}
