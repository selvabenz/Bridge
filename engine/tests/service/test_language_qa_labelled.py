"""The labelled examples (engine/tests/fixtures/language_qa/labelled/).

Two sources, one format:
- Phase 2.4, sampled from the IRV AI review reports by
  scripts/language_qa_benchmark.py --write-labelled. Positives name the rule
  that finds them today (or None, for rows no rule covers yet); maybes are
  contradicted rows a test must not insist on either way.
- The 2026-09-28 human review (benchmark/human/2026-09-28/labelled/, appended;
  origin "... (human review 2026...)"). `expect` is a finding the reviewer
  confirmed, with the reviewer's fix; `negative` is a confirmed false alarm,
  which its rule must not raise; `maybe` is evidence of another defect (a
  split word), which no sandhi rule may claim.

When a human-review example fails, the pack disagrees with the reviewer:
fix the pack, never the fixture."""
import json
import re
import unicodedata

import pytest

from tc_ai_bridge.language_qa import scan_text
from tc_ai_bridge.language_qa_benchmark import BUCKETS, CATEGORY_BUCKETS
from tests.support.packs import ta_pack
from tests.support.paths import REPO_ROOT

LABELLED = REPO_ROOT / "engine" / "tests" / "fixtures" / "language_qa" / "labelled"
# The review buckets, plus the human review's split words (round 2).
FIXTURE_BUCKETS = (*BUCKETS, "word-joining")
# The round-2 converter named the split-word candidate rule; the curated rule
# that implements it is lexicon.known-split (LANGUAGE_QA_PLAN.md).
RULE_ALIASES = {"ta-irv/word-joining.orphan-syllable": "ta-irv/lexicon.known-split"}


# (test, example id) the pack does not yet satisfy: the reviewer disagrees with
# it, and the pack changes that follow the review fix them. Strict, so each
# entry must start passing when its fix lands, and is then deleted here.
# Round 2 (2026-09-29): the reviewer disagrees with the pack. Cleared by the
# round-2 steps: roots (2), misspelling pairs (3), தான் (4), known splits (5).
PENDING_PACK_CHANGES: set[tuple[str, str]] = set()  # round 2 cleared by its steps 2-5 (2026-09-29)

# False alarms the pack still raises, and no pack change can remove: the two
# accusatives before a name (GEN 21:9 செய்கிறதை சாராள், GEN 35:4 அவைகளை சீகேம்).
# Names block doubling in 8 of 25 reviewed cases, not 17, so there is no name
# abstain to write. They are what keeps the accusative at 94.6%, not 100%.
RESIDUAL: set[tuple[str, str]] = {
    ("negative", "sandhi-81"), ("negative", "sandhi-83"),
}

# A reviewer's fix that combines two findings, which no single finding can
# carry: 1CH 23:5 "செய்வற்கு தான்" -> "செய்வதற்குத் தான்" is typo.suffix.dropped-tha
# (செய்வதற்கு) plus the dative's தான் (…குத் தான்). Both findings are raised.
COMBINED_FIX: set[tuple[str, str]] = {("positive", "sandhi-241")}


def examples(test: str = ""):
    params = []
    for bucket in FIXTURE_BUCKETS:
        lines = (LABELLED / f"{bucket}.jsonl").read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            ident = f"{bucket}-{number}"
            marks = [pytest.mark.xfail(strict=True, reason="the pack disagrees with the 2026-09-28 reviewer")] \
                if (test, ident) in PENDING_PACK_CHANGES else \
                [pytest.mark.xfail(strict=True, reason="residual false alarm before a name")] \
                if (test, ident) in RESIDUAL else \
                [pytest.mark.xfail(strict=True, reason="the reviewer's fix combines two findings")] \
                if (test, ident) in COMBINED_FIX else []
            params.append(pytest.param(bucket, json.loads(line), id=ident, marks=marks))
    return params


def squeezed(text):
    # The reviewer writes a pair across a poetry line with a space; the verse
    # has a line break there. Any whitespace run anchors as one space.
    return re.sub(r"\s+", " ", nfc(text))


def human(example):
    return "(human review" in example["origin"]


def nfc(text):
    return unicodedata.normalize("NFC", text)


def test_every_bucket_has_a_fixture_file():
    assert sorted(p.stem for p in LABELLED.glob("*.jsonl")) == sorted(FIXTURE_BUCKETS)


@pytest.mark.parametrize("bucket,example", examples())
def test_example_is_well_formed(bucket, example):
    assert example["text"] and example["origin"]
    for expected in example["expect"]:
        # The bucket itself, or a category the benchmark scores against it (a
        # human-review spacing finding sits in the punctuation bucket).
        assert expected["category"] == bucket or bucket in CATEGORY_BUCKETS.get(expected["category"], ())
        assert squeezed(expected["span"]) in squeezed(example["text"])
    for maybe in example.get("maybe", []):
        assert squeezed(maybe["span"]) in squeezed(example["text"]) and maybe["reason"]
    for negative in example.get("negative", []):
        assert nfc(negative["span"]) in nfc(example["text"]) and negative["ruleId"] and negative["reason"]
    assert example["expect"] or example.get("maybe") or example.get("negative") or "rejectedSpan" in example


def lexicon_over(text):
    from tc_ai_bridge.language_packs.lexicon import lexicon_findings
    from tc_ai_bridge.language_qa import rule_fields, suggestion, word_occurrences
    from tests.support.packs import ta_pack, with_enabled
    counts, first_seen = {}, {}
    for word, start, end in word_occurrences(text):
        counts[word] = counts.get(word, 0) + 1
        first_seen.setdefault(word, ("1", "1", start, end, text[start:end], "h"))
    # Every lexicon check, rare-near-common included although it ships disabled.
    return lexicon_findings("x", counts, first_seen, ta_pack().lexicon(),
                            pack=with_enabled(ta_pack(), "lexicon.rare-near-common"),
                            rule_fields=rule_fields, suggestion=suggestion)


def findings_of(example, rule_id):
    rule_id = RULE_ALIASES.get(rule_id, rule_id)
    if "/lexicon." in rule_id and rule_id != "ta-irv/lexicon.known-split":
        # The lexicon rules run over a book's word counts; over this verse
        # alone every word is "rare in the book", and a known misspelling
        # needs no book at all, so the verse is a book of one.
        findings = lexicon_over(example["text"])
    else:
        findings = scan_text(example["text"], book="x", chapter="1", verse="1", pack=ta_pack())["findings"]
    # ruleId, not the `rule` alias: migrated pack rules keep their legacy name there.
    return [f for f in findings if f["ruleId"] == rule_id]


def rule_disabled(rule_id):
    """A reviewed finding whose rule the pack has since disabled, because
    another rule now finds it (sandhi.clitic.fused -> the வல்லினம் rules' தான்
    context, 2026-09-29): any sandhi rule may find it."""
    pack = ta_pack()
    rule = pack.by_id(rule_id.split("/", 1)[1]) if rule_id.startswith(f"{pack.name}/") else None
    return rule is not None and not rule.enabled


def overlaps(a, b):
    a, b = squeezed(a), squeezed(b)
    return a in b or b in a


@pytest.mark.parametrize("bucket,example", examples("negative"))
def test_a_confirmed_false_alarm_is_not_raised_by_its_rule(bucket, example):
    for negative in example.get("negative", []):
        raised = [f for f in findings_of(example, negative["ruleId"]) if overlaps(f["originalText"], negative["span"])]
        # The reviewer's reason for integrity.space-before-note-end is "USFM
        # formatting, not a text error": the rule stays, as a low-severity
        # markup item, and must never present itself as a text error.
        raised = [f for f in raised if not (f["category"] == "usfm" and f["severity"] == "low")]
        assert not raised, (negative, [(f["originalText"], f["category"]) for f in raised])


@pytest.mark.parametrize("bucket,example", examples("maybe"))
def test_a_reviewed_other_defect_is_not_claimed_by_a_sandhi_rule(bucket, example):
    """The reviewer's "maybe" items are split words (கை கோலில் -> கைக்கோலில்,
    சு வரை -> சுவரை), not a missing வல்லினம். The வல்லினம் rules keep these
    words excluded; a split-word check is scoped separately (LANGUAGE_QA_PLAN)."""
    if not human(example):
        return  # Phase 2.4 maybes: contradicted AI rows, not insisted on either way
    findings = scan_text(example["text"], book="x", chapter="1", verse="1", pack=ta_pack())["findings"]
    for maybe in example.get("maybe", []):
        claimed = [f["originalText"] for f in findings
                   if f["category"] == "sandhi" and overlaps(f["originalText"], maybe["span"])]
        assert not claimed, (maybe, claimed)


@pytest.mark.parametrize("bucket,example", examples("positive"))
def test_a_positive_credited_to_a_rule_is_still_found_by_it(bucket, example):
    for expected in example["expect"]:
        if not expected["ruleId"]:
            continue  # no current rule covers it; Phase 3+ will
        if expected["ruleId"].endswith("/tamil.wordlist-variant"):
            continue  # a book-wide audit, not a per-verse rule; the benchmark measures it
        if human(example) and ("missed)" in example["origin"] or rule_disabled(expected["ruleId"])):
            # A context the pack skipped: the reviewer said "doubling needed",
            # and the converter guessed the rule. Any sandhi rule may find it
            # (கிழக்கு காற்று is the direction rule's, not the dative's).
            found = [f for f in scan_text(example["text"], book="x", chapter="1", verse="1", pack=ta_pack())["findings"]
                     if f["ruleId"].startswith("ta-irv/sandhi.")]
        else:
            found = findings_of(example, expected["ruleId"])
        span = nfc(expected["span"])
        assert any(overlaps(f["originalText"], span) for f in found), (expected, [f["originalText"] for f in found])
        if human(example) and expected.get("fix"):
            # The reviewer confirmed this fix: where the finding covers exactly
            # the reviewed span, one of its suggestions must be it.
            for finding in (f for f in found if squeezed(f["originalText"]) == squeezed(span)):
                assert squeezed(expected["fix"]) in {squeezed(s["text"]) for s in finding["suggestions"]}, \
                    (expected, finding["suggestions"])
