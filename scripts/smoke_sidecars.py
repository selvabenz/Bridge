"""Packaged sidecar smoke test.

Build both executables first, then pass the frozen bridge-engine path.  The
test creates a temporary translationCore-shaped project whose source USFM has
balanced markers but duplicate/missing verses, so the legacy USFM_BALANCE
regex cannot make this test pass accidentally.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture_project(root: Path) -> Path:
    project = root / "titus"
    _write_json(project / "manifest.json", {
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "eng", "name": "English"},
        "tc_version": "8",
    })
    _write_json(project / "tit" / "1.json", {
        "1": "ପ୍ରଥମ ପଦ।",
        "3": "ତୃତୀୟ ପଦ।",
    })
    _write_json(project / "tit" / "2.json", {
        "1": "Paul sent greetings to Titus his true son.",
        "4": "Titus is a beloved child in the common faith.",
        "7": "In everything show yourself an example, Tituss my son.",
    })
    _write_json(project / ".apps" / "translationCore" / "alignmentData" / "tit" / "1.json", {
        "1": {
            "alignments": [
                {
                    "topWords": [
                        {
                            "word": "Παῦλος", "strong": "G39720", "lemma": "Παῦλος",
                            "morph": "Gr,N,,,,,NMS,", "occurrence": 1, "occurrences": 1,
                        },
                    ],
                    "bottomWords": [
                        {"word": "ପ୍ରଥମ", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                    ],
                },
                {
                    "topWords": [
                        {
                            "word": "δοῦλος", "strong": "G14010", "lemma": "δοῦλος",
                            "morph": "Gr,N,,,,,NMS,", "occurrence": 1, "occurrences": 1,
                        },
                    ],
                    "bottomWords": [],
                },
            ],
            "wordBank": [
                {"word": "ପଦ", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
            ],
        },
        "3": {
            "alignments": [{
                "topWords": [{
                    "word": "λόγος", "strong": "G30560", "lemma": "λόγος",
                    "morph": "Gr,N,,,,,NMS,", "occurrence": 1, "occurrences": 1,
                }],
                "bottomWords": [
                    {"word": "ତୃତୀୟ", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                    {"word": "ପଦ", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                ],
            }],
            "wordBank": [],
        },
    })
    _write_json(project / ".apps" / "translationCore" / "alignmentData" / "tit" / "2.json", {
        "1": {"alignments": [], "wordBank": []},
        "4": {"alignments": [], "wordBank": []},
        "7": {"alignments": [], "wordBank": []},
    })
    (project / "tit.usfm").write_text(
        "\\id TIT\n\\h Titus\n\\toc1 The Letter to Titus\n\\c 1\n\\p\n"
        "\\v 1 ପ୍ରଥମ ପଦ।\n"
        "\\v 1 ନକଲ ପଦ ସଂଖ୍ୟା।\n"
        "\\v 3 ତୃତୀୟ ପଦ; ଦ୍ୱିତୀୟ ପଦ ନାହିଁ।\n",
        encoding="utf-8",
    )
    return project


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", type=Path, help="Path to frozen bridge-engine executable")
    parser.add_argument("--import-source", type=Path, help="Optional real USFM/Paratext folder to benchmark")
    # 15 s, not the 10 s it was before #91: the frozen 66-book Tamil IRV import
    # measured 10.2-10.7 s once usfmtc decides verse boundaries (main was 9.4-9.8 s).
    # The +0.8 s is `import usfmtc` ~0.1 s, identifying 66 preambles ~0.3 s and
    # the first book's full parse ~0.4 s -- real work, not a regression to chase.
    parser.add_argument("--max-import-seconds", type=float, default=15.0)
    args = parser.parse_args()
    engine = args.engine.resolve()
    repository_root = Path(__file__).resolve().parent.parent
    expected_bridge_version = str(json.loads(
        (repository_root / "package.json").read_text(encoding="utf-8")
    )["version"])
    extension = engine.suffix if sys.platform == "win32" else ""
    helper = engine.with_name(f"bridge-usfm-checker{extension}")
    if not engine.is_file() or not helper.is_file():
        raise SystemExit(f"Expected sibling executables: {engine} and {helper}")

    version = subprocess.run(
        [str(helper), "--version"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    if version.returncode != 0 or "vendored-18ddcf0" not in version.stdout:
        raise SystemExit(f"Helper health check failed: {version.stderr or version.stdout}")

    # The bundled tN/tW/tA/UHB/UGNT snapshot no longer lives inside
    # bridge-engine.spec's onefile archive (see that file's own comment) —
    # in production Tauri passes --resources-dir, resolved from
    # bundle.resources; here it's the repo's own engine/resources, the same
    # source build-sidecars.ps1 copies from. Without this the frozen exe
    # under test would correctly (by design) report every original-language
    # resource as unavailable, which would look like this smoke test
    # catching a real regression when it's actually just an incomplete
    # invocation.
    resources_dir = repository_root / "engine" / "resources"
    if not resources_dir.is_dir():
        raise SystemExit(f"Expected bundled resources at {resources_dir} for this smoke test")
    # The Language QA packs ship the same way (--language-packs-dir).
    packs_dir = repository_root / "engine" / "language_packs"
    if not (packs_dir / "index.json").is_file():
        raise SystemExit(f"Expected the Language QA packs at {packs_dir} for this smoke test")

    with tempfile.TemporaryDirectory(prefix="bridge-frozen-smoke-") as temp:
        project = _fixture_project(Path(temp))
        process_env = os.environ.copy()
        process_env["LOCALAPPDATA"] = str(Path(temp) / "app-data")
        process = subprocess.Popen(
            [str(engine), "--resources-dir", str(resources_dir), "--language-packs-dir", str(packs_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=process_env,
        )
        frames: queue.Queue[dict] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    frames.put(json.loads(line))
                except json.JSONDecodeError:
                    continue

        threading.Thread(target=read_stdout, daemon=True).start()

        # stderr must be drained too: it is a pipe, and once its buffer (a few
        # KB on Windows) is full the engine blocks on its next diagnostic line,
        # mid-request. The desktop shell reads it continuously (sidecar.rs);
        # the timing traces (2026-10-08) made this harness the first to fill it.
        stderr_tail: "collections.deque[str]" = collections.deque(maxlen=200)

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_tail.append(line.rstrip("\n"))

        threading.Thread(target=read_stderr, daemon=True).start()

        def request(request_id: str, method: str, params: dict, timeout: float = 30) -> dict:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    response = frames.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if response.get("id") == request_id:
                    return response
            tail = "\n".join(list(stderr_tail)[-5:])
            raise SystemExit(f"Request {request_id} timed out" + (f"; engine stderr ends:\n{tail}" if tail else ""))

        try:
            # Boot first, on its own clock: request() would otherwise count the
            # onefile unpack and imports against the first request (#242).
            boot_started = time.monotonic()
            while True:
                try:
                    frame = frames.get(timeout=max(0.01, 60 - (time.monotonic() - boot_started)))
                except queue.Empty:
                    raise SystemExit("Frozen engine never reported ready")
                if frame.get("id") == "__ready__":
                    break
            print(f"Frozen engine ready in {time.monotonic() - boot_started:.2f}s.")
            info_started = time.monotonic()
            info = request("info", "engine.info", {})
            info_seconds = time.monotonic() - info_started
            if not info.get("success"):
                raise SystemExit(f"Request info failed: {info}")
            # engine.info used to build uroman on the dispatcher, ~5 s with
            # nothing else answered; it now loads on a background thread (#242).
            if info_seconds >= 2.0:
                raise SystemExit(f"engine.info took {info_seconds:.2f}s; it must not wait for uroman")
            print(f"engine.info answered in {info_seconds * 1000:.0f}ms.")
            if info.get("result", {}).get("bridgeVersion") != expected_bridge_version:
                raise SystemExit(f"Frozen engine version is stale or inconsistent: {info}")
            wildebeest = (
                info.get("result", {})
                .get("greekRoom", {})
                .get("adapters", {})
                .get("wildebeest", {})
            )
            if not wildebeest.get("usingRealEngine"):
                raise SystemExit(
                    "Frozen engine is using the Wildebeest mock fallback: "
                    f"{wildebeest}"
                )
            names = (
                info.get("result", {})
                .get("greekRoom", {})
                .get("adapters", {})
                .get("names", {})
            )
            if not names.get("available") or not names.get("usingRealEngine"):
                raise SystemExit(
                    "Frozen engine is missing real Uroman/Smart Edit Distance: "
                    f"{names}"
                )

            raw_source = Path(temp) / "57-TIT.usfm"
            raw_source.write_text(
                # A heading and a mid-line \v: both are decided by usfmtc
                # (tc_ai_bridge/usfm_parser.py, #91), whose pure-Python grammar
                # is exactly what a freeze can drop without failing to build.
                "\\id TIT\n\\h Titus\n\\c 1\n\\v 1 Paul, a servant of God.\n"
                # A character style in verse 2: the frozen exe must hand the
                # reader clean display text with the style as a span (#91 Phase 2).
                "\\s The work in Crete\n\\q1 \\v 2 In hope of \\nd eternal\\nd* life.\n",
                encoding="utf-8",
            )
            raw_import = request("original-language-import", "project.import", {
                "path": str(raw_source),
                "destinationRoot": str(Path(temp) / "original-language-projects"),
                "metadata": {
                    "languageId": "eng",
                    "languageName": "English",
                    "languageDirection": "ltr",
                    "projectName": "Frozen original-language smoke",
                    "bibleName": "Test Bible",
                },
            })
            original_resource = raw_import.get("result", {}).get("originalLanguageResource", {})
            if (
                not raw_import.get("success")
                or original_resource.get("resourceId") != "ugnt"
                or original_resource.get("version") != "0.34"
                or original_resource.get("commit") != "fc95b2b8aad08bb65ab54628ab685413a1139e97"
            ):
                raise SystemExit(f"Frozen UGNT resource provenance failed: {raw_import}")
            parsed_chapter = request("usfm-parser-verse-data", "chapter.verseData", {"chapter": "1"})
            parsed_result = parsed_chapter.get("result", {})
            parsed_verses = {
                verse: (item or {}).get("text")
                for verse, item in (parsed_result.get("verses") or {}).items()
            }
            display_two = ((parsed_result.get("verses") or {}).get("2") or {}).get("display") or {}
            if (
                not parsed_chapter.get("success")
                or parsed_verses.get("1") != "Paul, a servant of God."
                or parsed_verses.get("2") != "In hope of \\nd eternal\\nd* life."
                or display_two.get("plain") != "In hope of eternal life."
                or [s.get("marker") for s in display_two.get("styles") or []] != ["nd"]
                or (parsed_result.get("headings") or {}).get("2", [{}])[0].get("text") != "The work in Crete"
            ):
                raise SystemExit(f"Frozen USFM parser did not bound verses and headings: {parsed_chapter}")
            raw_alignment = request(
                "original-language-alignment", "alignment.get", {"chapter": "1", "verse": "1"},
            )
            raw_top_tokens = raw_alignment.get("result", {}).get("topTokens", [])
            if (
                not raw_alignment.get("success")
                or not raw_alignment.get("result", {}).get("sourceAvailable")
                or len(raw_top_tokens) != 17
                or raw_top_tokens[0].get("word") != "Παῦλος"
            ):
                raise SystemExit(f"Frozen UGNT token initialization failed: {raw_alignment}")
            # #219/#221: the automatic alignment modules are new top-level and
            # tc_ai_bridge imports a freeze could drop without failing to
            # build. Both calls are offline: the estimate builds real window
            # payloads, and a pass with no key must answer `unavailable`
            # without sending anything.
            estimate = request("auto-align-estimate", "alignment.autoAlign.estimate", {
                "scope": "chapter", "chapters": ["1"],
            })
            if (
                not estimate.get("success")
                or estimate.get("result", {}).get("windows") != 1
                or estimate.get("result", {}).get("calls") != 2
                or not estimate.get("result", {}).get("estimatedInputTokens")
            ):
                raise SystemExit(f"Frozen automatic-alignment estimate failed: {estimate}")
            offline_pass = request("auto-align-offline", "alignment.window.autoAlign", {
                "chapter": "1", "verses": ["1", "2"],
            })
            if (offline_pass.get("result", {}).get("unavailable") or {}).get("reason") != "no-api-key":
                raise SystemExit(f"Frozen automatic alignment did not degrade offline: {offline_pass}")

            if args.import_source:
                started_at = time.perf_counter()
                imported = request("import", "project.import", {
                    "path": str(args.import_source.resolve()),
                    "destinationRoot": str(Path(temp) / "imported"),
                    "metadata": {
                        "languageId": "tam",
                        "languageName": "Tamil",
                        "languageDirection": "ltr",
                        "projectName": "Frozen import benchmark",
                        "bibleName": "Tamil Bible",
                    },
                }, timeout=max(30.0, args.max_import_seconds + 10.0))
                elapsed = time.perf_counter() - started_at
                if not imported.get("success"):
                    raise SystemExit(f"Frozen import failed: {imported}")
                projects = imported.get("result", {}).get("importedProjects", [])
                if len(projects) != 66:
                    raise SystemExit(f"Frozen import returned {len(projects)} projects, expected 66")
                if elapsed >= args.max_import_seconds:
                    raise SystemExit(
                        f"Frozen import took {elapsed:.2f}s, limit is {args.max_import_seconds:.2f}s "
                        "(the budget includes ~0.8 s of usfmtc work on the first book and 66 "
                        "preambles, #91; a miss beyond that is a regression)"
                    )
                print(f"Frozen 66-book import passed in {elapsed:.2f}s.")

            opened = request("open", "project.open", {"path": str(project)})
            if not opened.get("success"):
                raise SystemExit(f"Request open failed: {opened}")
            project_id = opened.get("result", {}).get("projectId")
            if not project_id:
                raise SystemExit(f"Frozen project.open did not assign a stable project id: {opened}")
            listed = request("project-list", "project.list", {})
            registered = listed.get("result", {}).get("projects", [])
            # Compare resolved paths on both sides: the registry always
            # canonicalizes (project_registry.canonical_path_key), while
            # `project` here is whatever tempfile.TemporaryDirectory() and
            # %TEMP% happened to hand back — on a machine where %TEMP%
            # itself is the short 8.3 form (common Windows default when a
            # username contains a space), that's a different string from
            # the resolved long form even though it's the same directory.
            if not listed.get("success") or not any(
                item.get("projectId") == project_id and Path(item.get("path", "")) == project.resolve()
                for item in registered
            ):
                raise SystemExit(f"Frozen project registry did not return the opened project: {listed}")
            duplicate = request("project-duplicate", "project.inspectImport", {"path": str(project)})
            assessment = duplicate.get("result", {}).get("duplicates", {})
            if (
                assessment.get("classification") != "exactDuplicate"
                or assessment.get("inputBookCount") != 1
                or assessment.get("exactBookCount") != 1
                or assessment.get("missingExactBookCount") != 0
                or assessment.get("matchingGroupCount", 0) < 1
                or not assessment.get("exactMatchGroupId", "").startswith("project:")
                or not any(
                    match.get("match") == "exact" and match.get("reason") == "sourceFingerprint"
                    for match in assessment.get("matches", [])
                )
            ):
                raise SystemExit(f"Frozen duplicate classification failed: {duplicate}")

            names_check = request(
                "names-check",
                "verse.runChecks",
                {"chapter": "2", "verse": "7", "checks": ["names"]},
            )
            names_findings = names_check.get("findings", [])
            if (
                not names_check.get("success")
                or not any(
                    finding.get("engine") == "names"
                    and finding.get("original_text") == "Tituss"
                    and finding.get("suggested_replacement") == "Titus"
                    for finding in names_findings
                )
            ):
                raise SystemExit(
                    f"Frozen names/transliteration check missed Titus/Tituss: {names_check}"
                )
            names_state = (request("info-after-names", "engine.info", {}).get("result", {})
                           .get("greekRoom", {}).get("adapters", {}).get("names", {}))
            if names_state.get("state") != "loaded":
                raise SystemExit(f"Frozen uroman did not report loaded after the names check: {names_state}")

            alignment = request("alignment-get", "alignment.get", {"chapter": "1", "verse": "1"})
            if not alignment.get("success") or not alignment["result"].get("sourceAvailable"):
                raise SystemExit(f"Frozen alignment source was unavailable: {alignment}")
            context = alignment["result"]
            realigned = request("alignment-realign", "alignment.realign", {
                "chapter": "1", "verse": "1",
                "topIds": [token["id"] for token in context["topTokens"]],
                "bottomIds": [token["id"] for token in context["bottomTokens"]],
                "expectedOriginal": context["alignment"],
            })
            if not realigned.get("success") or not realigned["result"].get("canComplete"):
                raise SystemExit(f"Frozen many-to-many alignment failed: {realigned}")
            completed = request(
                "alignment-complete", "alignment.complete", {"chapter": "1", "verse": "1"},
            )
            if not completed.get("success") or completed["result"].get("completionState") != "completed":
                raise SystemExit(f"Frozen alignment completion failed: {completed}")

            # No API key is configured in this smoke test (no real network call is made or
            # needed) — this exists to confirm ai_client.py/alignment_engine.py's new imports
            # (apply_proposal, validate_preparation_proposal) and alignment_reliability.py are
            # actually bundled and importable inside the FROZEN executable, not just in source
            # mode. A missing/broken bundle would surface as an internal_error or a crash here,
            # not the expected clean ai_error.
            ai_propose = request(
                "ai-propose-no-key", "alignment.aiPropose", {"chapter": "1", "verse": "1"},
            )
            if ai_propose.get("success") or ai_propose.get("error", {}).get("code") != "ai_error":
                raise SystemExit(f"Frozen alignment.aiPropose did not fail cleanly with ai_error: {ai_propose}")

            # A developer machine may or may not have the companion running.
            # Either a well-formed live state or the connector's clean unavailable
            # error proves the frozen import/dispatch path is intact.
            paratext_state = request("paratext-state", "paratext.getState", {})
            if paratext_state.get("success"):
                paratext_result = paratext_state.get("result", {})
                if not paratext_result.get("connected") or not paratext_result.get("project_id"):
                    raise SystemExit(f"Frozen paratext.getState returned invalid live state: {paratext_state}")
            elif paratext_state.get("error", {}).get("code") != "paratext_connector_error":
                raise SystemExit(f"Frozen paratext.getState did not fail cleanly: {paratext_state}")

            # This actually spawns the bundled logos_bridge.ps1 from under sys._MEIPASS.
            # A developer machine may have no Logos COM API, an installed-but-closed
            # app, or a live panel, so validate the environment-safe response shape.
            logos_state = request("logos-state", "logos.getState", {}, timeout=25)
            if logos_state.get("success"):
                if not isinstance(logos_state.get("result", {}).get("connected"), bool):
                    raise SystemExit(f"Frozen logos.getState returned invalid live state: {logos_state}")
            elif logos_state.get("error", {}).get("code") != "logos_connector_error":
                raise SystemExit(f"Frozen logos.getState did not fail cleanly: {logos_state}")
            else:
                logos_message = logos_state["error"]["message"].lower()
                if "helper is missing" in logos_message or "helper did not start" in logos_message:
                    raise SystemExit(f"Frozen Logos helper was not bundled correctly: {logos_state}")
                if "logos4lib.logoslauncher" in logos_message:
                    raise SystemExit(f"Frozen Logos helper still uses the obsolete ProgID: {logos_state}")

            aligned_path = Path(temp) / "tit-aligned.usfm"
            exported = request(
                "alignment-export", "export.aligned", {"outputPath": str(aligned_path)},
            )
            if not exported.get("success") or "\\zaln-s" not in aligned_path.read_text(encoding="utf-8"):
                raise SystemExit(f"Frozen aligned-USFM export failed: {exported}")
            undone = request("alignment-undo", "alignment.undo", {
                "chapter": "1", "verse": "1",
                "expectedOriginal": completed["result"]["alignment"],
            })
            if not undone.get("success") or undone["result"].get("status") != "partial":
                raise SystemExit(f"Frozen alignment undo failed: {undone}")

            started = request("start", "checks.start", {
                "scope": "chapter", "chapters": ["1"], "checks": ["usfm"],
            })
            if not started.get("success"):
                raise SystemExit(f"Request start failed: {started}")
            job_id = started["result"]["jobId"]

            # Reproduce the desktop's first-open request order. ReviewPanel sends
            # a Greek-Room-only live check immediately after checks.start; if that
            # request waits for the tN/tW/USFM/names preparation lock, the
            # synchronous stdio loop cannot even read status/list requests behind
            # it. The deterministic source test holds that lock explicitly; this
            # frozen gate proves the release binary contains the non-blocking path.
            live_started = time.monotonic()
            live_review = request(
                "live-greek-room-while-checking", "verse.runChecks",
                {"chapter": "1", "verse": "1", "checks": ["greekroom"]}, timeout=5,
            )
            live_elapsed = time.monotonic() - live_started
            if not live_review.get("success") or live_elapsed >= 5:
                raise SystemExit(
                    "Frozen live Greek Room request blocked the dispatcher during preparation: "
                    f"elapsed={live_elapsed:.2f}s response={live_review}"
                )

            review_started = time.monotonic()
            review = request(
                "review-while-checking", "check.listForVerse",
                {"chapter": "1", "verse": "1"}, timeout=5,
            )
            review_elapsed = time.monotonic() - review_started
            review_state = review.get("result", {}).get("state")
            if (
                not review.get("success")
                or review_state not in {"ready", "preparing"}
                or review_elapsed >= 5
            ):
                raise SystemExit(
                    "Frozen review request blocked the dispatcher during checking: "
                    f"elapsed={review_elapsed:.2f}s response={review}"
                )
            responsive_status = request(
                "status-after-review", "checks.status", {"jobId": job_id}, timeout=5,
            )
            if not responsive_status.get("success"):
                raise SystemExit(
                    "Frozen status request was blocked behind translation-help loading: "
                    f"{responsive_status}"
                )

            snapshot = started["result"]
            deadline = time.monotonic() + 180
            attempt = 0
            while snapshot["state"] not in {"succeeded", "failed", "cancelled"}:
                if time.monotonic() >= deadline:
                    raise SystemExit(f"Frozen background job timed out: {snapshot}")
                time.sleep(0.1)
                attempt += 1
                status = request(f"status-{attempt}", "checks.status", {"jobId": job_id})
                if not status.get("success"):
                    raise SystemExit(f"Request status failed: {status}")
                snapshot = status["result"]

            if snapshot["state"] != "succeeded":
                raise SystemExit(f"Frozen background job failed: {snapshot}")
            check_types = {
                finding["check_type"]
                for result in snapshot["results"].values()
                for finding in result.get("findings", [])
            }
            if "usfm.duplicate_verse_number" not in check_types:
                raise SystemExit(f"Frozen checker missed duplicate verse: {sorted(check_types)}")
            if not any("missing_verses" in value for value in check_types):
                raise SystemExit(f"Frozen checker missed absent verse: {sorted(check_types)}")

            # The indic-qa profile packs: the vendored checker (inside the exe)
            # and a dictionary plus IRV snapshot (beside it, --language-packs-dir)
            # must both resolve in the frozen build. A Hindi project with one
            # reviewed misspelling must report it from hi-irv.
            hindi = Path(temp) / "hindi"
            _write_json(hindi / "manifest.json", {
                "project": {"id": "rut", "name": "Ruth"},
                "target_language": {"id": "hin", "name": "Hindi"}, "tc_version": "8",
            })
            _write_json(hindi / "rut" / "1.json", {"1": "उसकी आयु सताईस वर्ष की थी।"})
            _write_json(hindi / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json",
                        {"1": {"alignments": [], "wordBank": []}})
            (hindi / "rut.usfm").write_text("\\id RUT\n\\c 1\n\\v 1 उसकी आयु सताईस वर्ष की थी।\n",
                                            encoding="utf-8")
            opened_hindi = request("open-hindi", "project.open", {"path": str(hindi)})
            if not opened_hindi.get("success"):
                raise SystemExit(f"Frozen open of the Hindi project failed: {opened_hindi}")
            lqa_started = time.monotonic()
            lqa: dict = {}
            while time.monotonic() - lqa_started < 90:
                lqa = request(f"lqa-hindi-{int((time.monotonic() - lqa_started) * 10)}", "languageQa.status",
                              {"projectPath": str(hindi), "limit": 100}).get("result") or {}
                if lqa.get("state") in {"completed", "failed"}:
                    break
                time.sleep(0.2)
            lqa_rules = {f.get("ruleId") for f in lqa.get("findings", [])}
            if (lqa.get("state") != "completed" or lqa.get("language", {}).get("pack") != "hi-irv"
                    or "hi-irv/hi.lex.known-misspelling" not in lqa_rules):
                raise SystemExit(f"Frozen indic-qa Hindi pack did not report its finding: state={lqa.get('state')} "
                                 f"language={lqa.get('language')} rules={sorted(lqa_rules)} "
                                 f"limitations={lqa.get('limitations')}")
            print(f"Frozen indic-qa Hindi pack passed in {time.monotonic() - lqa_started:.2f}s.")

            # ta-irv's indic-qa layer: the Tamil profile over the OV dictionary,
            # beside the pack's own rules. GEN 1:27 as the IRV has it carries the
            # ஒற்று the reviewer's house style removes before தேவ- (no_theva); the
            # finding must arrive with the OV verse attached.
            tamil = Path(temp) / "tamil"
            tamil_text = "அவனைத் தேவசாயலாகவே சிருஷ்டித்தார்"
            _write_json(tamil / "manifest.json", {
                "project": {"id": "gen", "name": "Genesis"},
                "target_language": {"id": "tam", "name": "Tamil"}, "tc_version": "8",
            })
            _write_json(tamil / "gen" / "1.json", {"27": tamil_text})
            _write_json(tamil / ".apps" / "translationCore" / "alignmentData" / "gen" / "1.json",
                        {"27": {"alignments": [], "wordBank": []}})
            (tamil / "gen.usfm").write_text(f"\\id GEN\n\\c 1\n\\v 27 {tamil_text}\n", encoding="utf-8")
            opened_tamil = request("open-tamil", "project.open", {"path": str(tamil)})
            if not opened_tamil.get("success"):
                raise SystemExit(f"Frozen open of the Tamil project failed: {opened_tamil}")
            lqa_started = time.monotonic()
            lqa = {}
            while time.monotonic() - lqa_started < 90:
                lqa = request(f"lqa-tamil-{int((time.monotonic() - lqa_started) * 10)}", "languageQa.status",
                              {"projectPath": str(tamil), "limit": 100}).get("result") or {}
                if lqa.get("state") in {"completed", "failed"}:
                    break
                time.sleep(0.2)
            layer = [f for f in lqa.get("findings", []) if f.get("ruleId") == "ta-irv/indicqa.sandhi.extra"]
            if (lqa.get("state") != "completed" or lqa.get("language", {}).get("pack") != "ta-irv"
                    or not layer or (layer[0].get("reference") or {}).get("ref") != "GEN 1:27"):
                raise SystemExit(f"Frozen ta-irv indic-qa layer did not report its finding: state={lqa.get('state')} "
                                 f"rules={sorted({f.get('ruleId') for f in lqa.get('findings', [])})} "
                                 f"limitations={lqa.get('limitations')}")
            print(f"Frozen ta-irv indic-qa layer passed in {time.monotonic() - lqa_started:.2f}s.")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    print(
        "Frozen sidecar smoke test passed: real Wildebeest/Uroman loaded; "
        "pinned UGNT source tokens, versification, names/transliteration, "
        "alignment statistics/proposal packaging, "
        "AI explain packaging, desktop connectors, project registry/duplicate import, "
        "first-open live-review responsiveness, alignment/export/undo, "
        "duplicate/missing-verse checks, the indic-qa Hindi pack and ta-irv's indic-qa layer succeeded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
