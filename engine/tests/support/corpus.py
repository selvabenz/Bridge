"""A synthetic general-corpus lexicon for tests (#246).

Shared here so no test module imports another (CLAUDE.md, #74)."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

from tc_ai_bridge.language_packs import general_corpus, indic_qa_vendor, loader
from tc_ai_bridge.language_packs.registry import packs_dir


def write_corpus(folder: Path, counts: dict[str, int], *, code: str, accept_min: int = 10,
                 suggest_min: int = 200, suggest_top: int = 15_000, ratio: int = 50) -> Path:
    """`general_corpus.tsv.gz` + its manifest in `folder`, keys as given (the
    caller passes canonical keys), counts as given."""
    rows = sorted(counts.items(), key=lambda row: (-row[1], row[0]))
    path = folder / general_corpus.FILE_NAME
    text = "#key\tcount\tsrc\n" + "".join(f"{word}\t{n}\tc\n" for word, n in rows)
    with gzip.open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    lang = indic_qa_vendor.modules().langs.get(code)
    manifest = {
        "format": general_corpus.FORMAT, "language": code, "file": general_corpus.FILE_NAME,
        "tokenizer": {"indicQaCommit": "test", "tokenRe": lang.token_re.pattern,
                      "canon": f"{code}.canon" if hasattr(indic_qa_vendor.profile(code), "canon") else "nfc"},
        "sources": [{"name": "synthetic", "licence": "test"}],
        "floor": accept_min, "cap": len(rows), "acceptMin": accept_min, "suggestMin": suggest_min,
        "suggestTop": suggest_top, "ratio": ratio, "listed": len(rows),
        "outputSha256": hashlib.sha256(path.read_bytes()).hexdigest(), "builtOn": "2026-01-01",
    }
    (folder / general_corpus.MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    return path


def pack_with_corpus(tmp_path: Path, name: str, counts: dict[str, int], **thresholds) -> loader.RulePack:
    """A copy of a shipped pack with this synthetic corpus switched on, loaded
    from its own folder. The copy carries the pack's dictionary and IRV
    snapshot; a corpus the pack already ships is replaced."""
    source = packs_dir() / name
    folder = tmp_path / name
    shutil.copytree(source, folder, ignore=shutil.ignore_patterns(general_corpus.FILE_NAME,
                                                                  general_corpus.MANIFEST_NAME))
    meta = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
    code = str(meta.get("profile") or (meta.get("indicQa") or {}).get("profile") or meta["language"])
    write_corpus(folder, counts, code=code, **thresholds)
    meta[general_corpus.PACK_KEY] = {"file": general_corpus.FILE_NAME, "manifest": general_corpus.MANIFEST_NAME,
                                     "enabled": True}
    (folder / "pack.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return loader.load_pack(directory=folder)
