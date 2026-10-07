"""CLAUDE.md rule 2: a test module never imports another test module.

A borrowed helper drags the lender's whole import graph (often the 5,300-line
dispatcher) into the borrower, which is what made "which tests could this
change affect?" unanswerable (#74). Seven such imports were removed in #74
phase 4; twenty-five had come back by 2026-10-07 with nothing to stop them.
Shared builders go in tests/support/; this test is what keeps them there.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def _test_module_imports(path: Path, root: Path = TESTS.parent) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            leaf = module.rsplit(".", 1)[-1]
            if leaf.startswith("test_") or (node.level and not module and any(
                    alias.name.startswith("test_") for alias in node.names)):
                found.append(f"{path.relative_to(root)}:{node.lineno} from {'.' * node.level}{module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1].startswith("test_"):
                    found.append(f"{path.relative_to(root)}:{node.lineno} import {alias.name}")
    return found


def test_no_test_module_imports_another_test_module():
    offenders = [hit for path in sorted(TESTS.rglob("*.py")) for hit in _test_module_imports(path)]
    assert not offenders, "Move the shared helper to tests/support/ instead:\n" + "\n".join(offenders)


def test_the_guard_sees_both_import_forms(tmp_path):
    sample = tmp_path / "test_sample.py"
    sample.write_text("from tests.service.test_bridge_service import call\n"
                      "from .test_other import helper\n"
                      "from . import test_third\n"
                      "import tests.ai.test_triage\n"
                      "from tests.support.projects import call\n", encoding="utf-8")
    hits = _test_module_imports(sample, root=tmp_path)
    assert [hit.split(" ", 1)[1] for hit in hits] == [
        "from tests.service.test_bridge_service", "from .test_other", "from .", "import tests.ai.test_triage"]
