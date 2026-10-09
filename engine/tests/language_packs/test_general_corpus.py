"""general_corpus.py: the lexicon itself and its verdict on a checker token (#246)."""
from __future__ import annotations

import gzip
import json
import unicodedata

from tc_ai_bridge.language_packs import general_corpus, indic_qa_vendor

NFC = lambda w: unicodedata.normalize("NFC", w)  # noqa: E731


def corpus(counts: dict[str, int], **thresholds) -> general_corpus.GeneralCorpus:
    return general_corpus.GeneralCorpus(counts=dict(counts), key=NFC, manifest={"outputSha256": "abc" * 8},
                                        **{"accept_min": 10, "suggest_min": 200, "suggest_top": 100, "ratio": 50,
                                           **thresholds})


def token(word: str, status: str = "unknown", irv: int = 1, ov: int = 0, **extra) -> dict:
    return {"t": word, "s": 0, "e": len(word), "status": status, "irv": irv, "ov": ov, "ov_lemma": 0,
            "ignored_once": False, "sugg": [], **extra}


def test_load_applies_the_floor_and_keeps_counts(tmp_path):
    rows = "#key\tcount\tsrc\nஒரு\t5000\tck\nஅவன்\t10\tk\nஅரிது\t3\tc\n"
    path = tmp_path / general_corpus.FILE_NAME
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(rows)
    manifest = tmp_path / general_corpus.MANIFEST_NAME
    manifest.write_text(json.dumps({"format": 1, "acceptMin": 10, "suggestMin": 200, "suggestTop": 50,
                                    "ratio": 50, "outputSha256": "x" * 64}), encoding="utf-8")
    loaded = general_corpus.load(path, manifest, NFC)
    assert loaded is not None
    assert (loaded.count("ஒரு"), loaded.known("ஒரு")) == (5000, True)
    assert (loaded.count("அவன்"), loaded.known("அவன்")) == (10, True)
    assert (loaded.count("அரிது"), loaded.known("அரிது")) == (3, False)
    assert loaded.fingerprint().startswith("xxxxxxxxxxxx/10/200/50")
    # Loaded once per process, by path.
    assert general_corpus.load_file(path, manifest, NFC) is general_corpus.load_file(path, manifest, NFC)
    manifest.write_text(json.dumps({"format": 99}), encoding="utf-8")
    assert general_corpus.load(path, manifest, NFC) is None, "a format this build does not read"


def test_lookup_uses_the_profiles_canon_key():
    key = general_corpus.key_function("hi")
    assert key("परमेश्‍वर") == "परमेश्वर"
    assert general_corpus.key_function("ta")("கோ") == NFC("கோ")
    assert general_corpus.rule_id("hi") == "hi.lex.irv-consistent-slip"
    assert general_corpus.rule_id("ta") == "indicqa.lex.irv-consistent-slip"
    assert set(general_corpus.RULES) == set(indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES)


def test_near_miss_needs_the_suggest_floor_and_closer_edits_come_first():
    lex = corpus({"மனிதன்": 50_000, "மனிதர்": 300, "மனித": 150})
    # One edit away and common enough to suggest: offered, commonest first.
    assert lex.near_miss("மனிதம்") == [("மனிதன்", 50_000), ("மனிதர்", 300)]
    # Below the suggest floor: never a suggestion, even one edit away.
    assert ("மனித", 150) not in lex.near_miss("மனிதம்")


def test_a_known_word_is_accepted_only_when_no_close_neighbour_is_commoner():
    # The #246 review: a web corpus carries popular misspellings. कवियत्री (36x)
    # is one vowel-sign swap from कवयित्री (137x); सन्यासी (129x) one anusvara
    # from संन्यासी (240x). Neither was a cluster edit, and neither neighbour is
    # 50x commoner, so the first rule accepted both.
    lex = general_corpus.GeneralCorpus(
        counts={"कवियत्री": 36, "कवयित्री": 137, "सन्यासी": 129, "संन्यासी": 240, "एससी": 1942,
                "मोबाइल": 25_979}, key=NFC, manifest={}, accept_min=10, suggest_min=100)
    swap = general_corpus.verdict(lex, token("कवियत्री"), irv_accept_min=5)
    assert (swap.kind, [s["text"] for s in swap.suggestions]) == ("augment", ["कवयित्री"])
    anusvara = general_corpus.verdict(lex, token("सन्यासी"), irv_accept_min=5)
    assert anusvara.kind == "augment"
    # The one-sign neighbour leads; a whole-cluster swap (एससी) is never a veto.
    assert anusvara.suggestions[0]["text"] == "संन्यासी"
    assert general_corpus.verdict(lex, token("मोबाइल"), irv_accept_min=5).kind == "accept"


def test_suggestions_are_spellings_not_lookup_keys():
    # Malayalam's canon writes a final ு as ്: the key ഒര് is not a spelling.
    lex = general_corpus.GeneralCorpus(counts={"ഒര്": 364_846}, key=NFC, manifest={},
                                       forms={"ഒര്": "ഒരു"}, accept_min=10, suggest_min=100)
    said = general_corpus.verdict(lex, token("ഒറ്"), irv_accept_min=5)
    assert [s["text"] for s in said.suggestions] == ["ഒരു"]
    assert "ഒരു" in said.note


def test_the_index_is_built_lazily_over_the_top_words_only():
    lex = corpus({f"w{i}": 1000 - i for i in range(300)}, suggest_top=20)
    assert lex._index is None
    lex.near_miss("w5")
    assert lex._index is not None and len({w for words in lex._index.values() for w in words}) == 20


def test_one_edit_is_one_grapheme_cluster_not_one_code_point():
    # A dropped final consonant takes its virama: two code points, one cluster.
    lex = corpus({"தண்ணீர்": 20_000, "मनुष्य": 20_000})
    assert lex.candidates("தண்ணீ") == {"தண்ணீர்"}
    # A vowel-length slip changes one sign inside a cluster: one cluster edit.
    assert lex.candidates("मनूष्य") == {"मनुष्य"}
    assert lex.candidates("तन") == set()


def test_an_unknown_word_the_corpus_knows_is_accepted_unless_a_neighbour_dwarfs_it():
    lex = corpus({"மனிதன்": 50_000, "தண்ணீர்": 20_000, "தண்ணீ": 30})
    accepted = general_corpus.verdict(lex, token("மனிதன்"), irv_accept_min=5)
    assert (accepted.kind, accepted.suggestions, accepted.note) == ("accept", [], "50000× in the general corpus")
    # A popular web typo: the corpus has it, but the right word is 50x commoner.
    typo = general_corpus.verdict(lex, token("தண்ணீ"), irv_accept_min=5)
    assert typo.kind == "augment"
    assert [s["text"] for s in typo.suggestions] == ["தண்ணீர்"]
    assert typo.suggestions[0] == {"text": "தண்ணீர்", "rank": 1, "source": "corpus", "kind": "corpus",
                                   "freq": 20_000, "rationale": "20000× in the general corpus"}
    assert typo.note == "30× in the general corpus, but தண்ணீர் 20000×"
    # Unknown to the corpus too, with a neighbour: the finding stays, with the neighbour.
    absent = general_corpus.verdict(lex, token("தண்ணீரு"), irv_accept_min=5)
    assert absent.kind == "augment" and absent.note == "nearest corpus word தண்ணீர் 20000×"
    # Unknown everywhere, no neighbour: nothing to add.
    assert general_corpus.verdict(lex, token("xyzzy"), irv_accept_min=5).kind == "none"
    # Other statuses are the checker's business.
    assert general_corpus.verdict(lex, token("தண்ணீ", status="compound"), irv_accept_min=5).kind == "none"
    assert general_corpus.verdict(lex, token("தண்ணீ", ignored_once=True), irv_accept_min=5).kind == "none"


def test_house_practice_absent_from_the_corpus_with_a_corpus_neighbour_is_a_consistent_slip():
    lex = corpus({"மனிதன்": 50_000})
    slip = general_corpus.verdict(lex, token("மனிதம்", irv=7), irv_accept_min=5)
    assert slip.kind == "slip" and [s["text"] for s in slip.suggestions] == ["மனிதன்"]
    assert slip.note == "IRV 7×; not in the OV nor the general corpus; nearest corpus word மனிதன் 50000×"
    # The profiles say irv_ok themselves; the Tamil layer leaves it as unknown with the count.
    assert general_corpus.verdict(lex, token("மனிதம்", status="irv_ok", irv=7), irv_accept_min=5).kind == "slip"
    # Known to the OV, or to the corpus: house practice stands.
    assert general_corpus.verdict(lex, token("மனிதம்", irv=7, ov=3), irv_accept_min=5).kind == "none"
    assert general_corpus.verdict(corpus({"மனிதம்": 40}), token("மனிதம்", irv=7), irv_accept_min=5).kind == "none"
    # No neighbour: nothing to say.
    assert general_corpus.verdict(corpus({}), token("மனிதம்", irv=7), irv_accept_min=5).kind == "none"


def test_corpus_suggestions_follow_the_checkers_and_never_repeat_one():
    mine = [{"text": "மனிதன்", "rank": 1, "source": "lexicon", "rationale": "reviewed"}]
    extra = [{"text": "மனிதன்", "rank": 1, "source": "corpus", "rationale": "x"},
             {"text": "மனிதர்", "rank": 1, "source": "corpus", "rationale": "y"}]
    merged = general_corpus.append_suggestions(mine, extra)
    assert [(s["text"], s["source"]) for s in merged] == [("மனிதன்", "lexicon"), ("மனிதர்", "corpus")]


def test_a_pack_without_the_key_or_switched_off_has_no_corpus(tmp_path):
    assert general_corpus.for_pack({}, tmp_path, "hi") is None
    assert general_corpus.for_pack({"generalCorpus": {"enabled": False}}, tmp_path, "hi") is None
    assert general_corpus.for_pack({"generalCorpus": {}}, None, "hi") is None
    assert general_corpus.for_pack({"generalCorpus": {}}, tmp_path, "hi") is None, "no file yet"
