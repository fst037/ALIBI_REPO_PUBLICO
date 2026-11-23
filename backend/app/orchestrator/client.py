"""The orchestrator's LLM seam — one chat call against AWS Bedrock.

Every orchestrator node (the `explain_result` narrative, Module C's prompted
chart) reaches its model through `complete()` here, which hands the call to the
shared transport in `app/core/bedrock.py`. ALIBI is AWS-only: authentication is the Lambda's IAM role, so this config carries a
model id and budgets, and no endpoint or key.

Module A still has its own client next door (`app/services/text2sql/client.py`)
and that is not duplication: it is pinned to the SQL model and to temperature 0,
and a failure there means something different (no query to run) from a failure
here (no sentence to show). Both share the one transport.

In the deployed VPC topology the call is relayed through the bridge Lambda
(`app/llm_bridge.py`), because the VPC-attached API Lambda has no route to
Bedrock; callers never see the difference.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core import bedrock, usage_meter
from app.core.config import Settings, get_settings

logger = logging.getLogger("alibi.orchestrator.client")

Message = dict[str, str]


class LlmUnavailable(Exception):
    """No model configured, or the model could not be reached."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class LlmConfig:
    """Everything one orchestrator call needs. Frozen so it can key a cache."""

    #: A Bedrock model or cross-region inference-profile id, e.g.
    #: "us.anthropic.claude-haiku-4-5-20251001-v1:0".
    model_id: str
    region: str = "us-east-1"
    timeout_seconds: float = 15.0
    #: Not zero. SQL generation is not creative work and pins to 0, but prose at
    #: temperature 0 collapses into the same three sentence skeletons for every
    #: result — and the grounding validator, not the sampler, is what keeps the
    #: numbers honest here.
    temperature: float = 0.2

    @property
    def is_configured(self) -> bool:
        return bool(self.model_id)


def config_from_settings(settings: Settings | None = None) -> LlmConfig:
    settings = settings or get_settings()
    return LlmConfig(
        model_id=settings.orchestrator_model_id or "",
        region=settings.aws_region,
        timeout_seconds=settings.orchestrator_timeout_seconds,
        temperature=settings.orchestrator_temperature,
    )


def is_configured(settings: Settings | None = None) -> bool:
    return config_from_settings(settings).is_configured


def planner_config(settings: Settings | None = None) -> LlmConfig:
    """The agent harness's planner: small, fast, called once per turn.

    Temperature 0: the planner chooses actions, and the same state should lead to
    the same choice — variety is a property wanted in prose, not in a plan.
    """
    settings = settings or get_settings()
    return LlmConfig(
        model_id=settings.planner_model_id or "",
        region=settings.aws_region,
        timeout_seconds=settings.planner_timeout_seconds,
        temperature=0.0,
    )


def synthesis_config(deep: bool, settings: Settings | None = None) -> LlmConfig:
    """The model that writes the final answer — a bigger one for Deep."""
    settings = settings or get_settings()
    model_id = settings.synthesis_deep_model_id if deep else settings.synthesis_quick_model_id
    return LlmConfig(
        model_id=model_id or "",
        region=settings.aws_region,
        timeout_seconds=settings.synthesis_timeout_seconds,
        temperature=settings.orchestrator_temperature,
    )


def complete(
    config: LlmConfig,
    messages: list[Message],
    *,
    max_tokens: int = 400,
    json_object: bool = True,
) -> Completion:
    """One chat completion. Raises `LlmUnavailable` for anything that went wrong.

    `json_object` is kept for the callers' sake and forwarded to the bridge, but
    Bedrock's Converse has no JSON mode: the prompts ask for JSON and
    `extract_json` tolerates a fenced or prefaced object.

    Collapsing every failure into one exception is intentional: no caller here
    can act differently on a rate limit than on a timeout — both mean "this
    attempt produced nothing, take the next path".
    """
    if not config.is_configured:
        raise LlmUnavailable("no orchestrator model configured")

    bridge = get_settings().llm_bridge_function_name
    if bridge:
        # Deployed VPC topology: this process has no route to Bedrock, so the
        # call happens in `app/llm_bridge.py`, a second Lambda outside the VPC.
        # See `app/core/llm_bridge_transport.py`.
        from app.core.llm_bridge_transport import invoke as invoke_bridge

        result = invoke_bridge(
            bridge,
            "orchestrator",
            config,
            messages,
            unavailable_cls=LlmUnavailable,
            max_tokens=max_tokens,
            json_object=json_object,
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
            messages,
            region=config.region,
            timeout_seconds=config.timeout_seconds,
            max_tokens=max_tokens,
            temperature=config.temperature,
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


def extract_json(text: str) -> dict[str, Any]:
    """The JSON object in a completion, whether or not it came back clean.

    There is no JSON mode on Bedrock's Converse, so models sometimes wrap the
    object in a ```json fence or a line of preamble. Slicing to the outermost braces recovers all of
    those; a genuinely malformed body raises, and every caller turns that into a
    retry.

    Lives beside the transport rather than in a node because it is about what a
    completion literally contains, and both nodes that parse one need the same
    tolerance. It used to be `explain_result._extract_json`; Module C's node
    would otherwise have carried a second copy that could be fixed once and
    stay broken in the other.
    """
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the completion")
    return json.loads(stripped[start : start + (end - start + 1)])


__all__ = [
    "Completion",
    "LlmConfig",
    "LlmUnavailable",
    "complete",
    "config_from_settings",
    "extract_json",
    "is_configured",
    "planner_config",
    "synthesis_config",
]
