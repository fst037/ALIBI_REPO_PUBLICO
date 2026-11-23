"""Thin chat client for the model that writes Module A's SQL.

Every call goes through the shared Bedrock transport (`app/core/bedrock.py`), so
the only thing that names a model is `EndpointConfig.model_id` — a Bedrock model
or cross-region inference-profile id. Swapping models is a config change, not a
code change.

ALIBI is AWS-only: authentication is the caller's
IAM role, so there is no key or endpoint URL here. In the deployed VPC topology
the call is relayed through the bridge Lambda (`app/llm_bridge.py`).
"""

from dataclasses import dataclass

from app.core import bedrock, usage_meter
from app.core.config import get_settings
from app.services.text2sql.formats import Message


class LlmUnavailable(Exception):
    """No model configured, or the model could not be reached."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class EndpointConfig:
    """Everything needed to call one model. Hashable so it can key a cache."""

    model_id: str
    region: str = "us-east-1"
    timeout_seconds: float = 60.0

    @property
    def is_configured(self) -> bool:
        return bool(self.model_id)


def complete(
    config: EndpointConfig,
    messages: list[Message],
    *,
    temperature: float = 0.0,
    max_tokens: int = 512,
    stop: list[str] | None = None,
) -> Completion:
    """One chat completion. `temperature=0` because SQL generation is not creative work.

    A non-zero temperature would make the same question produce different SQL on
    retry, which breaks both the prepare/execute cycle (the user confirms a
    specific query) and reproducible evaluation.
    """
    if not config.is_configured:
        raise LlmUnavailable("no model configured")

    bridge = get_settings().llm_bridge_function_name
    if bridge:
        # Deployed VPC topology: this process has no route to Bedrock, so the
        # call happens in `app/llm_bridge.py`, a second Lambda outside the VPC.
        # See `app/core/llm_bridge_transport.py`.
        from app.core.llm_bridge_transport import invoke as invoke_bridge

        result = invoke_bridge(
            bridge,
            "text2sql",
            config,
            messages,
            unavailable_cls=LlmUnavailable,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )
        return _metered(Completion(
            text=result["text"],
            model=result["model"],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
        ))

    try:
        reply = bedrock.converse(
            config.model_id,
            list(messages),  # type: ignore[arg-type]
            region=config.region,
            timeout_seconds=config.timeout_seconds,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
        )
    except bedrock.BedrockUnavailable as err:
        raise LlmUnavailable(str(err)) from err
    return _metered(Completion(
        text=reply.text,
        model=reply.model,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
    ))


def _metered(completion: Completion) -> Completion:
    """Report the call to the cost ledger's meter (a no-op outside one)."""
    usage_meter.record(completion.model, completion.prompt_tokens, completion.completion_tokens)
    return completion
