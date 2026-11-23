"""The shared Bedrock transport (`app/core/bedrock.py`) and the three clients on it.

No test here reaches AWS: `bedrock._runtime` is replaced with a fake boto3
client that records the request it was given, which is the thing worth
asserting — the shape Converse receives is exactly what used to be the Azure
SDK's job.
"""

from __future__ import annotations

import io
import json

import pytest

from app.core import bedrock
from app.core.config import Settings, SchemaInferenceProvider

# Captured at import, before conftest's autouse stub replaces the module
# attribute for every test: this is the real function, transport and all.
from app.orchestrator.client import LlmConfig as _LlmConfig  # noqa: E402
from app.orchestrator.client import LlmUnavailable as _OrchestratorUnavailable  # noqa: E402
from app.orchestrator.client import complete as _real_orchestrator_complete  # noqa: E402


class _FakeRuntime:
    def __init__(self, text: str = "ok", raise_with: Exception | None = None) -> None:
        self.converse_calls: list[dict] = []
        self.invoke_calls: list[dict] = []
        self._text = text
        self._raise = raise_with

    def converse(self, **request):  # noqa: ANN003
        self.converse_calls.append(request)
        if self._raise:
            raise self._raise
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self._text}]}},
            "usage": {"inputTokens": 11, "outputTokens": 7},
        }

    def invoke_model(self, **request):  # noqa: ANN003
        self.invoke_calls.append(request)
        dims = json.loads(request["body"])["dimensions"]
        return {"body": io.BytesIO(json.dumps({"embedding": [0.5] * dims}).encode())}


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> _FakeRuntime:
    fake = _FakeRuntime()
    monkeypatch.setattr(bedrock, "_runtime", lambda *a, **k: fake)  # noqa: ARG005
    return fake


# ---------------------------------------------------------------------------
# message translation
# ---------------------------------------------------------------------------


def test_system_turns_become_converse_system_blocks() -> None:
    system, turns = bedrock.to_converse(
        [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q"}]
    )
    assert system == [{"text": "be brief"}]
    assert turns == [{"role": "user", "content": [{"text": "q"}]}]


def test_consecutive_turns_of_one_role_are_merged_because_converse_requires_alternation() -> None:
    _, turns = bedrock.to_converse(
        [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}]
    )
    assert turns == [
        {"role": "user", "content": [{"text": "a"}, {"text": "b"}]},
        {"role": "assistant", "content": [{"text": "c"}]},
    ]


def test_a_conversation_that_does_not_start_with_the_user_is_refused() -> None:
    with pytest.raises(bedrock.BedrockUnavailable, match="start with a user"):
        bedrock.to_converse([{"role": "assistant", "content": "hi"}])


# ---------------------------------------------------------------------------
# converse / embed
# ---------------------------------------------------------------------------


def test_converse_sends_model_budgets_and_stop_sequences(runtime: _FakeRuntime) -> None:
    reply = bedrock.converse(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
        region="us-east-1",
        timeout_seconds=5,
        max_tokens=42,
        temperature=0.0,
        stop=[";"],
    )
    request = runtime.converse_calls[0]
    assert request["modelId"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert request["inferenceConfig"] == {"maxTokens": 42, "temperature": 0.0, "stopSequences": [";"]}
    assert request["system"] == [{"text": "s"}]
    assert reply == bedrock.Reply(text="ok", model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                                  prompt_tokens=11, completion_tokens=7)


def test_every_provider_error_becomes_bedrock_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRuntime(raise_with=RuntimeError("ThrottlingException: slow down"))
    monkeypatch.setattr(bedrock, "_runtime", lambda *a, **k: fake)  # noqa: ARG005
    with pytest.raises(bedrock.BedrockUnavailable, match="slow down"):
        bedrock.converse("m", [{"role": "user", "content": "q"}], region="r", timeout_seconds=1,
                         max_tokens=1, temperature=0)


def test_no_model_id_is_refused_before_any_call(runtime: _FakeRuntime) -> None:
    with pytest.raises(bedrock.BedrockUnavailable, match="no model"):
        bedrock.converse("", [{"role": "user", "content": "q"}], region="r", timeout_seconds=1,
                         max_tokens=1, temperature=0)
    assert runtime.converse_calls == []


def test_embed_asks_titan_for_the_pgvector_width(runtime: _FakeRuntime) -> None:
    vectors = bedrock.embed("amazon.titan-embed-text-v2:0", ["a", "b"], region="r", timeout_seconds=1, dimensions=512)
    assert [len(v) for v in vectors] == [512, 512]
    assert json.loads(runtime.invoke_calls[0]["body"]) == {"inputText": "a", "dimensions": 512, "normalize": True}


# ---------------------------------------------------------------------------
# the clients on top
# ---------------------------------------------------------------------------


def test_text2sql_direct_call_reaches_bedrock_at_temperature_zero(runtime: _FakeRuntime) -> None:
    from app.services.text2sql.client import EndpointConfig, complete

    result = complete(EndpointConfig(model_id="m"), [{"role": "user", "content": "q"}], max_tokens=42)
    assert runtime.converse_calls[0]["inferenceConfig"]["temperature"] == 0.0
    assert runtime.converse_calls[0]["inferenceConfig"]["maxTokens"] == 42
    assert result.text == "ok"


def test_orchestrator_direct_call_maps_bedrock_failure_to_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRuntime(raise_with=RuntimeError("AccessDeniedException"))
    monkeypatch.setattr(bedrock, "_runtime", lambda *a, **k: fake)  # noqa: ARG005
    with pytest.raises(_OrchestratorUnavailable, match="AccessDenied"):
        _real_orchestrator_complete(_LlmConfig(model_id="m"), [{"role": "user", "content": "q"}])
    assert fake.converse_calls[0]["inferenceConfig"]["temperature"] == 0.2


def test_module_b_provider_infers_through_converse_and_embeds_through_titan(runtime: _FakeRuntime) -> None:
    from app.ingest import inference

    runtime._text = '{"table_description": "Sales", "columns": []}'
    settings = Settings(
        _env_file=None,
        schema_inference_provider=SchemaInferenceProvider.bedrock,
        schema_inference_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        embedding_model_id="amazon.titan-embed-text-v2:0",
    )
    provider = inference.get_provider(settings)
    assert isinstance(provider, inference.BedrockProvider)
    assert provider.model_id == "bedrock:amazon.titan-embed-text-v2:0"
    assert len(provider.embed(["x"])[0]) == settings.embedding_dimensions
    assert provider._chat("sys", "user") == {"table_description": "Sales", "columns": []}
    assert runtime.converse_calls[0]["inferenceConfig"]["temperature"] == 0.0
