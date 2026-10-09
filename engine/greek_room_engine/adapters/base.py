"""
Adapter interface.

Per the architecture doc (§3): "Our application should never directly
depend on Greek Room's internal module structure." Every checker is wrapped
in a thin adapter implementing this interface, so upstream Greek Room churn
(it's currently Alpha) never leaks into our app or protocol layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models.finding import QaFinding


class CheckAdapter(ABC):
    """One adapter per underlying engine (Wildebeest, OWL, USFM, ...)."""

    #: short identifier used in QaFinding.engine, e.g. "wildebeest"
    engine_name: str = "unknown"

    @abstractmethod
    def check_verse(self, *, project_id: str, lang_code: str, ref: str,
                     text: str, params: dict[str, Any]) -> list[QaFinding]:
        """Run this engine's checks against a single verse and return findings."""
        raise NotImplementedError

    def version(self) -> str:
        """Report the underlying engine's version for provenance/caching."""
        return "unknown"

    def is_available(self) -> bool:
        """Whether this adapter can currently produce findings (real engine
        or a mock fallback). If False, the engine skips this check rather
        than crashing the whole request.
        """
        return True

    def using_real_engine(self) -> bool:
        """Whether this adapter is backed by the real upstream package, as
        opposed to a mock fallback used for protocol/UI development.
        """
        return True

    def status(self) -> dict[str, Any]:
        """Anything else engine.info should say about this adapter (names:
        whether its tables are loaded yet). Must not do slow work."""
        return {}
