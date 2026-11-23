"""Lambda entry points for the two ingestion stages (deployed topology only).

Two handlers, one per stage, matching the VPC split described in
`app/services/ingestion.py`:

* `processor_handler` — stage 1. Outside the VPC, container image (pandas,
  pyarrow, pdfplumber and friends do not fit a 250 MB zip). Reached two ways:
  an SQS batch carrying S3 `ObjectCreated` records in the normal path, and a
  direct invoke carrying `{ownerId, uploadId}` when a user retries.
* `committer_handler` — stage 2. Inside the VPC, plain zip, reuses the API's
  own package. Invoked by the processor.

Both are thin on purpose: parse the event, call the orchestrator, decide whether
to acknowledge. Nothing here knows how ingestion works.

**Failure semantics are the whole point of these two functions being separate
from the orchestrator.** `run_stage1` never raises — it records the failure in
`result.json` — so a *bad file* is a successful invocation that must be
acknowledged, or SQS would redeliver it three times and then park a legitimate
user error in the DLQ. Only an *infrastructure* failure (S3 unreachable, the
committer invoke rejected) raises, which is exactly what should be retried.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import unquote_plus

from app.core.config import get_settings
from app.ingest.storage import RAW_UPLOAD_ROOT, get_object_store, raw_upload_prefix
from app.services.ingestion import commit_stage2, run_stage1

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# `uploads/{owner_id}/{upload_id}/{filename}` — the layout `raw_upload_key`
# writes. Parsed rather than looked up in the database because stage 1 has no
# database: the key itself carries everything needed to start work.
RAW_PREFIX = f"{RAW_UPLOAD_ROOT}/"


class UnparseableEvent(ValueError):
    """The event does not describe an upload we can act on."""


def parse_raw_key(key: str) -> tuple[str, str, str]:
    """`uploads/u_ana/up_123/ventas.csv` → (`u_ana`, `up_123`, `ventas.csv`)."""
    if not key.startswith(RAW_PREFIX):
        raise UnparseableEvent(f"key outside {RAW_PREFIX}: {key!r}")
    parts = key[len(RAW_PREFIX) :].split("/", 2)
    if len(parts) != 3 or not all(parts):
        raise UnparseableEvent(f"unexpected key shape: {key!r}")
    return parts[0], parts[1], parts[2]


def _jobs_from_event(event: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """Every (owner_id, upload_id, filename) this event asks us to process.

    Handles the two shapes that reach this function, so the retry path and the
    S3-event path share one body below.
    """
    # Direct invoke — a retry from the API (`LambdaRunner.submit`).
    if "uploadId" in event or "upload_id" in event:
        owner = event.get("ownerId") or event.get("owner_id")
        upload = event.get("uploadId") or event.get("upload_id")
        if not owner or not upload:
            raise UnparseableEvent("direct invoke needs both ownerId and uploadId")
        return [(owner, upload, event.get("filename"))]

    jobs: list[tuple[str, str, str | None]] = []
    for record in event.get("Records") or []:
        # SQS wraps the S3 notification in a JSON string body.
        body = record.get("body")
        payload = json.loads(body) if isinstance(body, str) else record
        # An S3 test event has no Records; skip it rather than failing the batch.
        for s3_record in payload.get("Records") or []:
            s3 = s3_record.get("s3") or {}
            key = (s3.get("object") or {}).get("key")
            if not key:
                continue
            # S3 URL-encodes the key in notifications, and `+` means a space —
            # `unquote` alone would leave "mi+archivo.csv" wrong.
            owner, upload, filename = parse_raw_key(unquote_plus(key))
            jobs.append((owner, upload, filename))
    if not jobs:
        raise UnparseableEvent("no S3 object records in the event")
    return jobs


def _invoke_committer(owner_id: str, upload_id: str) -> None:
    """Hand off to stage 2, which is the only half that can write Postgres."""
    settings = get_settings()
    function_name = settings.committer_function_name
    if not function_name:
        # Raising means SQS retries. Correct: the work succeeded but nothing
        # recorded it, and the result.json is sitting there ready to commit.
        raise RuntimeError("ALIBI_COMMITTER_FUNCTION_NAME is not set")

    # Imported after the config check so a misconfiguration reports itself
    # rather than surfacing as a missing dependency.
    import boto3

    boto3.client("lambda", region_name=settings.aws_region).invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps({"ownerId": owner_id, "uploadId": upload_id}).encode(),
    )


def processor_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Stage 1: parse the file, write Parquet, hand off to the committer."""
    try:
        jobs = _jobs_from_event(event)
    except (UnparseableEvent, json.JSONDecodeError) as exc:
        # Nothing to retry — a malformed event will still be malformed next
        # time, and failing would send it round the DLQ for no reason.
        logger.warning("ignoring unusable event: %s", exc)
        return {"processed": 0, "skipped": 1}

    processed = 0
    for owner_id, upload_id, filename in jobs:
        logger.info("stage 1 starting: owner=%s upload=%s", owner_id, upload_id)
        if filename is None:
            # A retry gives us only the ids, so the filename has to come from
            # the object itself. Its key is the one thing we can enumerate.
            filename = _filename_from_store(owner_id, upload_id)
            if filename is None:
                logger.error("no raw object left for upload %s", upload_id)
                continue

        result = run_stage1(owner_id, upload_id, filename)
        # Invoked even on failure: `result.json` carries the error message, and
        # stage 2 is what puts it in front of the user.
        _invoke_committer(owner_id, upload_id)
        processed += 1
        logger.info(
            "stage 1 done: upload=%s tables=%d error=%s",
            upload_id,
            len(result.datasets),
            result.error,
        )

    return {"processed": processed}


def _filename_from_store(owner_id: str, upload_id: str) -> str | None:
    """The raw object's filename, recovered from its key on a retry."""
    prefix = f"{raw_upload_prefix(owner_id, upload_id)}/"
    found = get_object_store().list_prefix(prefix)
    return found[0][len(prefix) :] if found else None


def committer_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Stage 2: read `result.json` and write the control plane."""
    owner_id = event.get("ownerId") or event.get("owner_id")
    upload_id = event.get("uploadId") or event.get("upload_id")
    if not owner_id or not upload_id:
        logger.warning("ignoring committer event without ids: %s", event)
        return {"committed": False}

    logger.info("stage 2 starting: owner=%s upload=%s", owner_id, upload_id)
    committed = commit_stage2(owner_id, upload_id)
    logger.info("stage 2 done: upload=%s committed=%s", upload_id, committed)
    return {"committed": committed}


__all__ = ["committer_handler", "parse_raw_key", "processor_handler"]
