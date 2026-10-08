"""
Stdio transport for the sidecar process.

Per doc §4: the process starts with the Tauri app and stays alive
(resources like Uroman/Wildebeest are loaded once, not per-call). Requests
arrive as newline-delimited JSON on stdin; responses are written the same
way on stdout. stderr is reserved for logs/crashes so it never corrupts
the protocol stream.
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Protocol

from ..protocol import EngineRequest, EngineResponse


class _Dispatcher(Protocol):
    def handle_request(self, request: EngineRequest) -> EngineResponse: ...


def run_stdio_loop(engine: Any | None = None, on_ready: Any | None = None) -> None:
    """`on_ready` runs once, right after the ready line, for warmup work that
    must not delay it (main.py preloads uroman, #242). A failure there is
    logged and never stops the loop."""
    if engine is None:
        from ..engine import GreekRoomEngine
        engine = GreekRoomEngine()

    # CRITICAL for Windows: Python's stdout/stdin default to the console's
    # legacy codepage (not UTF-8) unless told otherwise. Since verse text
    # is Tamil/Hebrew/etc, an un-reconfigured stdout raises
    # UnicodeEncodeError the moment a non-ASCII response is printed — which
    # silently kills this loop, and every request after that times out on
    # the Rust side with no indication the sidecar process actually died.
    # Force UTF-8 explicitly rather than relying on the platform default.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # Signal readiness once on startup so the Rust side knows the sidecar
    # finished loading (import cost for NLP resources can be nontrivial).
    print(EngineResponse.ok("__ready__", result={"status": "ready"}).to_json(), flush=True)
    if on_ready is not None:
        try:
            on_ready()
        except Exception:  # noqa: BLE001
            print("[unhandled] on_ready", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = EngineRequest.from_json(line)
        except Exception as exc:  # noqa: BLE001
            print(EngineResponse.fail("__unknown__", "bad_request", str(exc)).to_json(), flush=True)
            continue

        # Defense in depth: a crash anywhere in handling OR printing a
        # response must produce a failure response, never silently kill
        # the loop — a dead sidecar looks identical to a hung one from the
        # Rust side (both just time out), which is much harder to debug.
        try:
            response = engine.handle_request(request)
            print(response.to_json(), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(EngineResponse.fail(request.id, "internal_error", str(exc)).to_json(), flush=True)
            # str(exc) alone loses the traceback, which is the one thing
            # that actually tells you where an "internal_error" came from.
            # Rust relays stderr into its own log (see sidecar.rs), so this
            # is the only place that trace survives a release build where
            # eprintln has no console to write to.
            print(f"[unhandled] {request.method}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()


if __name__ == "__main__":
    run_stdio_loop()
