"""Lambda entry point for the model-call bridge (deployed topology only).

The main API Lambda is VPC-attached to reach RDS Postgres, and with no NAT
gateway or VPC interface endpoint a VPC Lambda has no route to AWS Bedrock. This
function is the other half of that split: it carries no database access and no
VPC config, so it keeps the normal AWS-API egress every Lambda gets by default,
and its only job is making the one Bedrock call Module A (Text-to-SQL) or the
orchestrator needs. It costs nothing while idle, which is why it is preferred
over interface endpoints (~$7/month each).

Same shape as `app/ingest/lambda_handler.py`'s stage-1/stage-2 split, applied
to the model call instead of the file parse: one concern moves outside the
VPC, the caller invokes it synchronously (`InvocationType="RequestResponse"`,
because a `prepare`/`execute` request needs the answer before it can respond),
and nothing here knows *why* it's being asked — that's still decided entirely by
`text2sql/client.py` and `orchestrator/client.py`, whose `complete()` this reuses
verbatim. Deployed from the same zip as the main API Lambda (`code/backend.zip`,
see `backend/terraform/modules/api-lambda`) with a different `handler` — one
build, two functions, no drift.

Every failure is caught and returned as `{"ok": false, "error": ...}` rather
than raised: `app/core/llm_bridge_transport.py` distinguishes a clean bridge
response from an infrastructure-level `FunctionError`, and a Python exception
escaping this handler would show up as the latter, saying less about what
went wrong.

There are no credentials to hold: the bridge's own IAM role is what Bedrock
authorises (`bedrock:InvokeModel`, scoped in Terraform). `EndpointConfig` /
`LlmConfig` — model id, region, budgets — are rebuilt entirely from the invoke
`event`, sent fresh by the caller, so which model answers is decided in exactly
one place: the API Lambda's settings.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _text2sql_completion(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.text2sql.client import EndpointConfig, complete

    config = EndpointConfig(**payload["config"])
    completion = complete(
        config,
        payload["messages"],
        temperature=payload.get("temperature", 0.0),
        max_tokens=payload.get("max_tokens", 512),
        stop=payload.get("stop"),
    )
    return asdict(completion)


def _orchestrator_completion(payload: dict[str, Any]) -> dict[str, Any]:
    from app.orchestrator.client import LlmConfig, complete

    config = LlmConfig(**payload["config"])
    completion = complete(
        config,
        payload["messages"],
        max_tokens=payload.get("max_tokens", 400),
        json_object=payload.get("json_object", True),
    )
    return asdict(completion)


def _sagemaker_call(payload: dict[str, Any]) -> dict[str, Any]:
    """The fine-tuned models' endpoint: invoke it, or wake it (`app/core/sagemaker.py`)."""
    from app.core.sagemaker import Target, run_op

    return run_op(payload["op"], Target(**payload["target"]), payload.get("payload"))


def _mail(payload: dict[str, Any]) -> dict[str, Any]:
    # Not a model call, same reason to be here: the API Lambda has no route out
    # of the VPC, so it cannot reach the SMTP server either. The SMTP settings
    # live in this function's environment, not in the payload.
    from app.core.mailer import send_smtp

    send_smtp(payload["to"], payload["code"], payload["minutes"])
    return {}


def _google(payload: dict[str, Any]) -> dict[str, Any]:
    # Same reason again: checking a "Sign in with Google" token needs Google's
    # public keys, which the API Lambda cannot fetch from the VPC. The audience
    # comes in the payload, so the client id is configured in one place only.
    from app.core.google_identity import check

    return check(payload["credential"], payload["audience"])


def _mercadopago(payload: dict[str, Any]) -> dict[str, Any]:
    # Same reason again: api.mercadopago.com is out of the VPC's reach. The token
    # comes in the payload, so it is configured in one place only (the API's env).
    from app.core.mercadopago import run_op

    return run_op(payload["op"], payload["token"], payload["payload"])


_HANDLERS = {
    "text2sql": _text2sql_completion,
    "orchestrator": _orchestrator_completion,
    "sagemaker": _sagemaker_call,
    "mail": _mail,
    "google": _google,
    "mercadopago": _mercadopago,
}


def bridge_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    kind = event.get("kind")
    handler = _HANDLERS.get(kind)  # type: ignore[arg-type]
    if handler is None:
        return {"ok": False, "error": f"unknown kind: {kind!r}"}

    from app.core.google_identity import InvalidGoogleToken
    from app.core.mercadopago import MercadoPagoRejected
    from app.core.sagemaker import ModelAsleep

    try:
        completion = handler(event)
    except ModelAsleep as exc:
        # Carried as a code, not just text: the caller wakes the model on it,
        # where any other failure is final.
        return {"ok": False, "error": str(exc), "code": "model_asleep"}
    except InvalidGoogleToken as exc:
        # Also a code: a rejected token is a 401 for the user, not an outage.
        return {"ok": False, "error": str(exc), "code": "invalid_google_token"}
    except MercadoPagoRejected as exc:
        return {"ok": False, "error": str(exc), "code": "mercadopago_rejected", "status": exc.status}
    except Exception as exc:  # noqa: BLE001 - every path here means "no completion"
        # `text2sql.client.complete`/`orchestrator.client.complete` already
        # collapse SDK errors into their own `LlmUnavailable`; this is the net
        # under that, for a malformed event (a bad `config`/missing `messages`)
        # rather than a model-call failure.
        logger.warning("llm bridge call failed (kind=%s): %s", kind, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": True, "completion": completion}


__all__ = ["bridge_handler"]
