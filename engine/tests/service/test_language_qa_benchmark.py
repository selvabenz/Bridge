"""The Phase 2 benchmark harness, on a synthetic book and review report so it
runs anywhere (the real IRV text and reports are outside the repository)."""
import csv
import json
import subprocess
import sys

import pytest

from tc_ai_bridge import language_qa_benchmark as bench
from tests.support.paths import REPO_ROOT

COLUMNS = ["Book", "Chapter", "Verse", "Issue Type", "Priority Category", "Severity", "Confidence",
           "Original Tamil", "Suggested Correction", "Explanation", "Source / Reference Note",
           "Reviewer Decision Needed", "Status"]

SFM = "\n".join([
    "\\id RUT synthetic benchmark book", "\\c 1", "\\p",
    "\\v 1 அவன் அந்த காகம் பார்த்தான்.",       # vallinam, flagged by the review -> TP
    "\\v 2 அவள் அந்த பெண் வந்தாள்.",            # vallinam, not in the review -> FP
    "\\v 3 அவன் இந்த கல்லை எடுத்தான்.",          # vallinam on a Pass 3 house form (இந்த + bare), no row -> FP, house form
    "\\v 4 அவன் இந்த பட்டணம் போனான்.",          # vallinam; the row flagging it is house form -> maybe
    "\\v 5 அவர் அந்த சபையைக் கண்டார்.",          # vallinam; a human rejected the AI row -> maybe
    "\\v 7 அவன் அந்த தேவன் வந்தார்.",           # the pack abstains: house style, no doubling before தேவ- -> no finding
    "\\v 6 இது முழவதும் சரி.",                   # typo row the engine cannot find -> FN
    "",
])


@pytest.fixture(autouse=True)
def without_corpus_lexicon(monkeypatch):
    """The harness is tested on a synthetic book; the real corpus lexicon would
    add findings on its made-up words that these counts do not expect."""
    from tests.support.packs import switch_off_ta_lexicon
    switch_off_ta_lexicon(monkeypatch)


def review_rows():
    def row(chapter, verse, kind, original, fix, status="Open", explanation="AI proposal"):
        return {"Book": "Ruth", "Chapter": chapter, "Verse": verse, "Issue Type": kind, "Priority Category": "",
                "Severity": "Medium", "Confidence": "Medium", "Original Tamil": original,
                "Suggested Correction": fix, "Explanation": explanation, "Source / Reference Note": "",
                "Reviewer Decision Needed": "", "Status": status}
    return [
        row("1", "1", "Sandhi / word-joining", "அந்த காகம்", "அந்தக் காகம்"),
        row("1", "4", "Sandhi / word-joining", "இந்த பட்டணம்", "இந்தப் பட்டணம்"),
        row("1", "5", "Sandhi / word-joining", "அந்த சபையை", "அந்தச் சபையை", status="Rejected"),
        row("1", "6", "Confirmed typo", "முழவதும்", "முழுவதும்"),
        row("1", "6", "Source comparison", "இது", "அது"),                     # out of scope
        row("1", "6", "Punctuation", "சரி.", "சரி;", explanation="Comma splice policy"),  # policy, out
        row("1", "2", "Punctuation", "வந்தாள் .", "வந்தாள்.", explanation="Space before full stop"),
        row("Intro", "—", "Confirmed typo", "x", "y"),                        # skipped
    ]


def write_inputs(tmp_path, rows):
    irv = tmp_path / "irv"
    irv.mkdir()
    (irv / "08RUTIRVTam.SFM").write_text(SFM, encoding="utf-8")
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    with (reviews / "RUT_Round2_Proofreading_Issues.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return irv, reviews


@pytest.fixture
def benchmark(tmp_path):
    irv, reviews = write_inputs(tmp_path, review_rows())
    rows = bench.load_review_rows([reviews / "RUT_Round2_Proofreading_Issues.csv"])
    book, chapters = bench.book_verses(irv / "08RUTIRVTam.SFM")
    scans = {book: bench.scan_book(book, chapters)}
    return rows, bench.score(rows, scans, {book: chapters}), {book: chapters}


def test_rows_are_labelled_by_scope_verdict_and_contradiction(benchmark):
    rows, _, _ = benchmark
    labels = {(r.chapter, r.verse, r.issue_type): (r.label, r.reason) for r in rows}
    assert all(r.book == "rut" for r in rows)
    assert labels[("1", "1", "Sandhi / word-joining")][0] == "positive"
    assert labels[("1", "4", "Sandhi / word-joining")] == (
        "maybe", "Pass 3 confirmed house form: இந்த + bare hard consonant")
    # A human verdict against the AI proposal is a contradiction, not ground truth.
    assert labels[("1", "5", "Sandhi / word-joining")] == (
        "maybe", "human verdict: Rejected (contradicts the AI proposal)")
    assert labels[("1", "6", "Source comparison")][0] == "out-of-scope"
    assert labels[("1", "6", "Punctuation")] == ("out-of-scope", "punctuation policy, not spacing")
    assert labels[("1", "2", "Punctuation")][0] == "positive"
    assert labels[("Intro", "—", "Confirmed typo")][0] == "skipped"


def test_contradicting_rows_become_maybe():
    def row(original, fix, verse="1", bucket="typo"):
        return bench.ReviewRow(source="x", family="Round2", book="gen", chapter="1", verse=verse,
                               issue_type="Possible typo", bucket=bucket, severity="", confidence="",
                               original=original, suggestion=fix, explanation="", status="Open")
    different = [row("அவன்", "அவர்"), row("அவன்", "அவள்")]
    reversal = [row("மாலை வரை", "மாலைவரை", verse="2"), row("மாலைவரை", "மாலை வரை", verse="2")]
    digits = [row("12 பேர்", "பன்னிரண்டு பேர்", verse="3"), row("4. வது", "4 வது", verse="4")]
    rows = different + reversal + digits
    bench.classify(rows)
    assert [r.label for r in different] == ["maybe", "maybe"]
    assert [r.label for r in reversal] == ["maybe", "maybe"]
    # Writing out numerals contradicts the digits house form; fixing a stray stop does not.
    assert [r.label for r in digits] == ["maybe", "positive"]


def test_findings_are_scored_against_compatible_rows_only(benchmark):
    _, result, _ = benchmark
    vallinam = result["rules"]["ta-irv/sandhi.vallinam.demonstrative"]
    assert vallinam["findings"] == 5
    assert vallinam["tp_strict"] == 1                 # 1:1
    assert vallinam["matched_maybe"] == 2             # 1:4 house form, 1:5 human-rejected: strict FP, lenient TP
    assert vallinam["matched_negative"] == 0
    assert vallinam["fp_strict"] == 4 and vallinam["tp_lenient"] == 3
    assert vallinam["fp_house_form"] == 1             # 1:3 இந்த + bare (1:7 அந்த தேவ- is abstained by the pack)
    assert vallinam["precision_strict"] == 0.2 and vallinam["precision_lenient"] == 0.6
    assert vallinam["inline"] is True  # 24 labelled at 100% over both rounds (2026-09-29)
    # The spacing row at 1:2 is not matched by the vallinam finding at the same verse.
    assert result["buckets"]["punctuation"]["found_positive"] == 0


def test_recall_counts_only_anchored_rows(benchmark):
    _, result, _ = benchmark
    sandhi = result["buckets"]["sandhi"]
    assert (sandhi["rows_positive"], sandhi["rows_maybe"], sandhi["rows_negative"]) == (1, 2, 0)
    assert sandhi["recall_strict"] == 1.0 and sandhi["recall_lenient"] == 1.0
    typo = result["buckets"]["typo"]
    assert typo["anchored_positive"] == 1 and typo["found_positive"] == 0 and typo["recall_strict"] == 0.0
    assert [r["original"] for r in result["unmatchedRows"] if r["bucket"] == "typo"] == ["முழவதும்"]
    punctuation = result["buckets"]["punctuation"]
    assert punctuation["unanchored"] == 1  # "வந்தாள் ." is not in the verse text
    assert result["outOfScope"] == {"Source comparison": 1, "Punctuation": 1}


def test_unmatched_findings_are_listed_for_a_human_to_label(benchmark):
    _, result, _ = benchmark
    unmatched = {(f["verse"], f["originalText"]): f for f in result["unmatchedFindings"]}
    assert set(unmatched) == {("2", "அந்த பெண்"), ("3", "இந்த கல்லை")}
    assert unmatched[("3", "இந்த கல்லை")]["houseForm"] == "இந்த + bare hard consonant"
    assert unmatched[("2", "அந்த பெண்")]["houseForm"] is None


def test_the_ai_agreement_gate_only_guards_regression(benchmark):
    """AI agreement is a lower bound, so it never decides inline (DECISIONS.md
    2026-09-28): an inline rule at 20% passes it; only a drop fails."""
    _, result, _ = benchmark
    rule = result["rules"]["ta-irv/sandhi.vallinam.demonstrative"]
    rule["inline"] = True
    assert rule["precision_strict"] == 0.2 and "signOff" not in rule
    assert bench.gate(result, None) == []
    baseline = {"rules": {"ta-irv/sandhi.vallinam.demonstrative": {"precision_strict": 0.5, "findings": 12}}}
    assert bench.gate(result, baseline) == []  # 5 findings now: too few to compare a drop
    rule["findings"] = 12
    dropped = bench.gate(result, baseline)
    assert dropped == ["ta-irv/sandhi.vallinam.demonstrative strict precision fell from 50.0% to 20.0%"]
    assert bench.gate(result, baseline, max_drop=0.5) == []


# ---- human labels -----------------------------------------------------------

def _human_inputs(benchmark):
    """Labels over the synthetic book's own findings, as the reviewer's
    converter writes them (flagged, abstained and word rows)."""
    _, _, verses = benchmark
    scans = {"rut": bench.scan_book("rut", verses["rut"])}
    at = {(f["verse"], f["originalText"]): f for f in scans["rut"]["findings"]
          if f["ruleId"] == "ta-irv/sandhi.vallinam.demonstrative"}

    def flagged(ident, verse, original, verdict, start=None):
        finding = at.get((verse, original))
        s = finding["start"] if start is None else start
        return {"id": ident, "kind": "flagged", "cls": "flagged", "rule": "ta-irv/sandhi.vallinam.demonstrative",
                "book": "rut", "ch": "1", "v": verse, "start": s, "end": s + len(original),
                "original": original, "verdict": verdict}

    text7 = verses["rut"]["1"]["7"]
    labels = [
        flagged("R1", "1", "அந்த காகம்", "TP"),
        flagged("R2", "2", "அந்த பெண்", "FP_NODOUBLE"),
        flagged("R3", "3", "இந்த கல்லை", "HOUSE"),
        flagged("R4", "4", "இந்த பட்டணம்", "UNSURE"),
        # v7: the pack abstains before தேவ- (house style), so there is no
        # finding there; this synthetic reviewer says doubling was required.
        {"id": "R5", "kind": "flagged", "cls": "flagged", "rule": "ta-irv/sandhi.vallinam.demonstrative",
         "book": "rut", "ch": "1", "v": "7", "start": text7.index("அந்த"),
         "end": text7.index("அந்த") + len("அந்த தேவன்"),
         "original": "அந்த தேவன்", "verdict": "TP"},
        {"id": "R6", "kind": "abstained", "cls": "H_demonstrative_houseform", "rule": "(abstained)",
         "book": "rut", "ch": "1", "v": "7", "start": -1, "end": -1, "original": "அந்த தேவன்",
         "prev": "அந்த", "next": "தேவன்", "verdict": "MISSED"},
        {"id": "W1", "kind": "word", "cls": "acc_root", "word": "மலை", "verdict": "ROOT_KEEP"},
    ]
    return labels, scans, verses


def test_human_precision_counts_only_labelled_findings(benchmark):
    labels, scans, verses = _human_inputs(benchmark)
    result = bench.human_score(labels, scans, verses)
    rule = result["rules"]["ta-irv/sandhi.vallinam.demonstrative"]
    # R1 TP, R2 FP; R3 is a house form, reported apart; R4 "unsure" is excluded;
    # the unlabelled v4 finding is not scored at all.
    assert (rule["labelled"], rule["tp"], rule["fp"], rule["house"], rule["excluded"]) == (3, 1, 1, 1, 1)
    assert rule["precision"] == 0.5
    # R5: a human-confirmed finding the engine does not produce.
    assert rule["lost_tp"] == 1
    proxy = result["recallProxies"]["H_demonstrative_houseform"]
    assert (proxy["missed"], proxy["missed_now_flagged"], proxy["still_missed_rate"]) == (1, 0, 1.0)
    assert result["mismatches"] == []
    table = bench.human_markdown(result)
    assert "| `ta-irv/sandhi.vallinam.demonstrative` |" in table and "Recall proxies" in table


def test_human_gate_decides_inline_on_precision_and_sample_size(benchmark):
    labels, scans, verses = _human_inputs(benchmark)
    result = bench.human_score(labels, scans, verses)
    rule = result["rules"]["ta-irv/sandhi.vallinam.demonstrative"]
    rule["inline"], rule["lost_tp"] = True, 0

    def failures(baseline=None):
        # Only this rule: the synthetic labels do not cover the pack's real inline rules.
        return [f for f in bench.human_gate(result, baseline) if "demonstrative" in f]
    assert failures() == ["ta-irv/sandhi.vallinam.demonstrative is inline with 3 human-labelled findings (< 20)"]
    rule["labelled"], rule["tp"], rule["fp"], rule["precision"] = 20, 17, 3, 0.85
    assert failures() == ["ta-irv/sandhi.vallinam.demonstrative is inline but human precision is 85.0% (< 90.0%)"]
    rule["precision"] = 0.9
    assert failures() == []
    baseline = {"rules": {"ta-irv/sandhi.vallinam.demonstrative": {"precision": 0.95}}}
    assert failures(baseline) == ["ta-irv/sandhi.vallinam.demonstrative human precision fell from 95.0% to 90.0%"]
    rule["lost_tp"] = 1
    assert "1 human-confirmed finding(s) no longer produced" in failures()[0]
    # An inline rule the labels never reach fails too: inline needs a sample.
    assert "ta-irv/sandhi.vallinam.dative is inline with 0 human-labelled findings (< 20)"         in bench.human_gate(result, None)


def test_a_label_that_no_longer_anchors_fails_the_human_gate(benchmark):
    labels, scans, verses = _human_inputs(benchmark)
    labels[0] = {**labels[0], "start": labels[0]["start"] + 1, "end": labels[0]["end"] + 1}
    result = bench.human_score(labels, scans, verses)
    assert result["mismatches"] == ["R1 RUT 1:1: label text does not match the verse at "
                                    f"({labels[0]['start']}, {labels[0]['end']})"]
    assert bench.human_gate(result, None)[0].startswith("label not scored: R1 RUT 1:1")


def test_the_committed_human_labels_anchor_in_their_verses():
    """Every flagged label's text is at its offsets in the committed verses:
    the CI gate scores all of them, and a verse edit is caught here first."""
    folder = REPO_ROOT / "benchmark" / "human" / "2026-09-28"
    labels = bench.load_human_labels(folder / "human_labels.jsonl")
    verses = bench.load_human_verses(bench.human_verses_path(folder / "human_labels.jsonl"))
    flagged = [r for r in labels if r["kind"] == "flagged"]
    assert len(labels) == 598 and len(flagged) == 224
    for label in flagged:
        text = verses[label["book"]][label["ch"]][label["v"]]
        assert bench.nfc(text[label["start"]:label["end"]]) == bench.nfc(label["original"]), label["id"]
    # Exactly one row is unanswered (R0102, PSA 119:54) and it is never scored.
    assert [r["id"] for r in labels if bench.human_verdict(r.get("verdict")) is None and r["kind"] == "flagged"] \
        == ["R0102"]


def test_every_round_anchors_and_the_rounds_load_together():
    """Round 2 (2026-09-29): 220 new items from all 66 books, none seen in
    round 1. Loaded together, every flagged or split label still anchors."""
    labels, verses = bench.load_human_rounds(REPO_ROOT / "benchmark" / "human")
    rounds = {}
    for label in labels:
        rounds[label["round"]] = rounds.get(label["round"], 0) + 1
    assert rounds == {"2026-09-28": 598, "2026-09-29": 220}
    for label in labels:
        if label["kind"] in {"flagged", "split"}:
            text = verses[label["book"]][label["ch"]][label["v"]]
            assert bench.nfc(text[label["start"]:label["end"]]) == bench.nfc(label["original"]), \
                (label["round"], label["id"])


def test_the_human_gate_compares_each_round_with_itself():
    """A new round changes the combined denominator (dative 46/46 then
    85/86): not a regression. Round 1 falling is."""
    result = {"mismatches": [], "rounds": ["r1", "r2"], "rules": {"x/rule": {
        "inline": False, "labelled": 86, "precision": 0.988, "lost_tp": 0,
        "rounds": {"r1": {"precision": 1.0}, "r2": {"precision": 0.975}}}}}
    baseline = {"rounds": ["r1"], "rules": {"x/rule": {"precision": 1.0, "rounds": {"r1": {"precision": 1.0}}}}}
    assert bench.human_gate(result, baseline) == []
    # A baseline from before rounds holds its first round's numbers.
    assert bench.human_gate(result, {"rules": {"x/rule": {"precision": 1.0}}}) == []
    result["rules"]["x/rule"]["rounds"]["r1"]["precision"] = 0.97
    assert bench.human_gate(result, baseline) == ["x/rule human precision in round r1 fell from 100.0% to 97.0%"]


def test_a_rule_whose_whole_population_is_labelled_may_be_inline():
    """Rule (b), DECISIONS.md 2026-09-29: under 20 labels, inline only when
    every finding the rule makes in the collection is confirmed and none is
    wrong -- and only while the rule is unchanged since the population."""
    rule = "ta-irv/typo.suffix.dropped-tha"
    population = {"rules": {rule: {"definition": bench.rule_definition_hash(rule), "findings": [
        {"book": "1ch", "chapter": "6", "verse": "31", "start": 10, "end": 20, "text": "x"},
        {"book": "1ch", "chapter": "23", "verse": "5", "start": 30, "end": 39, "text": "y"}]}}}
    labels = [{"book": "1ch", "ch": "6", "v": "31", "start": 10, "end": 20, "verdict": "TP1"},
              {"book": "1ch", "ch": "23", "v": "5", "start": 30, "end": 39, "verdict": "TP1"}]
    result = {"mismatches": [], "rounds": ["r"], "rules": {rule: {
        "inline": True, "labelled": 2, "tp": 2, "fp": 0, "precision": 1.0, "lost_tp": 0, "rounds": {}}},
        "population": bench.population_coverage(population, labels)}
    assert result["population"][rule] == {"total": 2, "confirmed": 2, "current": True}
    assert bench.human_gate(result, None) == []
    # One finding unconfirmed: not the whole population.
    result["population"] = bench.population_coverage(population, labels[:1])
    assert "1 of 2 findings are confirmed" in bench.human_gate(result, None)[0]
    # The rule changed since the population was written: stale.
    population["rules"][rule]["definition"] = "old"
    result["population"] = bench.population_coverage(population, labels)
    assert "the rule changed since" in bench.human_gate(result, None)[0]


def test_baseline_holds_numbers_only(benchmark):
    _, result, _ = benchmark
    text = json.dumps(bench.baseline_of(result), ensure_ascii=False)
    assert "அந்த" not in text and "unmatched" not in text
    assert bench.baseline_of(result)["rules"]["ta-irv/sandhi.vallinam.demonstrative"]["precision_strict"] == 0.2


def test_labelled_examples_quote_their_verse(benchmark):
    rows, _, verses = benchmark
    examples = bench.labelled_examples(rows, verses)
    sandhi = examples["sandhi"]
    # Maybe rows first; a test on them must not insist either way.
    assert [e["maybe"][0]["span"] for e in sandhi[:2]] == ["இந்த பட்டணம்", "அந்த சபையை"]
    assert all(e["expect"] == [] for e in sandhi[:2])
    positive = sandhi[2]
    assert positive["expect"][0]["span"] in positive["text"]
    assert positive["expect"][0]["fix"] == "அந்தக் காகம்"
    assert positive["expect"][0]["ruleId"] == "ta-irv/sandhi.vallinam.demonstrative"
    assert positive["origin"].startswith("RUT 1:1")


def test_house_style_suppressions_are_reported_not_scored(tmp_path):
    """--housestyle: a word the project's house style hides is not a false
    positive (nor a true one); it is listed per rule, so a learned entry can
    never raise a rule's precision silently."""
    irv, reviews = write_inputs(tmp_path, review_rows())
    rows = bench.load_review_rows([reviews / "RUT_Round2_Proofreading_Issues.csv"])
    book, chapters = bench.book_verses(irv / "08RUTIRVTam.SFM")
    style = [{"scope": "word-in-book", "ruleId": "ta-irv/sandhi.vallinam.demonstrative",
              "word": "அந்த பெண்", "state": "active"}]
    result = bench.score(rows, {book: bench.scan_book(book, chapters, housestyle=style)}, {book: chapters})
    rule = result["rules"]["ta-irv/sandhi.vallinam.demonstrative"]
    assert rule["suppressedByHouseStyle"] == 1 and rule["findings"] == 4  # 1:2's FP is hidden, not scored
    assert "Suppressed by house style" in bench.markdown_tables(result)


@pytest.mark.subprocess
def test_cli_gate_exit_code_and_outputs(tmp_path):
    irv, reviews = write_inputs(tmp_path, review_rows())
    out = tmp_path / "out"
    command = [sys.executable, str(REPO_ROOT / "scripts" / "language_qa_benchmark.py"),
               "--irv-dir", str(irv), "--reviews", str(reviews), "--out-dir", str(out),
               "--baseline", str(tmp_path / "baseline.json")]
    written = subprocess.run(command + ["--write-baseline"], capture_output=True, text=True, encoding="utf-8")
    assert written.returncode == 0, written.stderr
    assert "| `ta-irv/sandhi.vallinam.demonstrative` | yes | 5 |" in written.stdout
    assert json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))["books"] == ["rut"]
    [result_file] = out.glob("*.json")
    assert json.loads(result_file.read_text(encoding="utf-8"))["unmatchedFindings"]
    # Diagnostic only: 5 findings are too few to compare against the baseline,
    # and AI agreement no longer fails an inline rule (DECISIONS.md 2026-09-28).
    gated = subprocess.run(command + ["--gate"], capture_output=True, text=True, encoding="utf-8")
    assert gated.returncode == 0 and "gate: pass" in gated.stderr, gated.stderr


@pytest.mark.subprocess
def test_cli_human_gate_runs_on_the_committed_labels_without_the_corpus():
    """What CI runs: no --irv-dir, the verses come from verses.jsonl."""
    labels = REPO_ROOT / "benchmark" / "human"  # every round, as CI passes it
    command = [sys.executable, str(REPO_ROOT / "scripts" / "language_qa_benchmark.py"),
               "--human-labels", str(labels), "--gate"]
    gated = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert gated.returncode == 0 and "human gate: pass" in gated.stderr, gated.stderr
    assert "| `ta-irv/sandhi.vallinam.dative` | yes |" in gated.stdout
    assert "Recall proxies" in gated.stdout


def test_the_precision_table_keeps_only_the_packs_labelled_rules():
    """rule_precision.json (the inline threshold's data) carries aggregates
    only, for rules a reviewer labelled, of the one pack it ships in."""
    result = {"packVersion": "hi-irv@1.0.0", "labelsFile": "benchmark/human/hi", "generatedAt": "2026-10-07T12:00:00",
              "rules": {"hi-irv/hi.lex.known-misspelling": {"labelled": 40, "tp": 38, "fp": 2, "precision": 0.95},
                        "hi-irv/hi.shape.errors": {"labelled": 0, "tp": 0, "fp": 0, "precision": None},
                        "common/spacing.extra": {"labelled": 23, "tp": 23, "fp": 0, "precision": 1.0}}}
    table = bench.human_precision_table(result, "hi-irv")
    assert table["rules"] == {"hi-irv/hi.lex.known-misspelling": {"precision": 0.95, "labelled": 40}}
    assert (table["packVersion"], table["labelsFile"]) == ("hi-irv@1.0.0", "benchmark/human/hi")
    assert "examples" not in json.dumps(table) and "text" not in json.dumps(table)
