"""Step timings for performance runs (tools/perf_run.py).

`plan_trip` sets the current trip's Usage via `track(usage)`; every `step(kind, label)` then appends
{kind, label, start, seconds, ...} to `Usage.timings` - no need to pass usage through every function.
asyncio tasks inherit the context, so parallel steps land in the right trip.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from .models import Usage

log = logging.getLogger("holydays.timing")
_current: ContextVar[Usage | None] = ContextVar("timing_usage", default=None)


def track(usage: Usage) -> None:
    _current.set(usage)


@contextmanager
def step(kind: str, label: str, **extra) -> Iterator[dict]:
    """Time a block; the yielded dict can be filled with details (status, tokens, cache hit ...)."""
    record = {"kind": kind, "label": label, "start": time.time(), **extra}
    t0 = time.perf_counter()
    try:
        yield record
    finally:
        record["seconds"] = round(time.perf_counter() - t0, 3)
        usage = _current.get()
        if usage is not None:
            usage.timings.append(record)
        log.debug("%s %s: %.2f s", kind, label, record["seconds"])
