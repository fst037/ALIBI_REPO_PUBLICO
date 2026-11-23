"""Lambda entry point for the answer-run worker (deployed topology only).

The API Lambda async-invokes this function (`InvocationType="Event"`,
`app/services/run_jobs.LambdaRunner`) with `{"ownerId", "runId"}` and returns
the queued run to the browser at once; this function then has its full timeout
(15 minutes) to answer, where a request through API Gateway has 29 seconds.

Same zip and same environment as the API Lambda (`code/backend.zip`, see
`backend/terraform/modules/api-lambda`), different handler — one build, two
functions, no drift. It runs in the VPC like the API, because it reads the
conversation's folder from Postgres, and reaches the models the same way the
API does (through `app/llm_bridge.py`).

`execute_run` records every outcome on the run and never raises, so a failure
never makes Lambda retry an async invoke that already wrote its result.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    owner_id, run_id = event.get("ownerId"), event.get("runId")
    if not owner_id or not run_id:
        logger.warning("run worker invoked without ownerId/runId: %s", event)
        return {"ok": False}

    from app.services.runs import execute_run

    execute_run(owner_id, run_id)
    return {"ok": True}


__all__ = ["handler"]
