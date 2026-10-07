"""Where the project's local data lives.

Everything the editor, the scripts and the tests read lives inside the repository:

    corpora/irv/<lang>/      the IRV books under review (the editor saves corrections here)
    corpora/ov/<lang>/       the Old Version books the dictionaries are built from (Tamil: usfm/)
    baselines/<lang>/        frozen outputs the regression tests compare against
    outputs/<lang>-irv/      reviewer workbooks, measurements and other generated files

`scripts/import_corpora.py` fills these folders.  They are not tracked by Git (see .gitignore).
Set INDIC_QA_HOME to keep them somewhere else; every path below then moves with it.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("INDIC_QA_HOME") or REPO_ROOT)

CORPORA = HOME / "corpora"
BASELINES = HOME / "baselines"
OUTPUTS = HOME / "outputs"


def irv_dir(code: str) -> Path:
    return CORPORA / "irv" / code


def ov_dir(code: str) -> Path:
    if code == "ta":
        return REPO_ROOT / "usfm"
    return CORPORA / "ov" / code


def baseline_dir(code: str) -> Path:
    return BASELINES / code


def output_dir(code: str) -> Path:
    return OUTPUTS / f"{code}-irv"
