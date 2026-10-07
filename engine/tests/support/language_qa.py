"""Builders for the Language QA manager tests: a minimal project namespace, a
wait for the pass, and the issue a verse.decide carries.

Shared here so no test module imports another (CLAUDE.md, #74)."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest


def issue_for(finding):
    """What the frontend sends with a Language QA verse.decide."""
    return {key: finding[key] for key in ("source", "rule", "ruleVersion", "originalText",
                                          "suggestedReplacement", "message", "start", "end")}


def project_at(root, text="தமிழ் தமிழ்  ", book="php", verses=None, terminology=None, decisions=None):
    folder = root / book
    folder.mkdir(parents=True)
    (folder / "1.json").write_text(json.dumps(verses or {"3a": text}, ensure_ascii=False), encoding="utf-8")
    namespace = SimpleNamespace(path=root, book_id=book, book_dir=folder,
                                manifest={"target_language": {"id": "tam"}})
    if terminology is not None:
        namespace.terminology_rules = terminology if callable(terminology) else (lambda: terminology)
    if decisions is not None:
        namespace.project_qa_decisions = decisions if callable(decisions) else (lambda: decisions)
    return namespace


def wait(manager, state="completed"):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = manager.status(limit=100)
        if status["state"] == state:
            return status
        assert status["state"] != "failed", status
        time.sleep(.005)
    pytest.fail(f"Language QA did not reach {state}: {manager.status()}")
