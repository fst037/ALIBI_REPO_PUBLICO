"""Turn "the database is unreachable" into "the database is waking up".

The RDS instance stops itself after an idle hour (terraform/modules/
rds-autostop). The first request after that fails to connect; this handler
asks the autostop Lambda to start it — through the Lambda interface endpoint
this VPC-attached function already uses for the LLM bridge — and answers 503
`db_waking` so the frontend can say so and retry, instead of a generic error.

A start takes ~5-10 minutes. The frontend polls `GET /health/db` meanwhile,
and every failed poll lands here again; the autostop Lambda only starts an
instance that is actually `stopped`, so repeating it is harmless. When AWS
has no capacity for the instance class the start fails; that is 503
`db_no_capacity`, and the next poll retries it.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import error_response

logger = logging.getLogger("alibi.db")

# Seconds the browser should wait before asking again.
RETRY_AFTER_SECONDS = 20


@lru_cache(maxsize=1)
def _client():  # noqa: ANN202
    import boto3
    from botocore.config import Config

    return boto3.client("lambda", config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1}))


NO_CAPACITY = "no_capacity"


def wake() -> str | None:
    """Ask the autostop Lambda to start the instance; return its RDS status.

    None when no waker is configured (local dev, tests) or the call itself
    failed — either way the caller can't claim the database is on its way.
    """
    name = get_settings().db_waker_function_name
    if not name:
        return None
    try:
        response = _client().invoke(FunctionName=name, Payload=b'{"action": "start"}')
        body = json.loads(response["Payload"].read() or b"{}")
    except Exception:
        logger.exception("db waker invoke failed")
        return None
    if response.get("FunctionError"):
        logger.error("db waker returned an error: %s", body)
        return None
    if body.get("error") == NO_CAPACITY:
        logger.error("db waker: AWS has no capacity to start the instance: %s", body.get("message"))
        return NO_CAPACITY
    return body.get("status")


async def db_unreachable_handler(request: Request, exc: Exception) -> JSONResponse:
    status = await run_in_threadpool(wake)
    if status == NO_CAPACITY:
        response = error_response(
            503,
            "db_no_capacity",
            "AWS has no capacity to start the database right now. Retrying automatically; it can take a while.",
        )
        response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
        return response
    # "available" means RDS is up and the connection failed for some other
    # reason — that is a real outage, not a nap.
    if status and status != "available":
        logger.info("database is %s; asked it to start (%s %s)", status, request.method, request.url.path)
        response = error_response(
            503, "db_waking", "La base de datos se está iniciando después de un período de inactividad. Tarda unos minutos."
        )
        response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
        return response
    logger.error("database unreachable on %s %s: %s", request.method, request.url.path, exc)
    return error_response(503, "db_unavailable", "No pudimos conectarnos a la base de datos. Probá de nuevo.")
