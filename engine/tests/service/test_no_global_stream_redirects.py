"""Engine code never calls ``contextlib.redirect_stdout`` / ``redirect_stderr``.

Both swap a process-global stream for every thread while the block runs. In the
sidecar that cost real work twice: ``usfm_parser``'s ``redirect_stdout`` on a job
thread swallowed protocol responses (``ai.review.status`` timeouts), and
``versification``'s ``redirect_stderr`` swallowed other threads' trace lines and
tracebacks (#248). Vendored trees and the tests themselves are out of scope.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.support.paths import ENGINE_ROOT

_REDIRECTS = {"redirect_stdout", "redirect_stderr"}
_SKIP = {"vendor", "tests", "build", "dist", ".venv", "__pycache__"}


def _redirect_calls(path: Path, root: Path = ENGINE_ROOT) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"), str(path))):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in _REDIRECTS:
                found.append(f"{path.relative_to(root)}:{node.lineno} {name}")
    return found


def _engine_sources(root: Path = ENGINE_ROOT) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if not _SKIP.intersection(path.relative_to(root).parts[:-1])
    )


def test_no_engine_module_swaps_a_global_stream():
    offenders = [hit for path in _engine_sources() for hit in _redirect_calls(path)]
    assert not offenders, (
        "contextlib.redirect_* swaps sys.stdout/sys.stderr for every thread; "
        "the stdio transport already keeps stray prints off the protocol:\n" + "\n".join(offenders)
    )


def test_the_guard_sees_both_call_forms(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import contextlib, io\n"
        "from contextlib import redirect_stderr\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n    pass\n"
        "with redirect_stderr(io.StringIO()):\n    pass\n",
        encoding="utf-8",
    )
    assert _redirect_calls(sample, tmp_path) == ["sample.py:3 redirect_stdout", "sample.py:5 redirect_stderr"]


def test_the_guard_covers_the_dispatcher_and_the_engine_packages():
    covered = {path.relative_to(ENGINE_ROOT).parts[0] for path in _engine_sources()}
    assert {"bridge_service.py", "main.py", "tc_ai_bridge", "greek_room_engine"} <= covered
