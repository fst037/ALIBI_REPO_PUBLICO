"""`runs` domain service — asking a question asynchronously.

    POST …/runs   start_run   record the run as `queued`, hand it to the runner,
                              return it at once
    worker        execute_run `running` → steps appended as they happen →
                              `done` (message or guard) or `failed` (error)
    GET  …/runs/x get_run     what the client polls

Why: an answer can take longer than API Gateway's 29 s request ceiling — and with
the agent harness a Deep answer routinely will.
The work itself is `conversations.answer()` — the agent harness for a question,
the one-query cycle for hand-written SQL; this module only owns the record
around it and where it runs (`run_jobs`).

Storage dispatches like every other domain: Postgres when configured
(`repositories/runs.py`), else the in-memory store (`runs_memory.py`).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.errors import ApiException
from app.db import pool
from app.db.engine import session_scope
from app.repositories import runs as repo
from app.schemas.chat import GuardResult
from app.schemas.notifications import NotificationAction
from app.schemas.runs import (
    Run,
    RunError,
    RunMode,
    RunStatus,
    RunStep,
    RunStepPurpose,
    RunStepStatus,
    RunStepTool,
    StartRunRequest,
)
from app.services import billing, conversations, notifications
from app.services import runs_memory as mem
from app.services.run_jobs import get_run_runner

logger = logging.getLogger("alibi.runs")

_UNEXPECTED = RunError(
    code="internal_error",
    message="Algo salió mal mientras preparábamos la respuesta. Probá de nuevo.",
)
_STALE = RunError(
    code="run_timeout",
    message="La respuesta tardó demasiado y se interrumpió. Probá de nuevo.",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(owner_id: str, run: Run) -> None:
    run.updatedAt = _now()
    if pool.is_configured():
        with session_scope() as session:
            repo.save_run(session, owner_id, run)
        return
    mem.save_run(owner_id, run)


def _load(owner_id: str, run_id: str) -> Run | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_run(session, owner_id, run_id)
    return mem.get_run(owner_id, run_id)


# ---- API side ----------------------------------------------------------------


def start_run(owner_id: str, conversation_id: str, request: StartRunRequest) -> Run:
    """Record the run and hand it to the runner.

    `mode` is the request's when it names one (an override for this question
    alone), else the conversation's default. Resolved here, once, so the
    run records the depth it was actually answered at.
    """
    conversation = conversations.get_conversation(owner_id, conversation_id)
    if conversation is None:
        raise ApiException(404, "not_found", "No se encontró la conversación.")
    # Refused here, synchronously, so a user past their quota gets the 402 at
    # once instead of a run that fails a poll later. `prepare` checks again
    # inside the run, for a run queued just before the quota ran out.
    billing.require_query_allowance(owner_id)
    # Deep is a Pro feature. Asked for this one
    # question, it is refused; inherited from a conversation saved as Deep
    # before a downgrade, the question is answered Quick instead — the saved
    # default comes back on its own if the user returns to Pro.
    # Extra queries beyond the quota are answered Quick, the same way: refused
    # when asked Deep explicitly, clamped when Deep is only the default.
    mode = request.mode or conversation.mode
    if request.mode is not None:
        billing.require_answer_mode(owner_id, request.mode)
        if request.mode is RunMode.deep:
            billing.require_deep_within_quota(owner_id)
    elif not billing.allows_mode(owner_id, mode) or (mode is RunMode.deep and billing.in_overage(owner_id)):
        mode = RunMode.quick
    now = _now()
    run = Run(
        id="run_" + secrets.token_urlsafe(8),
        conversationId=conversation_id,
        question=request.question if request.question is not None else (request.sql or ""),
        isSql=request.question is None,
        mode=mode,
        createdAt=now,
        updatedAt=now,
    )
    _save(owner_id, run)
    get_run_runner().submit(owner_id, run.id)
    return run


def get_run(owner_id: str, conversation_id: str, run_id: str) -> Run | None:
    run = _load(owner_id, run_id)
    if run is None or run.conversationId != conversation_id:
        return None
    return _with_staleness(run)


def _with_staleness(run: Run) -> Run:
    """Report a run whose worker died as failed, without writing anything.

    A Lambda killed at its timeout cannot record its own end, so a run would
    otherwise poll as `running` forever. Read-time only: if the worker is in
    fact still alive and finishes, its own save wins.
    """
    if run.status not in (RunStatus.queued, RunStatus.running):
        return run
    updated = datetime.fromisoformat(run.updatedAt)
    if datetime.now(timezone.utc) - updated < timedelta(seconds=get_settings().run_stale_after_seconds):
        return run
    return run.model_copy(update={"status": RunStatus.failed, "error": _STALE})


# ---- worker side ---------------------------------------------------------------


class _StepRecorder:
    """The `step(label, purpose=…)` context manager handed to `conversations.answer`."""

    def __init__(self, owner_id: str, run: Run) -> None:
        self._owner_id = owner_id
        self._run = run

    @contextmanager
    def step(
        self,
        label: str,
        purpose: RunStepPurpose | None = None,
        thought: str | None = None,
        tool: RunStepTool | None = None,
        model: str | None = None,
        question: str | None = None,
    ) -> Iterator[RunStep]:
        # Everything known up front goes in at creation: the step is saved here,
        # so the planner's reason and the tool it called are on the poll while
        # the step runs, not only once it is done.
        current = RunStep(
            id="step_" + secrets.token_urlsafe(6),
            label=label,
            purpose=purpose,
            thought=thought,
            tool=tool,
            model=model,
            question=question,
            startedAt=_now(),
        )
        self._run.steps.append(current)
        _save(self._owner_id, self._run)
        try:
            yield current
        except Exception as exc:
            current.status = RunStepStatus.failed
            # `ApiException` and the harness's `ToolFailed` carry a readable
            # message; anything else is reported by its type, not its text.
            message = getattr(exc, "message", None)
            current.error = message if isinstance(message, str) else type(exc).__name__
            raise
        else:
            current.status = RunStepStatus.done
        finally:
            current.finishedAt = _now()
            _save(self._owner_id, self._run)


def execute_run(owner_id: str, run_id: str) -> None:
    """Run it to the end. Never raises: every outcome is written onto the run.

    Idempotent on a `queued` check, because Lambda may deliver an async invoke
    more than once — a second delivery finds the run already taken and leaves.
    """
    run = _load(owner_id, run_id)
    if run is None or run.status is not RunStatus.queued:
        return
    run.status = RunStatus.running
    _save(owner_id, run)

    recorder = _StepRecorder(owner_id, run)
    try:
        outcome = conversations.answer(
            owner_id,
            run.conversationId,
            question=None if run.isSql else run.question,
            sql=run.question if run.isSql else None,
            step=recorder.step,
            deep=run.mode is RunMode.deep,
        )
        if isinstance(outcome, GuardResult):
            run.guard = outcome
        else:
            run.message, run.receipt = outcome.message, outcome.receipt
        run.status = RunStatus.done
    except ApiException as exc:
        run.status = RunStatus.failed
        run.error = RunError(code=exc.code, message=exc.message)
    except Exception:  # noqa: BLE001 - the run must end recorded, whatever happened
        logger.exception("answer run %s crashed", run_id)
        run.status = RunStatus.failed
        run.error = _UNEXPECTED
    _save(owner_id, run)
    _notify_deep(owner_id, run)


def _notify_deep(owner_id: str, run: Run) -> None:
    """A deep investigation takes minutes, so the user is likely elsewhere.

    Quick answers are not announced: the user is looking at the screen.
    """
    if run.mode is not RunMode.deep:
        return
    done = run.status is RunStatus.done
    question = " ".join(run.question.split())
    if len(question) > 80:
        question = question[:79] + "…"
    notifications.notify(
        owner_id,
        tone="ok" if done else "err",
        title="Tu investigación está lista" if done else "La investigación no se pudo completar",
        body=f"Terminamos de analizar: «{question}»."
        if done
        else (run.error.message if run.error else _UNEXPECTED.message),
        action=NotificationAction(
            label="Ver respuesta" if done else "Abrir conversación",
            href=f"/app?conversation={run.conversationId}",
        ),
    )
