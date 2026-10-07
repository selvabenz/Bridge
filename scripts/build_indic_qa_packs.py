"""Write the Bridge-side files of the indic-qa packs: the profile packs (pa,
ml, hi, or) and ta-irv's indic-qa layer.

The dictionaries come from scripts/sync_indic_qa.py. This script writes what
Bridge adds around them, in engine/language_packs/<code>-irv/:

    pack.json            the pack manifest ("engine": "indic-qa")
    rule_versions.json   one entry per rule of the profile's RULES: Bridge
                         category, layer, severity, confidence, enabled,
                         inline, and the revision that expires old ignores
    irv_state.json.gz    the checker's index of the whole IRV, so a one-book
                         project sees IRV-wide word counts (--irv-dir)

and, for ta-irv (whose own rules come from scripts/build_ta_irv_pack.py):

    indic_qa_rules.json  Bridge's view of each indicqa.* rule of the layer
                         (indic_qa_tamil.RULES), same shape as rule_versions.json
    irv_state.json.gz    as above, with the ஒற்று pair counts, headings and
                         footnotes the layer also reads

Usage (from the repo root):

    python scripts/build_indic_qa_packs.py --rule-versions
    python scripts/build_indic_qa_packs.py --irv-state hi "D:\\Claude Lab\\IRV Hindi"
    python scripts/build_indic_qa_packs.py --ta-layer --irv-state ta "D:\\Claude Lab\\IRV Tamil"

--rule-versions (and --ta-layer) never changes an existing entry: it adds
entries for rules the profile gained (from indic_qa_adapter.default_entry,
default_layer_entry) and refuses when the profile lost a rule, naming it. Reviewed values are edited in the file by
hand, with a DECISIONS line when one becomes inline.

--irv-state imports every book of the folder through Bridge's own importer
(the same path a project takes), builds each one the way a Language QA pass
does, indexes it, and writes the snapshot deterministically. It records the
sha256 of every input file. Rebuild it when the IRV or the dictionary changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge.language_packs import indic_qa_adapter as adapter, indic_qa_tamil, indic_qa_vendor  # noqa: E402
from tc_ai_bridge.language_packs import loader  # noqa: E402
from tc_ai_bridge.language_qa import MAX_VERSE_CHARS, lift_inline_usfm  # noqa: E402
from tc_ai_bridge.language_qa_benchmark import book_verses  # noqa: E402
from tc_ai_bridge.project_import import imported_verse_text, parse_scripture_file  # noqa: E402

PACKS = REPO / "engine" / "language_packs"
NAMES = {"hi": "Hindi", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi"}
SCRIPTS = {"hi": "Deva", "ml": "Mlym", "or": "Orya", "pa": "Guru"}
PACK_VERSION = "1.0.0"


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def rule_versions() -> int:
    vendored = json.loads((indic_qa_vendor.vendor_root() / "VENDORED.json").read_text(encoding="utf-8"))
    for code in indic_qa_vendor.PROFILES:
        folder = PACKS / f"{code}-irv"
        profile = indic_qa_vendor.profile(code)
        pack_json = folder / "pack.json"
        if not pack_json.is_file():
            write_json(pack_json, {
                "pack": f"{code}-irv", "version": PACK_VERSION, "language": code, "script": SCRIPTS[code],
                "engine": "indic-qa", "profile": code, "dictionary": "dictionary", "rules": [],
                "description": f"{NAMES[code]} IRV checks from indic-qa's {code} profile "
                               f"(qa_app/langs/{indic_qa_vendor.modules().langs.MODULE.get(code, code)}.py, "
                               f"vendored at {vendored['commit'][:7]}) over its OV-built dictionary. "
                               f"See docs/LANGUAGE_QA_PACKS.md, \"Profile packs\".",
            })
            print(f"{code}: wrote pack.json")
        path = folder / adapter.RULE_VERSIONS
        current = json.loads(path.read_text(encoding="utf-8"))["rules"] if path.is_file() else {}
        lost = sorted(set(current) - set(profile.RULES))
        if lost:
            sys.exit(f"{code}: the profile no longer has {lost}; remove them from {path} by hand "
                     f"(their findings' decisions stop applying)")
        added = [rid for rid in profile.RULES if rid not in current]
        for rid in added:
            group, _label, _inline, on = profile.RULES[rid]
            current[rid] = adapter.default_entry(rid, group, on)
        write_json(path, {
            "description": "Bridge's view of each indic-qa rule. `revision` expires earlier ignores when "
                           "bumped; `inline` needs the human gate (DECISIONS 2026-09-28). Written by "
                           "scripts/build_indic_qa_packs.py --rule-versions, then reviewed by hand.",
            "rules": {rid: current[rid] for rid in profile.RULES},
        })
        print(f"{code}: {len(profile.RULES)} rules, {len(added)} added")
    return 0


def ta_layer() -> int:
    """Write (or extend) ta-irv's indic_qa_rules.json."""
    path = PACKS / "ta-irv" / indic_qa_tamil.RULES_FILE
    current = json.loads(path.read_text(encoding="utf-8"))["rules"] if path.is_file() else {}
    lost = sorted(set(current) - set(indic_qa_tamil.RULES))
    if lost:
        sys.exit(f"ta: the layer no longer has {lost}; remove them from {path} by hand "
                 f"(their findings' decisions stop applying)")
    added = [rid for rid in indic_qa_tamil.RULES if rid not in current]
    for rid in added:
        current[rid] = adapter.default_layer_entry(rid)
    write_json(path, {
        "description": "Bridge's view of each rule of ta-irv's indic-qa layer (indic_qa_tamil.py). `revision` "
                       "expires earlier ignores when bumped; `inline` needs the human gate (DECISIONS "
                       "2026-09-28). Written by scripts/build_indic_qa_packs.py --ta-layer, then reviewed by hand.",
        "rules": {rid: current[rid] for rid in indic_qa_tamil.RULES},
    })
    print(f"ta: {len(indic_qa_tamil.RULES)} layer rules, {len(added)} added")
    return 0


def irv_state(code: str, irv_dir: Path) -> int:
    layer = code in indic_qa_vendor.LAYER_PROFILES
    if layer:
        loader.default_pack.cache_clear()
        pack = loader.load_pack(f"{code}-irv")
        if not adapter.runs_checker(pack):
            sys.exit(f"{code}-irv: the indic-qa layer did not load: {pack.problems}")
    else:
        pack = adapter.build_pack(json.loads((PACKS / f"{code}-irv" / "pack.json").read_text(encoding="utf-8")),
                                  PACKS / f"{code}-irv")
    mods = indic_qa_vendor.modules()
    lang = mods.langs.get(code)
    checker = mods.checker.Checker(
        mods.checker.Lexicon.load(pack.directory / "dictionary", lang), adapter._settings(pack), lang)
    inputs = {}
    files = sorted(p for p in irv_dir.iterdir() if p.suffix.lower() in (".sfm", ".usfm"))
    if not files:
        sys.exit(f"no .SFM/.usfm files in {irv_dir}")
    for sfm in files:
        if layer:
            # The same book a layer's pass builds: headings and footnotes too.
            parsed = parse_scripture_file(sfm)
            book = parsed.book_id
            chapters = {chapter: {verse: imported_verse_text(text) for verse, text in verses.items()}
                        for chapter, verses in parsed.chapters.items()}
            synthetic, _refs, _notes = adapter.build_book(book, chapters, lift=lift_inline_usfm,
                                                          max_verse_chars=MAX_VERSE_CHARS,
                                                          headings=parsed.headings, footnotes=True)
        else:
            book, chapters = book_verses(sfm)
            synthetic, _refs, _notes = adapter.build_book(book, chapters, lift=lift_inline_usfm,
                                                          max_verse_chars=MAX_VERSE_CHARS)
        checker.index_book(adapter.book_key(book), synthetic)
        inputs[sfm.name] = hashlib.sha256(sfm.read_bytes()).hexdigest()
        print(f"  {book}: {sum(checker.book_count[adapter.book_key(book)].values())} tokens", flush=True)
    vendored = json.loads((indic_qa_vendor.vendor_root() / "VENDORED.json").read_text(encoding="utf-8"))
    manifest = json.loads((pack.directory / "dictionary" / "MANIFEST.json").read_text(encoding="utf-8"))
    out = pack.directory / adapter.IRV_STATE
    adapter.write_state(out, checker, {
        "irvFolder": irv_dir.name, "books": len(files), "inputs": inputs,
        "indicQaCommit": vendored["commit"], "dictionary": manifest["files"],
    })
    print(f"{code}: {out.relative_to(REPO)} {out.stat().st_size / 2**20:.2f} MB, {len(files)} books")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rule-versions", action="store_true", help="write pack.json and rule_versions.json")
    parser.add_argument("--ta-layer", action="store_true", help="write ta-irv's indic_qa_rules.json")
    parser.add_argument("--irv-state", nargs=2, metavar=("CODE", "IRV_DIR"),
                        help="build irv_state.json.gz for one language from its IRV folder")
    args = parser.parse_args()
    if not args.rule_versions and not args.irv_state and not args.ta_layer:
        parser.error("nothing to do: --rule-versions, --ta-layer and/or --irv-state")
    if args.rule_versions:
        rule_versions()
    if args.ta_layer:
        ta_layer()
    if args.irv_state:
        code, folder = args.irv_state
        if code not in indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES:
            parser.error(f"code must be one of {indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES}")
        irv_state(code, Path(folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
