"""Token metering for the cost ledger: what each answered question really cost.

The model clients (`orchestrator/client.py`, `services/text2sql/client.py`)
report every completion here; whoever wants the bill for a piece of work opens
a `metering()` block around it and reads the calls afterwards. Outside a block,
`record` is a no-op — Module B's ingestion calls are not per-question cost.

A context variable rather than a parameter threaded through every node: the
calls happen four layers down, and the narrative and the chart run on worker
threads. Threads do not inherit a context on their own, so `run_in_context`
is what the caller submits instead of the bare function — the `Meter` object is
shared by reference, so both threads' calls land on the same bill.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.core.config import get_settings

logger = logging.getLogger("alibi.usage")

T = TypeVar("T")


@dataclass(frozen=True)
class ModelCall:
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class Meter:
    calls: list[ModelCall] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, call: ModelCall) -> None:
        with self._lock:
            self.calls.append(call)


_current: contextvars.ContextVar[Meter | None] = contextvars.ContextVar("alibi_usage_meter", default=None)


@contextmanager
def metering() -> Iterator[Meter]:
    meter = Meter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def record(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    meter = _current.get()
    if meter is not None:
        meter.add(ModelCall(model, int(prompt_tokens or 0), int(completion_tokens or 0)))


def run_in_context(fn: Callable[..., T]) -> Callable[..., T]:
    """`fn`, bound to a copy of the caller's context — for `executor.submit`."""
    ctx = contextvars.copy_context()

    def _bound(*args: Any, **kwargs: Any) -> T:
        return ctx.run(fn, *args, **kwargs)

    return _bound


def cost_usd(calls: list[ModelCall]) -> float:
    """What `calls` cost at the configured per-model prices (USD per 1M tokens)."""
    prices = get_settings().model_prices_usd
    total = 0.0
    for call in calls:
        price = prices.get(call.model)
        if price is None:
            if call.prompt_tokens or call.completion_tokens:
                logger.warning("no price configured for model %s; metered at 0", call.model)
            continue
        total += call.prompt_tokens * price.get("input", 0.0) / 1_000_000
        total += call.completion_tokens * price.get("output", 0.0) / 1_000_000
    return total


__all__ = ["Meter", "ModelCall", "cost_usd", "metering", "record", "run_in_context"]
