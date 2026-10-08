"""
GreekRoomEngine: the one class the rest of our application is allowed to
call. Nothing outside this package should import wildebeest, owl, or any
other upstream module directly (doc §3, §31-32).
"""
from __future__ import annotations

import uuid
from typing import Any

from .adapters.base import CheckAdapter
from .adapters.wildebeest_adapter import WildebeestAdapter
from .adapters.usfm_adapter import UsfmAdapter, UsfmCheckerError
from .adapters.names_adapter import NamesAdapter, NamesCheckError
from .models.finding import QaFinding
from .protocol import EngineRequest, EngineResponse, Methods

# Kept in step with package.json by tests/service/test_version_consistency.py.
ENGINE_VERSION = "0.13.0"


class GreekRoomEngine:
    def __init__(self) -> None:
        self._adapters: dict[str, CheckAdapter] = {}
        self._register_default_adapters()

    def _register_default_adapters(self) -> None:
        self.register_adapter(WildebeestAdapter())
        self.register_adapter(UsfmAdapter())
        self.register_adapter(NamesAdapter())
        # OWL and UAlign adapters are added in later roadmap stages per doc
        # §35-41 — register here when ready.

    def register_adapter(self, adapter: CheckAdapter) -> None:
        self._adapters[adapter.engine_name] = adapter

    def info(self) -> dict[str, Any]:
        return {
            "engineVersion": ENGINE_VERSION,
            "adapters": {
                name: {
                    "available": a.is_available(),
                    "usingRealEngine": a.using_real_engine(),
                    "version": a.version(),
                    **a.status(),
                }
                for name, a in self._adapters.items()
            },
        }

    def preload_async(self) -> None:
        """Start loading what is slow to load (uroman, for names) on a
        background thread. Called once the sidecar is ready (#242)."""
        names = self._adapters.get("names")
        if names is not None and hasattr(names, "preload_async"):
            names.preload_async()

    def check_verse(self, *, project_id: str, lang_code: str, ref: str,
                     text: str, checks: list[str]) -> list[QaFinding]:
        """Run the requested checks (by adapter/engine name) against one verse.

        A missing/unavailable adapter is skipped, not fatal — the whole
        request should never fail just because one upstream engine (e.g.
        USFM checker, which needs more integration work) isn't ready yet.
        """
        findings: list[QaFinding] = []
        run_id = str(uuid.uuid4())

        for check_name in checks:
            adapter = self._adapters.get(check_name)
            if adapter is None or not adapter.is_available():
                continue
            result = adapter.check_verse(
                project_id=project_id, lang_code=lang_code, ref=ref,
                text=text, params={},
            )
            for f in result:
                f.run_id = run_id
            findings.extend(result)

        return findings

    def check_book_usfm(
        self, *, project_id: str, book_id: str, usfm_text: str, cancel_event=None,
    ) -> list[QaFinding]:
        """USFM structural checks (duplicate/missing verses, unclosed
        markers, ...) operate on a whole book, not one verse — unlike
        check_verse, this is not part of the per-verse checks list and must
        be called explicitly, once per book, by the caller (bridge_service
        caches the result rather than re-running this per verse.runChecks
        call — see UsfmAdapter's own docstring for why)."""
        adapter = self._adapters.get("usfm")
        if adapter is None:
            raise UsfmCheckerError("USFM structural checker adapter is not registered")
        findings = adapter.check_book(
            project_id=project_id, book_id=book_id, usfm_text=usfm_text,
            cancel_event=cancel_event,
        )
        run_id = str(uuid.uuid4())
        for f in findings:
            f.run_id = run_id
        return findings

    def check_book_names(
        self, *, project_id: str, book_id: str, lang_code: str,
        token_occurrences: dict[str, list[tuple[str, str]]],
    ) -> list[QaFinding]:
        """Names/transliteration spelling-consistency check — like
        check_book_usfm, this is whole-book, not part of the per-verse
        checks list, and must be called explicitly by the caller
        (bridge_service caches the result per book — see NamesAdapter's own
        docstring for why: consistency is inherently a corpus-level
        question, not a per-verse one)."""
        adapter = self._adapters.get("names")
        if adapter is None:
            raise NamesCheckError("Names/transliteration adapter is not registered")
        findings = adapter.check_book(
            project_id=project_id, book_id=book_id, lang_code=lang_code,
            token_occurrences=token_occurrences,
        )
        run_id = str(uuid.uuid4())
        for f in findings:
            f.run_id = run_id
        return findings

    # -- protocol dispatch --------------------------------------------------

    def handle_request(self, request: EngineRequest) -> EngineResponse:
        try:
            if request.method == Methods.PING:
                return EngineResponse.ok(request.id, result={"pong": True})

            if request.method == Methods.ENGINE_INFO:
                return EngineResponse.ok(request.id, result=self.info())

            if request.method == Methods.VERSE_CHECK:
                p = request.params
                verse = p["verse"]
                findings = self.check_verse(
                    project_id=p.get("projectId", ""),
                    lang_code=p.get("langCode", ""),
                    ref=verse["ref"],
                    text=verse["text"],
                    checks=p.get("checks", ["wildebeest"]),
                )
                return EngineResponse.ok(request.id, findings=findings)

            return EngineResponse.fail(
                request.id, "unknown_method", f"No handler for method '{request.method}'"
            )
        except Exception as exc:  # noqa: BLE001 - protocol boundary must never crash the sidecar
            return EngineResponse.fail(request.id, "internal_error", str(exc))
