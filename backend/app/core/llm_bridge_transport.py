"""Caller-side transport for `app/llm_bridge.py`.

Used by `text2sql/client.py` and `orchestrator/client.py` when
`ALIBI_LLM_BRIDGE_FUNCTION_NAME` is set — the deployed topology where this
Lambda is VPC-attached (for RDS) and has no route to Bedrock, so the actual
model call happens in a second, non-VPC Lambda instead.

One function, parameterized by the caller's own `LlmUnavailable` class: the two
clients already have separate exceptions of that name, and every failure mode
here — a bad invoke, a crashed bridge, a rejected model call — means the same
thing to both callers ("this attempt produced nothing"), so it collapses into
whichever one the caller passes in rather than introducing a third.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from functools import lru_cache
from typing import Any


#: Seconds of slack above the model call's own timeout: the bridge's cold start
#: and its own invoke overhead come on top of the Bedrock call it makes.
_READ_SLACK_SECONDS = 10.0


@lru_cache(maxsize=8)
def _client(read_timeout: float):  # noqa: ANN202
    import boto3
    from botocore.config import Config

    # No retry: a retried invoke would repeat a model call that may well have
    # succeeded on the far side, and the bridge already retries Bedrock once.
    return boto3.client(
        "lambda",
        config=Config(connect_timeout=5, read_timeout=read_timeout, retries={"max_attempts": 1}),
    )


def _read_timeout(config: Any) -> float:
    """A little above the call's own timeout, so a slow model surfaces as the
    bridge's clean `ok: false`, not a client-side read timeout saying nothing.

    Derived per call rather than fixed: the synchronous path's calls are bounded
    by API Gateway's 29 s, while the agent harness's synthesis runs inside a
    15-minute worker and may legitimately take a minute or more.
    """
    timeout = getattr(config, "timeout_seconds", None)
    if timeout is None and isinstance(config, dict):
        timeout = config.get("timeout_seconds")
    return float(timeout or 20.0)


def invoke(
    function_name: str,
    kind: str,
    config: Any,
    messages: list[dict[str, str]],
    *,
    unavailable_cls: type[Exception],
    **extra: Any,
) -> dict[str, Any]:
    """Call the bridge Lambda and return its `completion` dict, or raise `unavailable_cls`."""
    payload = {
        "kind": kind,
        "config": asdict(config) if is_dataclass(config) else dict(config),
        "messages": messages,
        **extra,
    }
    body = call(function_name, payload, read_timeout=_read_timeout(config), unavailable_cls=unavailable_cls)
    if not body.get("ok"):
        raise unavailable_cls(body.get("error") or "bridge returned an error with no detail")

    return body["completion"]


def call(
    function_name: str,
    payload: dict[str, Any],
    *,
    read_timeout: float,
    unavailable_cls: type[Exception],
) -> dict[str, Any]:
    """Invoke the bridge and return its whole body, `ok: false` included.

    For callers that read more than "it failed" out of a refusal — the SageMaker
    transport has to tell a model that is asleep from one that is broken.
    Transport-level failures (the invoke, a crash, garbage back) still raise.
    """
    try:
        response = _client(read_timeout + _READ_SLACK_SECONDS).invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
    except Exception as err:  # noqa: BLE001 - boto3/botocore raise a wide family
        raise unavailable_cls(f"bridge invoke failed: {type(err).__name__}: {err}") from err

    raw = response["Payload"].read()
    if response.get("FunctionError"):
        raise unavailable_cls(f"bridge function crashed: {raw.decode(errors='replace')[:500]}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise unavailable_cls(f"bridge returned unparseable output: {raw[:500]!r}") from err


__all__ = ["call", "invoke"]
