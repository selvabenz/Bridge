"""Wall-clock accounting for the QA checks: how long each engine and each
stage took, per check job, per Language QA pass and per interactive
`verse.runChecks`.

Measurement only. Nothing here changes which checks run or what they return;
it exists so that "which check is slow, and how often is it repeated?" has a
number instead of a guess (docs/BUILD_LOG.md, 2026-10-08).

Two pieces:

- `Timings`: a thread-safe accumulator of (name -> total ms, calls). A job or a
  pass owns one and reports it as `timings` on its result.
- `current()`: the `Timings` active on this thread, or a shared no-op. A job
  thread activates its own with `activate()`; code deep in the check path
  (`local_checks`, the Greek Room call sites) records against `current()`
  without being handed the object through every signature.

The per-step overhead is two `perf_counter()` calls and a dict update, so it
is safe at per-verse, per-rule granularity.
"""
from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator


class Timings:
    """Accumulated wall-clock per named step."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seconds: dict[str, float] = {}
        self._calls: dict[str, int] = {}
        self._started = time.perf_counter()

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - start)

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            self._seconds[name] = self._seconds.get(name, 0.0) + seconds
            self._calls[name] = self._calls.get(name, 0) + 1

    def merge(self, other: "Timings") -> None:
        for name, (seconds, calls) in other._items():
            with self._lock:
                self._seconds[name] = self._seconds.get(name, 0.0) + seconds
                self._calls[name] = self._calls.get(name, 0) + calls

    def _items(self) -> list[tuple[str, tuple[float, int]]]:
        with self._lock:
            return [(name, (self._seconds[name], self._calls[name])) for name in self._seconds]

    def elapsed(self) -> float:
        return time.perf_counter() - self._started

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """{name: {"ms": total, "calls": n}}, slowest first. JSON-native."""
        items = sorted(self._items(), key=lambda item: -item[1][0])
        return {name: {"ms": round(seconds * 1000, 1), "calls": calls} for name, (seconds, calls) in items}

    def summary(self, limit: int = 40) -> str:
        parts = [f"{name}={entry['ms']:.0f}ms/{entry['calls']}" for name, entry in list(self.as_dict().items())[:limit]]
        return f"total={self.elapsed():.2f}s " + " ".join(parts)


class _NullTimings(Timings):
    """The no-op `current()` when nothing is being measured on this thread."""

    def add(self, name: str, seconds: float) -> None:  # noqa: D401 - deliberately nothing
        return


NULL = _NullTimings()
_active = threading.local()


def current() -> Timings:
    """The `Timings` activated on this thread, else a no-op."""
    stack = getattr(_active, "stack", None)
    return stack[-1] if stack else NULL


@contextmanager
def activate(timings: Timings) -> Iterator[Timings]:
    """Make `timings` the target of `current()` on this thread for the block."""
    stack = getattr(_active, "stack", None)
    if stack is None:
        stack = _active.stack = []
    stack.append(timings)
    try:
        yield timings
    finally:
        stack.pop()


def trace(label: str, timings: Timings) -> None:
    """One `[trace]` line on stderr, the channel `bridge_service._trace` uses:
    the Rust shell relays it to the diagnostics panel and engine-events.log."""
    print(f"[trace] timing {label}: {timings.summary()}", file=sys.stderr, flush=True)
