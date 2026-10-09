"""The JSON-lines stream must survive other threads touching ``sys.stdout``.

``sys.stdout`` is process-global. A background job (an AI review, a check job,
a Language QA pass) that runs ``contextlib.redirect_stdout`` -- usfm_parser did,
around every usfmtc parse -- swaps it for every thread at once, so a response
the dispatcher printed in that window went into the job's StringIO and was never
seen by Rust: a 30 s ``ai.review.status`` timeout with no slow request in the
engine log. A stray ``print`` from a library would corrupt the stream the same
way. The transport writes to the stream it captured at start-up instead.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import textwrap
import threading
import time

from tests.support.paths import ENGINE_ROOT

_SIDECAR = textwrap.dedent("""
    import contextlib, io, threading
    from greek_room_engine.protocol import EngineResponse
    from greek_room_engine.transport.stdio_transport import run_stdio_loop

    def noisy_background_job():
        while True:
            with contextlib.redirect_stdout(io.StringIO()):
                pass
            print("stray library output")

    class Engine:
        started = False

        def handle_request(self, request):
            # The first request starts the job, as ai.review.start does.
            if not self.started:
                self.started = True
                threading.Thread(target=noisy_background_job, daemon=True).start()
            return EngineResponse.ok(request.id, result={"method": request.method})

    run_stdio_loop(Engine())
""")


def test_responses_survive_a_background_thread_swapping_stdout():
    process = subprocess.Popen(
        [sys.executable, "-c", _SIDECAR], cwd=ENGINE_ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    # stderr is drained so the stray prints can never fill its pipe and stall the child.
    threading.Thread(target=lambda: [None for _ in process.stderr], daemon=True).start()
    try:
        expected = {f"r{index}" for index in range(300)}
        for request_id in sorted(expected):
            process.stdin.write(json.dumps({"id": request_id, "method": "ping", "params": {}}) + "\n")
        process.stdin.flush()

        answered: set[str] = set()
        deadline = time.monotonic() + 20
        while answered != expected and time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            message = json.loads(line)  # a stray print on stdout fails here
            if message["id"] != "__ready__":
                assert message["success"] is True, message
                answered.add(message["id"])
        assert answered == expected, f"{len(expected - answered)} of 300 responses never arrived"
    finally:
        process.kill()
        process.communicate(timeout=10)
