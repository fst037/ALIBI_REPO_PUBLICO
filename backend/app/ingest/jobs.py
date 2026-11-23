"""How an ingestion job gets executed once the bytes have landed.

The pipeline itself is synchronous and pure (`app.ingest.pipeline`). What
changes between a laptop and the deployed stack is only *where* it runs, so
that choice lives behind one interface:

* `ThreadRunner` — a daemon thread in the API process. The default, and the
  reason the whole upload → parse → confirm → query flow works locally with no
  AWS account. A 25 MB spreadsheet parses in a few seconds; uvicorn keeps
  serving requests meanwhile.
* `LambdaRunner` — an async `Invoke` of the stage-1 processor function, used
  when deployed.

Threads, not `asyncio`: the pipeline is CPU-bound pandas/pyarrow work, which
would block the event loop. A thread releases the GIL inside those libraries,
so the API stays responsive.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod

from app.core.config import JobRunnerKind, get_settings

logger = logging.getLogger(__name__)


class JobRunner(ABC):
    @abstractmethod
    def submit(self, owner_id: str, upload_id: str) -> None:
        """Start processing `upload_id`. Must not block the caller."""

    def wait_all(self, timeout: float = 30.0) -> None:
        """Block until in-flight jobs finish. No-op unless jobs are local."""


class ThreadRunner(JobRunner):
    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, owner_id: str, upload_id: str) -> None:
        # Imported here, not at module scope: the orchestrator imports the
        # services, which import this module. A local import keeps the cycle
        # from existing at import time.
        from app.services.ingestion import process_upload

        thread = threading.Thread(
            target=self._run,
            args=(process_upload, owner_id, upload_id),
            name=f"ingest-{upload_id}",
            daemon=True,
        )
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()

    @staticmethod
    def _run(target, owner_id: str, upload_id: str) -> None:
        try:
            target(owner_id, upload_id)
        except Exception:  # noqa: BLE001 - a thread that raises dies silently
            # `process_upload` already records user-facing failures itself, so
            # reaching here means a bug rather than a bad file. Log it loudly;
            # the upload will simply stay in `processing` until it's retried.
            logger.exception("ingestion job crashed (upload=%s)", upload_id)

    def wait_all(self, timeout: float = 30.0) -> None:
        """Join outstanding jobs.

        Used by tests, which need the upload finished before asserting, and by
        a graceful shutdown that shouldn't drop a half-written dataset.
        """
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout)


class LambdaRunner(JobRunner):
    """Async invoke of the stage-1 processor.

    Only reached on a *retry*. In the normal deployed path nothing here runs:
    the browser PUTs to S3, the ObjectCreated event goes to SQS, and the
    processor picks it up without the API being involved at all.
    """

    def __init__(self, function_name: str, region: str) -> None:
        import boto3

        self.function_name = function_name
        self._client = boto3.client("lambda", region_name=region)

    def submit(self, owner_id: str, upload_id: str) -> None:
        import json

        self._client.invoke(
            FunctionName=self.function_name,
            # Fire-and-forget: the API returns 201 immediately and the browser
            # polls GET /uploads/{id} for progress.
            InvocationType="Event",
            Payload=json.dumps({"ownerId": owner_id, "uploadId": upload_id}).encode(),
        )


_runner: JobRunner | None = None


def get_job_runner() -> JobRunner:
    global _runner
    if _runner is None:
        settings = get_settings()
        if settings.job_runner is JobRunnerKind.lambda_:
            if not settings.processor_function_name:
                raise RuntimeError(
                    "ALIBI_JOB_RUNNER=lambda requires ALIBI_PROCESSOR_FUNCTION_NAME"
                )
            _runner = LambdaRunner(settings.processor_function_name, settings.aws_region)
        else:
            _runner = ThreadRunner()
    return _runner


def reset_job_runner() -> None:
    global _runner
    _runner = None


__all__ = [
    "JobRunner",
    "LambdaRunner",
    "ThreadRunner",
    "get_job_runner",
    "reset_job_runner",
]
