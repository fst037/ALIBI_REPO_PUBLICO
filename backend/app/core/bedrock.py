"""The one transport every model call in ALIBI goes through: AWS Bedrock.

ALIBI is AWS-only. The orchestrator's narrative
and prompted chart, Module A's Bedrock path and Module B's schema
inference all reach their model through `converse()` here, and Module B's
column embeddings through `embed()`. Authentication is the caller's IAM role —
there is no API key anywhere, in code, in `.env` or in a Lambda environment.

Two Bedrock APIs, each used for what it is for:

* **Converse** for chat. It is model-agnostic — the same request shape reaches
  Claude, Nova or gpt-oss — so a model change is a model id in configuration,
  never a code change. Callers keep handing over OpenAI-style message lists
  (`[{"role": "system" | "user" | "assistant", "content": str}]`); the
  translation to Converse's shape lives here, once.
* **InvokeModel** for embeddings, because Converse does not do embeddings.
  Titan Text Embeddings V2 natively emits 256/512/1024 dimensions, so the
  pgvector column stays at 512 with no migration.

Converse has no JSON mode. Callers that need a JSON object already say so in the
prompt and parse with `orchestrator.client.extract_json`, which tolerates a
fenced or prefaced object; that tolerance is what replaces the old
`response_format`.

`boto3` is imported lazily and the client cached per (region, timeout): in Lambda
the first request pays for construction and warm invocations reuse the pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


class BedrockUnavailable(Exception):
    """The call produced nothing usable: not configured, throttled, denied, timed out."""


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@lru_cache(maxsize=8)
def _runtime(region: str, timeout_seconds: float):  # noqa: ANN202 - boto3 type
    try:
        import boto3
        from botocore.config import Config
    except ImportError as err:  # pragma: no cover - boto3 is declared
        raise BedrockUnavailable("boto3 is not installed") from err
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=5,
            read_timeout=timeout_seconds,
            # One retry, not botocore's default of several. The orchestrator's own
            # retry carries a *critique* and is the one worth the time; repeating
            # an identical prompt only rerolls the dice, and every second here
            # comes out of the caller's budget.
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def to_converse(messages: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """OpenAI-style messages -> (Converse `system`, Converse `messages`).

    System turns become the separate `system` blocks Converse expects. Consecutive
    turns from the same role are merged, because Converse requires the roles to
    alternate, and it requires the conversation to start with a user turn.
    """
    system: list[dict[str, str]] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        role, text = message.get("role"), message.get("content") or ""
        if role == "system":
            if text:
                system.append({"text": text})
            continue
        if role not in ("user", "assistant"):
            raise BedrockUnavailable(f"unsupported message role {role!r}")
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].append({"text": text})
        else:
            turns.append({"role": role, "content": [{"text": text}]})
    if not turns or turns[0]["role"] != "user":
        raise BedrockUnavailable("a conversation must start with a user message")
    return system, turns


def converse(
    model_id: str,
    messages: list[dict[str, str]],
    *,
    region: str,
    timeout_seconds: float,
    max_tokens: int,
    temperature: float,
    stop: list[str] | None = None,
) -> Reply:
    """One chat turn. Every failure becomes `BedrockUnavailable`.

    Collapsing the botocore error family into one exception is deliberate: no
    caller acts differently on a throttle than on a timeout — both mean "this
    attempt produced nothing, take the next path".
    """
    if not model_id:
        raise BedrockUnavailable("no model configured")
    system, turns = to_converse(messages)
    inference: dict[str, Any] = {"maxTokens": max_tokens, "temperature": temperature}
    if stop:
        inference["stopSequences"] = stop
    request: dict[str, Any] = {"modelId": model_id, "messages": turns, "inferenceConfig": inference}
    if system:
        request["system"] = system
    try:
        response = _runtime(region, timeout_seconds).converse(**request)
    except BedrockUnavailable:
        raise
    except Exception as err:  # noqa: BLE001 - botocore raises a wide family
        raise BedrockUnavailable(f"{type(err).__name__}: {err}") from err

    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(block.get("text", "") for block in blocks)
    usage = response.get("usage") or {}
    return Reply(
        text=text,
        model=model_id,
        prompt_tokens=int(usage.get("inputTokens", 0)),
        completion_tokens=int(usage.get("outputTokens", 0)),
    )


def embed(
    model_id: str,
    texts: list[str],
    *,
    region: str,
    timeout_seconds: float,
    dimensions: int,
) -> list[list[float]]:
    """One vector per text, Titan Text Embeddings V2 request shape.

    Titan embeds one text per call; column lists are short (a table's columns),
    so a loop costs a few small calls, not a batch API's worth of complexity.
    """
    if not model_id:
        raise BedrockUnavailable("no embedding model configured")
    client = _runtime(region, timeout_seconds)
    vectors: list[list[float]] = []
    for text in texts:
        try:
            response = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"inputText": text, "dimensions": dimensions, "normalize": True}),
            )
            payload = json.loads(response["body"].read())
        except Exception as err:  # noqa: BLE001
            raise BedrockUnavailable(f"{type(err).__name__}: {err}") from err
        vectors.append([float(v) for v in payload["embedding"]])
    return vectors


__all__ = ["BedrockUnavailable", "Reply", "converse", "embed", "to_converse"]
