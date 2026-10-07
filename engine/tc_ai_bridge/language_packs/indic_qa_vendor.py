"""Where the vendored indic-qa checker lives, and importing it.

indic-qa's checker core (engine/vendor/indic-qa/, see its NOTICE.md) is a
library imported into the long-lived engine, like the vendored
versification module, not a helper subprocess like the USFM checker. Its
modules import each other as the top-level package `qa_app`, and
`qa_app.usfm_doc` puts `<vendor>/scripts` on sys.path to import
`build_dictionary`, so the vendor root itself must be on sys.path.

Imported on first need only: nothing here runs at engine start-up.
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

# The non-Tamil profiles Bridge runs, by language code. Tamil is Bridge's own
# ta-irv pack; indic-qa's Tamil profile is imported (checker.py needs it) but
# never used.
PROFILES = ("hi", "ml", "or", "pa")

_lock = threading.Lock()
_modules: SimpleNamespace | None = None


def vendor_root() -> Path:
    """The vendored tree: under sys._MEIPASS in a frozen build (bridge-engine.spec
    extracts it there), else beside this source tree."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "vendor" / "indic-qa"
    return Path(__file__).resolve().parents[2] / "vendor" / "indic-qa"


def modules() -> SimpleNamespace:
    """`checker`, `usfm_doc`, `kinds` and `langs` from the vendored package,
    imported once. Raises ImportError when the tree is missing."""
    global _modules
    if _modules is not None:
        return _modules
    with _lock:
        if _modules is None:
            root = vendor_root()
            if not (root / "qa_app" / "checker.py").is_file():
                raise ImportError(f"indic-qa is not vendored at {root}")
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from qa_app import checker, kinds, langs, usfm_doc  # noqa: PLC0415 (vendored, on demand)
            _modules = SimpleNamespace(checker=checker, usfm_doc=usfm_doc, kinds=kinds, langs=langs)
    return _modules


def profile(code: str) -> ModuleType:
    """The profile module for a language code (`or` lives in `odia.py`)."""
    if code not in PROFILES:
        raise KeyError(f"no indic-qa profile is run for {code!r}; known: {', '.join(PROFILES)}")
    langs = modules().langs
    return importlib.import_module(f"{langs.__name__}.{langs.MODULE.get(code, code)}")
