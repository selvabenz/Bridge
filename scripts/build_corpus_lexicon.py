"""Build a pack's general-corpus lexicon: `general_corpus.tsv.gz` + `general_corpus.json`.

    python scripts/build_corpus_lexicon.py <code> [--sample-bytes N] [--windows N] [--floor F]
        [--cap N] [--cache-dir DIR] [--offline] [--verify]

The lexicon says how often a word form occurs in general written use of the
language, outside the Bible. Language QA consults it after the vendored
checker (tc_ai_bridge/language_packs/general_corpus.py): a word the OV never
used but the general corpus knows is not a misspelling; a near-miss from a
corpus word is a better suggestion than none; a word the IRV repeats but no
corpus knows, one edit from a corpus word, is an IRV-wide slip.

Sources, all compatible with Bridge's GPL-3 (fork issue #246):

- AI4Bharat IndicCorp v2 (CC0): raw text on Hugging Face, 2-80 GB per
  language. `--windows` evenly spaced byte ranges totalling `--sample-bytes`
  are read by HTTP Range request, tokenised with the profile's own `token_re`
  and counted. A single prefix of a crawl shard is one or two sites in crawl
  order; spaced windows are no dearer and far more representative.
- Tamil: KaniyamFoundation/all_tamil_words (public domain), `word,count`
  rows per source site, read from the tar without extracting. A member whose
  top rows are letter pairs (a broken tokenisation) is skipped.
- Hindi: Shreeshrii/hindi-hunspell `hi_IN.txt` (GPL-3), membership only.

The count of a row is its IndicCorp sample count when the word reached the
floor there; a word vouched for only by a list gets exactly `acceptMin`, so it
is known but never offered as a suggestion (suggestions need `suggestMin`).
Keys are NFC, then the profile's `canon` where the profile looks words up by
one (hi, ml, or): the runtime folds the same way, and the manifest records the
exact tokeniser so a re-vendor that changes it fails the pack's data test.

This runs at development time only. The app never fetches anything: the files
it reads are the two written here, shipped as pack data outside the exe.
Downloads live under `--cache-dir` (default %LOCALAPPDATA%/Bridge/dev-cache/
corpus), outside the repo; they are data, never executed or imported.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import statistics
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge.language_packs import indic_qa_vendor  # noqa: E402

PACKS = REPO / "engine" / "language_packs"
FORMAT = 1
FILE_NAME, MANIFEST_NAME = "general_corpus.tsv.gz", "general_corpus.json"

HF_REPO = "ai4bharat/IndicCorpV2"
HF_REVISION = "2d7285e6ce14fdb3fb2449c9f89427b9f582ac3f"
HF_FILES = {"ta": "data/ta.txt", "ml": "data/ml.txt", "hi": "data/hi-1.txt", "pa": "data/pa.txt", "or": "data/or.txt"}
HF_LICENCE = "CC0-1.0"

KANIYAM_REPO = "KaniyamFoundation/all_tamil_words"
KANIYAM_COMMIT = "a8eeb4b0cea35c515f17224e669c774d535fed46"
KANIYAM_FILE = "words_and_frequency.tar.bz2"
KANIYAM_LICENCE = "public domain"
KANIYAM_CAP_BYTES = 200 * 1024 * 1024

HUNSPELL_REPO = "Shreeshrii/hindi-hunspell"
HUNSPELL_COMMIT = "a44828e5dc4d49f344c87187ec401f9a4bb56965"
HUNSPELL_FILE = "Hindi/hi_IN.txt"
HUNSPELL_LICENCE = "GPL-3.0-only"

DEFAULTS = {
    "sample_bytes": 1_500_000_000, "windows": 8, "floor": 10, "cap": 300_000,
    "accept_min": 10, "suggest_min": 200, "suggest_top": 15_000, "ratio": 50, "kaniyam_floor": 20,
}
CHUNK = 8 * 1024 * 1024
NET_CHUNK = 1024 * 1024
MAX_TOKEN_CHARS = 40
USER_AGENT = "bridge-build-corpus-lexicon/1 (+https://github.com/selvabenz/Bridge)"
CODES = tuple(HF_FILES)


# --------------------------------------------------------------------------------------
# the profile's tokeniser
# --------------------------------------------------------------------------------------

def tokenizer(code: str) -> tuple[Callable[[str], list[str]], Callable[[str], str], dict]:
    """(findall, key, manifest entry) for a language: the vendored profile's
    `token_re` and, for hi/ml/or, its `canon`; NFC only for pa and ta."""
    lang = indic_qa_vendor.modules().langs.get(code)
    canon = getattr(indic_qa_vendor.profile(code), "canon", None)
    vendored = json.loads((indic_qa_vendor.vendor_root() / "VENDORED.json").read_text(encoding="utf-8"))
    nfc = lambda w: unicodedata.normalize("NFC", w)  # noqa: E731
    key = (lambda w: canon(nfc(w))) if canon else nfc
    entry = {"indicQaCommit": vendored["commit"], "tokenRe": lang.token_re.pattern,
             "canon": f"{code}.canon" if canon else "nfc"}
    return lang.token_re.findall, key, entry


# --------------------------------------------------------------------------------------
# fetching (development time only)
# --------------------------------------------------------------------------------------

def _open(url: str, headers: dict[str, str] | None = None, timeout: float = 120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout)


def hf_file_meta(path: str, *, repo: str = HF_REPO, revision: str = HF_REVISION) -> dict:
    """size and sha256 (the LFS oid) of one file, from the tree listing."""
    folder = path.rsplit("/", 1)[0]
    with _open(f"https://huggingface.co/api/datasets/{repo}/tree/{revision}/{folder}") as response:
        rows = json.loads(response.read().decode("utf-8"))
    for row in rows:
        if row.get("path") == path:
            return {"size": int(row["size"]), "sha256": (row.get("lfs") or {}).get("oid")}
    raise SystemExit(f"{path} is not in {repo}@{revision[:7]}")


def hf_url(path: str, *, repo: str = HF_REPO, revision: str = HF_REVISION) -> str:
    return f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"


def window_ranges(size: int, sample_bytes: int, windows: int) -> list[tuple[int, int]]:
    """`windows` evenly spaced [start, end) byte ranges summing to at most
    `sample_bytes`; one range of the whole file when the file is smaller."""
    if sample_bytes >= size or windows < 1:
        return [(0, size)]
    width = sample_bytes // max(1, windows)
    if windows == 1:
        return [(0, width)]
    step = (size - width) // (windows - 1)
    return [(i * step, i * step + width) for i in range(windows)]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_range(url: str, start: int, end: int, dest: Path, *, offline: bool = False,
                opener: Callable = _open) -> str:
    """Bytes [start, end) of `url` into `dest`, resuming a partial file, and the
    sha256 of the result. The server must honour the range (206 with a
    matching Content-Range); a 200 would be the whole file and is refused."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.is_file() else 0
    wanted = end - start
    if have > wanted:
        dest.unlink()
        have = 0
    if have < wanted:
        if offline:
            raise SystemExit(f"--offline, but {dest.name} needs {wanted - have} more bytes from {url}")
        with opener(url, {"Range": f"bytes={start + have}-{end - 1}"}, 600) as response:
            if response.status != 206:
                raise SystemExit(f"{url}: the server answered {response.status}, not 206, to a Range request")
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {start + have}-"):
                raise SystemExit(f"{url}: Content-Range {content_range!r} does not start at {start + have}")
            with dest.open("ab") as handle:
                while True:
                    block = response.read(NET_CHUNK)
                    if not block:
                        break
                    handle.write(block)
        have = dest.stat().st_size
        if have != wanted:
            raise SystemExit(f"{dest.name}: {have} bytes after the fetch, {wanted} expected; run again to resume")
    return sha256_of(dest)


def download(url: str, dest: Path, *, offline: bool = False, opener: Callable = _open) -> str:
    """A whole small file, kept once; its sha256."""
    if not dest.is_file():
        if offline:
            raise SystemExit(f"--offline, but {dest.name} is not cached ({url})")
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        with opener(url, None, 600) as response, partial.open("wb") as handle:
            for block in iter(lambda: response.read(NET_CHUNK), b""):
                handle.write(block)
        partial.replace(dest)
    return sha256_of(dest)


# --------------------------------------------------------------------------------------
# counting
# --------------------------------------------------------------------------------------

def count_tokens(path: Path, findall: Callable[[str], list[str]], *, first_line_partial: bool,
                 last_line_partial: bool) -> tuple[Counter, int, int]:
    """Token counts over a text window. A line cut by the window's edge is
    dropped (the first when the window does not start the file, the last when
    it does not end it): a cut line is a cut word. Returns (counts, bytes
    counted, tokens counted)."""
    counts: Counter = Counter()
    tokens = used = 0
    carry = b""
    skipping = first_line_partial
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            data = carry + block
            if skipping:
                cut = data.find(b"\n")
                if cut < 0:
                    carry = b""
                    continue
                data = data[cut + 1:]
                skipping = False
            last = data.rfind(b"\n")
            if last < 0:
                carry = data
                continue
            whole, carry = data[:last + 1], data[last + 1:]
            found = findall(unicodedata.normalize("NFC", whole.decode("utf-8", "replace")))
            counts.update(tok for tok in found if len(tok) <= MAX_TOKEN_CHARS)
            tokens += len(found)
            used += len(whole)
    if carry and not last_line_partial and not skipping:
        found = findall(unicodedata.normalize("NFC", carry.decode("utf-8", "replace")))
        counts.update(tok for tok in found if len(tok) <= MAX_TOKEN_CHARS)
        tokens += len(found)
        used += len(carry)
    return counts, used, tokens


def fold_keys(counts: Counter, key: Callable[[str], str]) -> Counter:
    """Counts by lookup key: applied once per distinct form, not per token."""
    folded: Counter = Counter()
    for word, n in counts.items():
        folded[key(word)] += n
    return folded


def _member_looks_like_words(rows: list[tuple[str, int]]) -> bool:
    """A member whose top rows are two-letter fragments is a broken
    tokenisation of its source, not a word list."""
    lengths = [len(word) for word, _ in rows[:20]]
    return bool(lengths) and statistics.median(lengths) >= 3


def kaniyam_counts(tar_path: Path, findall: Callable[[str], list[str]], *, floor: int,
                   cap_bytes: int = KANIYAM_CAP_BYTES) -> tuple[Counter, dict]:
    """`word,count` rows of every member, summed per NFC word that is one whole
    token, floored. Members are streamed with `extractfile`, never extracted to
    disk, and the decompressed bytes read are capped."""
    counts: Counter = Counter()
    report = {"members": [], "skipped": []}
    read = 0
    with tarfile.open(tar_path, "r:bz2") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if read + member.size > cap_bytes:
                raise ValueError(f"{tar_path.name}: more than {cap_bytes} bytes of members; refusing to read on")
            stream = archive.extractfile(member)
            if stream is None:
                continue
            rows: list[tuple[str, int]] = []
            for raw in io.TextIOWrapper(stream, encoding="utf-8", errors="replace"):
                word, sep, number = raw.rstrip("\r\n").rpartition(",")
                if not sep or not number.strip().isdigit():
                    continue
                word = unicodedata.normalize("NFC", word.strip())
                if len(word) > MAX_TOKEN_CHARS:
                    continue
                rows.append((word, int(number)))
            read += member.size
            if not _member_looks_like_words(rows):
                report["skipped"].append(member.name)
                continue
            kept = 0
            for word, n in rows:
                if findall(word) == [word]:
                    counts[word] += n
                    kept += 1
            report["members"].append({"name": member.name, "rows": len(rows), "kept": kept})
    return Counter({w: n for w, n in counts.items() if n >= floor}), report


def hunspell_words(path: Path, findall: Callable[[str], list[str]]) -> set[str]:
    """One word per line, a `/flags` suffix dropped, whole tokens only."""
    words: set[str] = set()
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            word = unicodedata.normalize("NFC", line.split("/", 1)[0].strip())
            if word and len(word) <= MAX_TOKEN_CHARS and findall(word) == [word]:
                words.add(word)
    return words


def merge(indic: Counter, lists: dict[str, Iterable[str]], *, floor: int, accept_min: int,
          cap: int) -> list[tuple[str, int, str]]:
    """Rows (key, count, src). A word's count is its IndicCorp count when that
    reached the floor; a word only a list vouches for gets `accept_min`."""
    rows: dict[str, tuple[int, set[str]]] = {}
    for word, n in indic.items():
        if n >= floor:
            rows[word] = (n, {"c"})
    for letter, words in lists.items():
        for word in words:
            n, src = rows.get(word, (0, set()))
            rows[word] = (n if n >= floor else accept_min, src | {letter})
    ordered = sorted(((w, n, "".join(sorted(src))) for w, (n, src) in rows.items()),
                     key=lambda row: (-row[1], row[0]))
    return ordered[:cap]


# --------------------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------------------

def write_tsv_gz(path: Path, rows: Iterable[tuple[str, int, str]]) -> None:
    """Deterministic: identical rows, identical bytes (gzip with no file name
    and mtime 0), so a rebuild is diffable by hash."""
    text = "#key\tcount\tsrc\n" + "".join(f"{w}\t{n}\t{src}\n" for w, n, src in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle, gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0,
                                                   compresslevel=9) as stream:
        stream.write(text.encode("utf-8"))


def read_tsv_gz(path: Path) -> list[tuple[str, int, str]]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            word, n, src = line.rstrip("\n").split("\t")
            rows.append((word, int(n), src))
    return rows


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def verify(code: str) -> int:
    """The shipped file matches its manifest and the profile's tokeniser."""
    folder = PACKS / f"{code}-irv"
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    problems = []
    actual = sha256_of(folder / FILE_NAME)
    if actual != manifest.get("outputSha256"):
        problems.append(f"{FILE_NAME} sha256 {actual[:12]} != manifest {str(manifest.get('outputSha256'))[:12]}")
    _findall, _key, entry = tokenizer(code)
    for field in ("tokenRe", "canon"):
        if manifest.get("tokenizer", {}).get(field) != entry[field]:
            problems.append(f"tokenizer.{field} differs from the vendored profile's")
    rows = read_tsv_gz(folder / FILE_NAME)
    if len(rows) != manifest.get("listed"):
        problems.append(f"{len(rows)} rows, manifest says {manifest.get('listed')}")
    for problem in problems:
        print(f"{code}: {problem}")
    print(f"{code}: {'ok' if not problems else 'FAILED'} ({len(rows)} rows)")
    return 1 if problems else 0


# --------------------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------------------

def build(code: str, *, cache_dir: Path, sample_bytes: int, windows: int, floor: int, cap: int,
          accept_min: int, suggest_min: int, suggest_top: int, ratio: int, kaniyam_floor: int,
          offline: bool, kaniyam_path: Path | None = None, hunspell_path: Path | None = None) -> dict:
    findall, key, tok_entry = tokenizer(code)
    folder = PACKS / f"{code}-irv"
    if not folder.is_dir():
        raise SystemExit(f"no pack folder {folder}")
    started = time.monotonic()
    sources: list[dict] = []

    # IndicCorp v2: spaced windows by HTTP Range.
    path = HF_FILES[code]
    meta = hf_file_meta(path) if not offline else None
    size = meta["size"] if meta else None
    cache = cache_dir / code / "indiccorp"
    if size is None:
        sizes = sorted(cache.glob("*.size"))
        if not sizes:
            raise SystemExit("--offline needs the file size recorded by an earlier online run")
        size = int(sizes[0].read_text())
    else:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{Path(path).name}.size").write_text(str(size))
    counts: Counter = Counter()
    ranges = []
    tokens_total = used_total = 0
    for start, end in window_ranges(size, sample_bytes, windows):
        dest = cache / f"{Path(path).name}.{start}-{end}.part"
        digest = fetch_range(hf_url(path), start, end, dest, offline=offline)
        window_counts, used, tokens = count_tokens(
            dest, findall, first_line_partial=start > 0, last_line_partial=end < size)
        counts.update(window_counts)
        ranges.append({"start": start, "end": end, "sha256": digest, "bytesUsed": used, "tokens": tokens})
        tokens_total += tokens
        used_total += used
        print(f"{code}: window {start}-{end}: {tokens:,} tokens, {len(window_counts):,} distinct", flush=True)
    indic = fold_keys(counts, key)
    sources.append({
        "name": "IndicCorpV2", "url": hf_url(path), "repoCommit": HF_REVISION,
        "fileSha256": meta["sha256"] if meta else None, "fileBytes": size, "ranges": ranges,
        "bytesUsed": used_total, "tokens": tokens_total, "distinct": len(indic),
        "licence": HF_LICENCE, "src": "c",
    })

    lists: dict[str, Iterable[str]] = {}
    if code == "ta":
        url = f"https://raw.githubusercontent.com/{KANIYAM_REPO}/{KANIYAM_COMMIT}/{KANIYAM_FILE}"
        tar = kaniyam_path or (cache_dir / code / "kaniyam" / KANIYAM_FILE)
        digest = download(url, tar, offline=offline)
        kaniyam, report = kaniyam_counts(tar, findall, floor=kaniyam_floor)
        folded = fold_keys(kaniyam, key)
        lists["k"] = folded
        sources.append({"name": "all_tamil_words", "url": url, "repoCommit": KANIYAM_COMMIT, "sha256": digest,
                        "licence": KANIYAM_LICENCE, "floor": kaniyam_floor, "kept": len(folded), "src": "k",
                        "members": report["members"], "skippedMembers": report["skipped"]})
    if code == "hi":
        url = f"https://raw.githubusercontent.com/{HUNSPELL_REPO}/{HUNSPELL_COMMIT}/{HUNSPELL_FILE}"
        txt = hunspell_path or (cache_dir / code / "hunspell" / Path(HUNSPELL_FILE).name)
        digest = download(url, txt, offline=offline)
        words = {key(w) for w in hunspell_words(txt, findall)}
        lists["h"] = words
        sources.append({"name": "hindi-hunspell", "url": url, "repoCommit": HUNSPELL_COMMIT, "sha256": digest,
                        "licence": HUNSPELL_LICENCE, "kept": len(words), "src": "h"})

    rows = merge(indic, lists, floor=floor, accept_min=accept_min, cap=cap)
    write_tsv_gz(folder / FILE_NAME, rows)
    manifest = {
        "format": FORMAT, "language": code, "file": FILE_NAME, "tokenizer": tok_entry, "sources": sources,
        "floor": floor, "cap": cap, "acceptMin": accept_min, "suggestMin": suggest_min,
        "suggestTop": suggest_top, "ratio": ratio, "listed": len(rows),
        "outputSha256": sha256_of(folder / FILE_NAME), "outputBytes": (folder / FILE_NAME).stat().st_size,
        "builtOn": date.today().isoformat(), "buildSeconds": round(time.monotonic() - started, 1),
        "builder": "scripts/build_corpus_lexicon.py",
    }
    write_json(folder / MANIFEST_NAME, manifest)
    print(f"{code}: {len(rows):,} rows, {manifest['outputBytes']:,} bytes gz, "
          f"{manifest['buildSeconds']} s; distinct in sample {len(indic):,}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("code", choices=CODES)
    parser.add_argument("--sample-bytes", type=int, default=DEFAULTS["sample_bytes"])
    parser.add_argument("--windows", type=int, default=DEFAULTS["windows"])
    parser.add_argument("--floor", type=int, default=DEFAULTS["floor"])
    parser.add_argument("--cap", type=int, default=DEFAULTS["cap"])
    parser.add_argument("--accept-min", type=int, default=DEFAULTS["accept_min"])
    parser.add_argument("--suggest-min", type=int, default=DEFAULTS["suggest_min"])
    parser.add_argument("--suggest-top", type=int, default=DEFAULTS["suggest_top"])
    parser.add_argument("--ratio", type=int, default=DEFAULTS["ratio"])
    parser.add_argument("--kaniyam-floor", type=int, default=DEFAULTS["kaniyam_floor"])
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Bridge" / "dev-cache" / "corpus")
    parser.add_argument("--kaniyam-path", type=Path, help="a local copy of the Tamil tar.bz2")
    parser.add_argument("--hunspell-path", type=Path, help="a local copy of hi_IN.txt")
    parser.add_argument("--offline", action="store_true", help="use only what the cache holds")
    parser.add_argument("--verify", action="store_true", help="check the shipped file against its manifest")
    args = parser.parse_args(argv)
    if args.verify:
        return verify(args.code)
    build(args.code, cache_dir=args.cache_dir, sample_bytes=args.sample_bytes, windows=args.windows,
          floor=args.floor, cap=args.cap, accept_min=args.accept_min, suggest_min=args.suggest_min,
          suggest_top=args.suggest_top, ratio=args.ratio, kaniyam_floor=args.kaniyam_floor,
          offline=args.offline, kaniyam_path=args.kaniyam_path, hunspell_path=args.hunspell_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
