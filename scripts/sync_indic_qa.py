"""Vendor indic-qa's checker core and its dictionaries into Bridge.

indic-qa (github.com/selvabenz/indic-qa) is a standalone IRV spell-check
editor. Bridge uses only its pure-stdlib checker core, in-process: as the
whole of Language QA for Punjabi, Malayalam, Hindi and Odia, and as a layer
of the ta-irv pack for Tamil (engine/vendor/indic-qa/NOTICE.md). This
script is the only way files enter or change under:

    engine/vendor/indic-qa/                 the checker code, byte-identical
    engine/language_packs/<x>-irv/dictionary/   the dictionary files it reads

Usage (from the repo root):

    python scripts/sync_indic_qa.py --source <indic-qa clone> --commit <sha>
    python scripts/sync_indic_qa.py --check      # the tree matches VENDORED.json

The copy is an allow-list: a file upstream adds is not taken until it is
named here, and a named file that is missing stops the sync. Every copied
file's sha256 goes into engine/vendor/indic-qa/VENDORED.json (code and
dictionaries both), and into each pack's dictionary/MANIFEST.json, which the
pack fingerprint reads. When a profile module changed, the script says so:
that is the prompt to bump the affected rules' `revision` in the pack's
rule_versions.json, which is what expires earlier "ignored" decisions.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "engine" / "vendor" / "indic-qa"
PACKS = REPO / "engine" / "language_packs"
UPSTREAM = "https://github.com/selvabenz/indic-qa"

# The checker core. ta.py and tamil_grammar.py are imported unconditionally by
# checker.py (and run the ta-irv pack's indic-qa layer), and usfm_doc.py
# imports scripts/build_dictionary.py at import time.
CODE = (
    "LICENSE",
    "qa_app/__init__.py", "qa_app/checker.py", "qa_app/kinds.py", "qa_app/usfm_doc.py",
    "qa_app/tamil_grammar.py", "qa_app/paths.py",
    "qa_app/langs/__init__.py", "qa_app/langs/ta.py",
    "qa_app/langs/hi.py", "qa_app/langs/hi_tables.py",
    "qa_app/langs/ml.py", "qa_app/langs/ml_tables.py",
    "qa_app/langs/pa.py", "qa_app/langs/pa_tables.py",
    "qa_app/langs/odia.py", "qa_app/langs/odia_tables.py",
    "scripts/build_dictionary.py",
)
# What Lexicon.load and each profile's load_lexicon_extra read, plus
# clusters.tsv (the IRV counts of each encoded form, for seeding) and
# build_info.json (provenance). Not taken: verses.tsv (only related.py reads
# it), REPORT.md and the build reports nothing reads at runtime.
COMMON_DICT = ("wordlist.txt", "words.tsv", "extra_words.txt", "known_misspellings.tsv",
               "archaic.tsv", "clusters.tsv", "build_info.json")
DICTIONARIES = {
    "hi": COMMON_DICT + ("names.tsv", "gender.tsv", "oblique.tsv"),
    "ml": COMMON_DICT + ("names.tsv", "suffixes.tsv", "stems.tsv"),
    "or": COMMON_DICT + ("names.tsv",),
    "pa": COMMON_DICT + ("gender.tsv", "oblique.tsv"),
    # Tamil: what Lexicon.load reads when the profile has sandhi (the two
    # wordlists, the OV sandhi pairs, the names with a real final consonant),
    # the reviewer's corrections and sandhi rule table, and verses.tsv, which
    # Bridge reads itself to show the OV verse beside a finding (ov_reference.py).
    "ta": ("wordlist.txt", "wordlist_bare.txt", "words.tsv", "sandhi_pairs.tsv", "final_consonant_words.tsv",
           "extra_words.txt", "corrections.tsv", "sandhi_rules.tsv", "verses.tsv", "build_info.json"),
}
# Where each language's dictionary is upstream: Tamil's is the original
# `dictionary/`, the others' `dictionary_<code>/`.
SOURCE_DIRS = {"ta": "dictionary"}
PROFILE_MODULES = {"hi": ("langs/hi.py", "langs/hi_tables.py"), "ml": ("langs/ml.py", "langs/ml_tables.py"),
                   "or": ("langs/odia.py", "langs/odia_tables.py"), "pa": ("langs/pa.py", "langs/pa_tables.py"),
                   "ta": ("langs/ta.py", "qa_app/tamil_grammar.py", "qa_app/checker.py")}
# Where each language's Bridge-side rule entries are, whose `revision` a
# profile change should prompt a review of.
RULE_FILES = {"ta": "indic_qa_rules.json"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def planned(source: Path | None) -> dict[str, tuple[Path | None, Path]]:
    """Repo-relative key -> (upstream file, destination)."""
    out: dict[str, tuple[Path | None, Path]] = {}
    for rel in CODE:
        dest = VENDOR / rel
        out[dest.relative_to(REPO).as_posix()] = (source / rel if source else None, dest)
    for code, files in DICTIONARIES.items():
        for name in files:
            dest = PACKS / f"{code}-irv" / "dictionary" / name
            folder = SOURCE_DIRS.get(code, f"dictionary_{code}")
            out[dest.relative_to(REPO).as_posix()] = (source / folder / name if source else None, dest)
    return out


def check() -> int:
    record = json.loads((VENDOR / "VENDORED.json").read_text(encoding="utf-8"))
    problems = []
    for key, digest in record["files"].items():
        path = REPO / key
        if not path.is_file():
            problems.append(f"missing: {key}")
        elif sha256(path) != digest:
            problems.append(f"changed: {key}")
    expected = set(planned(None))
    if set(record["files"]) != expected:
        problems.append(f"VENDORED.json lists {sorted(set(record['files']) ^ expected)} against the allow-list")
    for line in problems:
        print(line)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def sync(source: Path, commit: str) -> int:
    head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if not head.startswith(commit):
        sys.exit(f"{source} is at {head}, not {commit}; check out the commit to vendor first")
    plan = planned(source)
    # Bytes come from the commit itself, never the working tree: a checkout with
    # core.autocrlf rewrites LF to CRLF, and VENDORED.json's hashes must be the
    # same on every machine that runs --check.
    blobs: dict[str, bytes] = {}
    missing = []
    for key, (src, _) in plan.items():
        rel = src.relative_to(source).as_posix()
        shown = subprocess.run(["git", "-C", str(source), "show", f"{head}:{rel}"], capture_output=True)
        if shown.returncode:
            missing.append(rel)
        else:
            blobs[key] = shown.stdout
    if missing:
        sys.exit("upstream is missing allow-listed files:\n  " + "\n  ".join(missing))
    previous = {}
    record_path = VENDOR / "VENDORED.json"
    if record_path.is_file():
        previous = json.loads(record_path.read_text(encoding="utf-8")).get("files", {})
    files = {}
    for key, (src, dest) in plan.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blobs[key])  # bytes as committed upstream: no line-ending change
        files[key] = sha256(dest)
    for code in DICTIONARIES:
        folder = PACKS / f"{code}-irv" / "dictionary"
        manifest = {name: files[(folder / name).relative_to(REPO).as_posix()] for name in DICTIONARIES[code]}
        (folder / "MANIFEST.json").write_text(json.dumps({"commit": head, "files": manifest}, indent=1,
                                                         sort_keys=True) + "\n", encoding="utf-8")
    record_path.write_text(json.dumps({
        "upstream": UPSTREAM, "commit": head,
        "fetched": datetime.date.today().isoformat(),
        "files": dict(sorted(files.items())),
    }, indent=1) + "\n", encoding="utf-8")
    changed = sorted(k for k, v in files.items() if previous and previous.get(k) != v)
    print(f"vendored {len(files)} files from {head[:7]}")
    if previous:
        print("changed since the last sync:" if changed else "nothing changed since the last sync")
        for key in changed:
            print("  " + key)
        for code, modules in PROFILE_MODULES.items():
            if any(k.endswith(f"/{m}") for k in changed for m in modules):
                print(f"  -> {code}: a profile module changed; review engine/language_packs/{code}-irv/"
                      f"{RULE_FILES.get(code, 'rule_versions.json')} and bump the `revision` of every rule "
                      f"whose matching changed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, help="an indic-qa clone at --commit")
    parser.add_argument("--commit", help="the upstream commit to vendor (full or abbreviated sha)")
    parser.add_argument("--check", action="store_true", help="only verify the tree against VENDORED.json")
    args = parser.parse_args()
    if args.check:
        return check()
    if not args.source or not args.commit:
        parser.error("--source and --commit are required unless --check")
    return sync(args.source.resolve(), args.commit)


if __name__ == "__main__":
    raise SystemExit(main())
