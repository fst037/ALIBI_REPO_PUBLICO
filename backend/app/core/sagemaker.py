"""The transport for ALIBI's fine-tuned models: one SageMaker real-time endpoint.

Module A (Text-to-SQL) and Module C (Vega-Lite) are two LoRA adapters on one
Qwen3.8-27B base, served together by `ML/src/model_a/inference.py`: a request
names its `task` (`sql` | `chart`) and the handler switches adapters. The
handler renders the *training* prompt itself (`ML/src/common/prompts.py`, the
module training used), so what crosses this wire is structured input — a schema
and a question, or a result profile — never a prompt. That is what keeps the
served prompt byte-for-byte the trained one.

**The endpoint scales to zero.** SageMaker allows that only for an endpoint that
hosts an *inference component*, so every invocation names one, and an endpoint
at zero copies refuses invocations until a copy is back (several minutes: an
instance, then the base model download and 4-bit load). `ensure_awake` is the
one place that deals with it: it asks for a copy (`UpdateInferenceComponent
RuntimeConfig`, the same "start it if it is stopped" move `app/db/waker.py` makes
for RDS) and waits until the model actually answers. Callers that skip it get
`ModelAsleep` and decide for themselves.

Whether the model is asleep is read from the inference component's own copy
count, not from the invocation's error text: the wording of that error is not
documented, and a transport that matched on it would break silently.

In the deployed topology the API and the run worker are VPC-attached with no
route to SageMaker, so every call is relayed through the bridge Lambda
(`app/llm_bridge.py`, kind `sagemaker`) exactly as Bedrock calls are.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from app.core.config import Settings, get_settings

logger = logging.getLogger("alibi.sagemaker")

# The waiting loop's clock, as names of this module so a test can drive it
# without freezing `time` for every other thread in the process.
_now = time.monotonic
_sleep = time.sleep


class SageMakerUnavailable(Exception):
    """The call produced nothing usable: not configured, failed, timed out."""


class ModelAsleep(SageMakerUnavailable):
    """The endpoint is scaled to zero (or still loading after a wake)."""


class ModelNoCapacity(SageMakerUnavailable):
    """A copy was requested but SageMaker cannot get an instance for it.

    Not a slow start: the endpoint keeps retrying and never gets a GPU
    (`ml.g6e.xlarge` capacity, 2026-10-13). Waiting longer does not help, so the
    caller is told instead of left hanging for the whole wake budget.
    """


@dataclass(frozen=True)
class Target:
    """Everything needed to reach the endpoint. Hashable so it can key a cache."""

    endpoint_name: str
    #: Empty for a classic endpoint (always on); set when it scales to zero.
    inference_component: str = ""
    region: str = "us-east-1"
    timeout_seconds: float = 65.0


def target_from_settings(settings: Settings | None = None) -> Target | None:
    settings = settings or get_settings()
    if not settings.sagemaker_endpoint_name:
        return None
    return Target(
        endpoint_name=settings.sagemaker_endpoint_name,
        inference_component=settings.sagemaker_inference_component or "",
        region=settings.aws_region,
        timeout_seconds=settings.sagemaker_timeout_seconds,
    )


# ── Public calls (relayed through the bridge when one is configured) ──────────


def invoke(target: Target, payload: dict[str, Any]) -> dict[str, Any]:
    """One request to the endpoint; the handler's JSON reply.

    Raises `ModelAsleep` when the endpoint has no copy to serve it, and
    `SageMakerUnavailable` for everything else — including a reply that carries
    an `error` (an adapter the endpoint does not serve, an unknown task).
    """
    return _dispatch("invoke", target, payload)


def wake(target: Target) -> str:
    """Ask for one copy of the model if there is none. `"ready"` or `"waking"`.

    Harmless to repeat: it only changes the copy count when it is 0.
    """
    return wake_status(target)[0]


def wake_status(target: Target) -> tuple[str, int | None]:
    """`wake`, plus how many instances the endpoint has (None when unknown).

    The instance count is what tells a slow start (an instance is up, the model
    is loading) from a stuck one (no instance is coming).
    """
    reply = _dispatch("wake", target, None)
    return reply["state"], reply.get("instances")


def ensure_awake(
    target: Target,
    *,
    wait_seconds: float,
    on_waking: Callable[[], None] | None = None,
    poll_seconds: float = 20.0,
    no_instance_seconds: float = 360.0,
) -> None:
    """Return once the model answers; wake it and wait if it is asleep.

    `on_waking` is called once, the first time the model turns out not to be
    ready — the run uses it to show a "starting the model" step, so a cold start
    reads as progress rather than as a hang. Raises `ModelAsleep` if the model is
    still not answering after `wait_seconds`, and `SageMakerUnavailable` if the
    endpoint fails in a way waiting will not fix. Raises `ModelNoCapacity` when
    the endpoint still has no instance `no_instance_seconds` after the wake: an
    instance normally arrives in a couple of minutes, so past that it is not
    coming and the rest of the wait would be a hang.
    """
    started = _now()
    deadline = started + wait_seconds
    announced = False
    while True:
        instances: int | None = None
        if target.inference_component:
            state, instances = wake_status(target)
        else:
            state = "ready"
        if state == "ready":
            try:
                _probe(target)
                return
            except ModelAsleep:
                pass
            except SageMakerUnavailable as err:
                # A copy that is up but whose handler is still loading the base
                # answers with errors for a few minutes — possibly woken by
                # someone else's run a moment ago. Worth waiting out when the
                # endpoint scales; an always-on endpoint failing is just failing.
                if not target.inference_component:
                    raise
                logger.info("model copy is up but not answering yet: %s", err)
        if instances == 0 and _now() - started >= no_instance_seconds:
            raise ModelNoCapacity(
                f"{target.endpoint_name} has no instance {no_instance_seconds:.0f}s after the wake"
            )
        if not announced:
            announced = True
            logger.info("model %s is asleep; waking it", target.endpoint_name)
            if on_waking is not None:
                on_waking()
        if _now() + poll_seconds > deadline:
            raise ModelAsleep(
                f"{target.endpoint_name} did not come up within {wait_seconds:.0f}s"
            )
        _sleep(poll_seconds)


def _probe(target: Target) -> None:
    """Cheapest proof that the handler has the model loaded.

    `ping` answers without generating. A handler that predates the `ping` task
    answers `unknown task` — which equally proves `predict_fn` ran, so that
    counts as ready too.
    """
    try:
        invoke(target, {"task": "ping"})
    except SageMakerUnavailable as err:
        if "unknown task" in str(err):
            return
        raise


# ── The relay ─────────────────────────────────────────────────────────────────


def _dispatch(op: str, target: Target, payload: dict[str, Any] | None) -> dict[str, Any]:
    bridge = get_settings().llm_bridge_function_name
    if not bridge:
        return run_op(op, target, payload)

    from app.core.llm_bridge_transport import call

    body = call(
        bridge,
        {"kind": "sagemaker", "op": op, "target": asdict(target), "payload": payload},
        read_timeout=target.timeout_seconds,
        unavailable_cls=SageMakerUnavailable,
    )
    if body.get("ok"):
        return body["completion"]
    error = body.get("error") or "bridge returned an error with no detail"
    if body.get("code") == "model_asleep":
        raise ModelAsleep(error)
    raise SageMakerUnavailable(error)


def run_op(op: str, target: Target, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Do the call in this process. The bridge's handler calls this too."""
    if op == "invoke":
        return _invoke_direct(target, payload or {})
    if op == "wake":
        state = _wake_direct(target)
        return {"state": state, "instances": _current_instances(target)}
    raise SageMakerUnavailable(f"unknown sagemaker op {op!r}")


@lru_cache(maxsize=8)
def _runtime(region: str, timeout_seconds: float):  # noqa: ANN202 - boto3 type
    import boto3
    from botocore.config import Config

    # No retry: a generation that timed out on the far side would be repeated
    # in full, and the 60 s SageMaker ceiling makes a retry hopeless anyway.
    return boto3.client(
        "sagemaker-runtime",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=timeout_seconds, retries={"max_attempts": 1}),
    )


@lru_cache(maxsize=4)
def _control(region: str):  # noqa: ANN202 - boto3 type
    import boto3
    from botocore.config import Config

    return boto3.client(
        "sagemaker",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"}),
    )


def _invoke_direct(target: Target, payload: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "EndpointName": target.endpoint_name,
        "ContentType": "application/json",
        "Accept": "application/json",
        "Body": json.dumps(payload, default=str).encode(),
    }
    if target.inference_component:
        kwargs["InferenceComponentName"] = target.inference_component
    try:
        response = _runtime(target.region, target.timeout_seconds).invoke_endpoint(**kwargs)
        body = json.loads(response["Body"].read())
    except Exception as err:  # noqa: BLE001 - botocore raises a wide family
        if target.inference_component and _current_copies(target) == 0:
            raise ModelAsleep(f"{target.inference_component} has no running copy") from err
        raise SageMakerUnavailable(f"{type(err).__name__}: {err}") from err
    if not isinstance(body, dict):
        raise SageMakerUnavailable(f"unexpected reply: {str(body)[:200]}")
    if body.get("error") and body.get("task") != "chart":
        # A chart reply carries `error` for a spec that was not valid JSON — that
        # is the model's answer, judged by the caller. Anything else with an
        # `error` is the handler refusing the request.
        raise SageMakerUnavailable(str(body["error"]))
    return body


def _describe(target: Target) -> dict[str, Any]:
    return _control(target.region).describe_inference_component(
        InferenceComponentName=target.inference_component
    )


def _current_copies(target: Target) -> int | None:
    try:
        return int(_describe(target).get("RuntimeConfig", {}).get("CurrentCopyCount", 0))
    except Exception:  # noqa: BLE001 - unknown is unknown; the caller reports the original error
        logger.exception("could not describe inference component %s", target.inference_component)
        return None


def _current_instances(target: Target) -> int | None:
    """Instances the endpoint's variant has right now; None when it cannot be read."""
    try:
        described = _control(target.region).describe_endpoint(EndpointName=target.endpoint_name)
        return int(described["ProductionVariants"][0].get("CurrentInstanceCount", 0))
    except Exception:  # noqa: BLE001 - a hint for the waiting loop, never worth failing a wake
        logger.warning("could not read the instance count of %s", target.endpoint_name)
        return None


def _wake_direct(target: Target) -> str:
    if not target.inference_component:
        return "ready"
    try:
        described = _describe(target)
    except Exception as err:  # noqa: BLE001
        raise SageMakerUnavailable(f"describe failed: {type(err).__name__}: {err}") from err
    runtime = described.get("RuntimeConfig", {})
    if int(runtime.get("CurrentCopyCount", 0)) >= 1:
        # A copy that exists while the component is still Creating/Updating is
        # not answering yet; probing it only adds invocations (which also keep
        # the idle clock from running) without learning anything.
        if described.get("InferenceComponentStatus", "InService") != "InService":
            return "waking"
        return "ready"
    if int(runtime.get("DesiredCopyCount", 0)) < 1:
        try:
            _control(target.region).update_inference_component_runtime_config(
                InferenceComponentName=target.inference_component,
                DesiredRuntimeConfig={"CopyCount": 1},
            )
            logger.info("asked %s for one copy", target.inference_component)
        except Exception as err:  # noqa: BLE001
            # Most often "is being updated": a scale-out is already under way,
            # which is what this call wanted anyway.
            logger.warning("wake of %s not accepted: %s", target.inference_component, err)
    return "waking"


__all__ = [
    "ModelAsleep",
    "ModelNoCapacity",
    "SageMakerUnavailable",
    "Target",
    "ensure_awake",
    "invoke",
    "run_op",
    "target_from_settings",
    "wake",
    "wake_status",
]
