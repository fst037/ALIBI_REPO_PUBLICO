"""Answer runs held in memory — local runs without a database, and the test suite."""

from __future__ import annotations

import threading

from app.schemas.runs import Run

_lock = threading.Lock()
_runs: dict[str, tuple[str, Run]] = {}  # run id -> (owner id, run)


def save_run(owner_id: str, run: Run) -> None:
    with _lock:
        stored = _runs.get(run.id)
        if stored is not None and stored[0] != owner_id:
            return
        # A copy, so a caller mutating its own object between saves cannot change
        # what a concurrent poll reads mid-step.
        _runs[run.id] = (owner_id, run.model_copy(deep=True))


def get_run(owner_id: str, run_id: str) -> Run | None:
    with _lock:
        stored = _runs.get(run_id)
        if stored is None or stored[0] != owner_id:
            return None
        return stored[1].model_copy(deep=True)


def reset() -> None:
    with _lock:
        _runs.clear()
