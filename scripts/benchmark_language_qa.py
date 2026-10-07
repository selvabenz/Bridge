"""Reproducible read-only Language QA timing and source/frozen protocol smoke.

Creates only a throwaway project. No translator data or installed app is used.

Also measures the layered-rules brief's performance contract: the p95
latency of foreground RPCs while a Language QA background scan runs.

- Gated (--gate): the RPCs Language QA owns or runs beside. These are
  ping, verse.get, verse.decide on a Language QA finding, languageQa.status
  and languageQa.inline. Each must stay under --p95-budget-ms (default 50).
- Reported, gated only with --gate-all: the shared write paths, which are
  slow with or without a scan (measured 2026-09-24, see BUILD_LOG). These are
  verse.decide on another finding (the progress rollup), verse.edit (the
  journalled Scripture write) and project.open.
- checks.status needs a running check job; it is measured once Language QA
  is a check stage (Phase 4).

--cores N pins the engine to N CPU cores (Windows), the brief's low-end
profile. RAM and disk throttling are NOT emulated. --without-scan repeats
the samples with Language QA paused, so the scan's own cost can be seen.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge.language_packs import default_pack
from tc_ai_bridge.language_qa import scan_text

GATED = ("ping", "verse.get", "verse.decide (languageQa)", "languageQa.status", "languageQa.inline")
LANGUAGE_QA_ISSUE = {"source": "languageQa", "rule": "tamil.vallinam-missing"}


def pin_to_cores(process: subprocess.Popen, cores: int) -> bool:
    """Restrict a child process to its first `cores` CPUs. Windows only."""
    if os.name != "nt" or cores <= 0:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    return bool(kernel32.SetProcessAffinityMask(int(process._handle), (1 << cores) - 1))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def summary(samples: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    return {name: {"samples": len(v), "p50Ms": round(percentile(v, .5), 2),
                   "p95Ms": round(percentile(v, .95), 2), "maxMs": round(max(v), 2)}
            for name, v in samples.items() if v}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, help="Frozen executable; default runs source")
    parser.add_argument("--cores", type=int, default=0, help="pin the engine to this many CPU cores (Windows)")
    parser.add_argument("--p95-budget-ms", type=float, default=50.0)
    parser.add_argument("--gate", action="store_true", help="exit 1 if a gated RPC's p95 exceeds the budget")
    parser.add_argument("--gate-all", action="store_true", help="also gate the shared write paths")
    parser.add_argument("--without-scan", action="store_true", help="also sample with Language QA paused")
    parser.add_argument("--language", choices=("ta", "hi"), default="ta",
                        help="hi: a Hindi project, so the indic-qa profile pack's book step runs beside "
                             "the sampled requests (one reviewed misspelling per verse)")
    args = parser.parse_args()
    if args.language == "hi":
        text = ("आदि में परमेश्वर ने आकाश और पृथ्वी की सृष्टि की। उसकी आयु सताईस वर्ष की थी। " * 2).strip()
        declared = {"id": "hin", "name": "Hindi"}
        measurements = {}
    else:
        text = ("ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார். " * 4).strip()
        declared = {"id": "tam", "name": "Tamil"}
        timings = []
        for _ in range(1000):
            start = time.perf_counter()
            assert not scan_text(text, book="php", chapter="1", verse="1", pack=default_pack("ta-irv"))["findings"]
            timings.append((time.perf_counter() - start) * 1000)
        measurements = {"pureScanMedianMs": statistics.median(timings),
                        "pureScanP95Ms": sorted(timings)[949], "pureScanMaxMs": max(timings)}
    with tempfile.TemporaryDirectory(prefix="bridge-language-qa-") as temp:
        root = Path(temp)
        project = root / "project"
        book_dir = project / "php"
        alignment_dir = project / ".apps" / "translationCore" / "alignmentData" / "php"
        book_dir.mkdir(parents=True)
        alignment_dir.mkdir(parents=True)
        (project / "manifest.json").write_text(json.dumps({
            "project": {"id": "php", "name": "Philippians"},
            "target_language": declared, "tc_version": "8",
        }), encoding="utf-8")
        # 400 synthetic verses, enough to keep the default yielding worker busy.
        for chapter in range(1, 5):
            verses = {str(v): text for v in range(1, 101)}
            if chapter == 1:
                verses["3-4"] = "தமிழ் �"
            (book_dir / f"{chapter}.json").write_text(json.dumps(verses, ensure_ascii=False), encoding="utf-8")
            (alignment_dir / f"{chapter}.json").write_text(json.dumps({
                verse: {"alignments": [], "wordBank": []} for verse in verses
            }), encoding="utf-8")
        original = {p: p.read_bytes() for p in book_dir.glob("*.json")}
        env = {**os.environ, "LOCALAPPDATA": str(root / "app-data"),
               "BRIDGE_SEMANTIC_SOURCE_DB": str(root / "absent.sqlite")}
        command = [str(args.engine.resolve())] if args.engine else [sys.executable, str(REPO / "engine" / "main.py")]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        pinned = pin_to_cores(process, args.cores)
        frames: queue.Queue[dict] = queue.Queue()

        def read_frames():
            for line in process.stdout:
                frames.put(json.loads(line))

        def drain_errors():
            for _ in process.stderr:
                pass

        threading.Thread(target=read_frames, daemon=True).start()
        threading.Thread(target=drain_errors, daemon=True).start()
        sequence = 0

        def request(method, params=None):
            nonlocal sequence
            sequence += 1
            request_id = str(sequence)
            process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                frame = frames.get(timeout=max(.01, deadline - time.monotonic()))
                if frame.get("id") == request_id:
                    assert frame["success"], frame
                    return frame["result"]
            raise AssertionError(f"Timeout: {method}")

        def timed(samples, name, method, params=None):
            before = time.perf_counter()
            result = request(method, params)
            samples.setdefault(name, []).append((time.perf_counter() - before) * 1000)
            return result

        def foreground_round(samples, tag, *, writes=True):
            """One round of the RPCs a translator makes. Writes restart the
            scan, so they are sampled a bounded number of times, and 2:7 is
            edited an even number of times so it ends with its original text."""
            path = {"projectPath": str(project)}
            for index in range(10):
                timed(samples, "verse.decide (languageQa)", "verse.decide", {
                    "chapter": "1", "verse": "5", "findingId": f"{tag}-lqa-{index}", "status": "ignored",
                    "issue": LANGUAGE_QA_ISSUE})
                timed(samples, "verse.get", "verse.get", {"chapter": "3", "verse": "10"})
                timed(samples, "languageQa.status", "languageQa.status", {**path, "limit": 50})
                timed(samples, "languageQa.inline", "languageQa.inline", {**path, "chapter": "1"})
            if not writes:
                return
            for index in range(10):
                timed(samples, "verse.decide (other)", "verse.decide", {
                    "chapter": "1", "verse": "5", "findingId": f"{tag}-gr-{index}", "status": "ignored"})
            for index in range(6):
                timed(samples, "verse.edit", "verse.edit", {
                    "chapter": "2", "verse": "7", "newText": text if index % 2 else text + " திருத்தம்"})
            for _ in range(3):
                timed(samples, "project.open", "project.open", {"path": str(project)})

        during: dict[str, list[float]] = {}
        quiet: dict[str, list[float]] = {}
        try:
            request("project.open", {"path": str(project)})
            foreground_round(during, "scan")
            latencies = during.setdefault("ping", [])
            started = time.monotonic()
            while time.monotonic() - started < 45:
                before = time.perf_counter()
                assert request("ping") == {"pong": True}
                latencies.append((time.perf_counter() - before) * 1000)
                timed(during, "verse.get", "verse.get", {"chapter": "3", "verse": "10"})
                timed(during, "languageQa.inline", "languageQa.inline",
                      {"projectPath": str(project), "chapter": "1"})
                status = request("languageQa.status", {"projectPath": str(project), "limit": 100})
                if status["state"] == "completed":
                    break
                assert status["state"] != "failed", status
                time.sleep(.1)
            assert status["state"] == "completed", status
            if args.language == "hi":
                # The book step reports the misspelling in every verse; the cap is 3,000.
                assert status["language"]["pack"] == "hi-irv", status
                assert any(f["ruleId"] == "hi-irv/hi.lex.known-misspelling" for f in status["findings"]), status
            else:
                assert status["totalFindings"] == 1, status
                assert status["findings"][0]["verse"] == "3-4", status
                assert status["language"]["pack"] == "ta-irv", status
            for p, raw in original.items():
                if p.name == "2.json":  # edited and edited back; the engine formats JSON its own way
                    assert json.loads(p.read_text(encoding="utf-8")) == json.loads(raw.decode("utf-8")), p
                else:
                    assert p.read_bytes() == raw, p
            assert max(latencies) < 250, latencies
            measurements.update(mode="frozen" if args.engine else "source",
                                backgroundSeconds=status["elapsedSeconds"],
                                pingSamples=len(latencies), pingMedianMs=statistics.median(latencies),
                                pingMaxMs=max(latencies), checkedVerses=status["checkedVerses"])
            paused = request("languageQa.pause", {"projectPath": str(project), "paused": True})
            assert paused["state"] == "paused"
            if args.without_scan:
                for _ in range(20):
                    timed(quiet, "ping", "ping")
                # project.open comes last in the round and re-binds (unpauses)
                # Language QA, so everything before it runs with no scan.
                foreground_round(quiet, "quiet")
                request("languageQa.pause", {"projectPath": str(project), "paused": True})
            request("verse.edit", {"chapter": "1", "verse": "3-4", "newText": "தமிழ் உரை"})
            assert request("languageQa.status", {"projectPath": str(project)})["state"] == "paused"
            request("languageQa.pause", {"projectPath": str(project), "paused": False})
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                status = request("languageQa.status", {"projectPath": str(project), "limit": 10})
                if status["state"] == "completed":
                    break
                time.sleep(.2)
            # Tamil: the one finding (verse 3-4) is edited away. Hindi: every verse
            # still carries its misspelling; only the pass completing matters.
            assert status["state"] == "completed", status
            assert args.language == "hi" or status["totalFindings"] == 0, status
            measurements["editRecheckReusedChapters"] = status["reusedChapters"]
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        measurements["pinnedCores"] = args.cores if pinned else None
        measurements["foregroundDuringScan"] = summary(during)
        if quiet:
            measurements["foregroundWithoutScan"] = summary(quiet)
        print(json.dumps(measurements, indent=2))
        gated = set(measurements["foregroundDuringScan"]) if args.gate_all else set(GATED)
        over = {name: v["p95Ms"] for name, v in measurements["foregroundDuringScan"].items()
                if name in gated and v["p95Ms"] > args.p95_budget_ms}
        if args.gate or args.gate_all:
            for name, p95 in over.items():
                print(f"GATE: {name} p95 {p95} ms > {args.p95_budget_ms} ms", file=sys.stderr)
            print("gate: " + ("FAIL" if over else "pass"), file=sys.stderr)
            return 1 if over else 0
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
