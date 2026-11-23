"""Where an answer run executes — same shape as `app/ingest/jobs.py`.

* `ThreadRunner` — a daemon thread in the API process. The default: locally and
  in the test suite the whole start → poll → answer flow works with no AWS.
* `LambdaRunner` — an async (`InvocationType="Event"`) invoke of the run-worker
  Lambda (`app/run_worker.py`), which has 15 minutes instead of API Gateway's
  29 seconds. The API returns the queued run immediately either way.
"""

from __future__ import annotations

import json
import logging
import threading
from abc import ABC, abstractmethod

from app.core.config import JobRunnerKind, get_settings

logger = logging.getLogger(__name__)


class RunRunner(ABC):
    @abstractmethod
    def submit(self, owner_id: str, run_id: str) -> None:
        """Start executing `run_id`. Must not block the caller."""

    def wait_all(self, timeout: float = 30.0) -> None:
        """Block until in-flight runs finish. No-op unless runs are local."""


class ThreadRunner(RunRunner):
    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, owner_id: str, run_id: str) -> None:
        from app.services.runs import execute_run

        thread = threading.Thread(target=self._run, args=(execute_run, owner_id, run_id), name=f"run-{run_id}", daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()

    @staticmethod
    def _run(target, owner_id: str, run_id: str) -> None:  # noqa: ANN001
        try:
            target(owner_id, run_id)
        except Exception:  # noqa: BLE001 - a thread that raises dies silently
            logger.exception("answer run crashed (run=%s)", run_id)

    def wait_all(self, timeout: float = 30.0) -> None:
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout)


class LambdaRunner(RunRunner):
    def __init__(self, function_name: str, region: str) -> None:
        import boto3

        self.function_name = function_name
        self._client = boto3.client("lambda", region_name=region)

    def submit(self, owner_id: str, run_id: str) -> None:
        self._client.invoke(
            FunctionName=self.function_name,
            InvocationType="Event",
            Payload=json.dumps({"ownerId": owner_id, "runId": run_id}).encode(),
        )


_runner: RunRunner | None = None


def get_run_runner() -> RunRunner:
    global _runner
    if _runner is None:
        settings = get_settings()
        if settings.run_runner is JobRunnerKind.lambda_:
            if not settings.run_worker_function_name:
                raise RuntimeError("ALIBI_RUN_RUNNER=lambda requires ALIBI_RUN_WORKER_FUNCTION_NAME")
            _runner = LambdaRunner(settings.run_worker_function_name, settings.aws_region)
        else:
            _runner = ThreadRunner()
    return _runner


def reset_run_runner() -> None:
    global _runner
    _runner = None
