"""
Tests for tc_ai_bridge.versification, which wraps the vendored Greek Room
versification tool (engine/vendor/greekroom-versification/, see NOTICE.md
there) as a direct library import.

All assertions run against the real vendored data files, not synthetic
mappings — the values checked here (e.g. the Psalm 3 descriptive-title
shift) are the real cross-tradition reference behavior these files encode.
"""
import sys

import pytest

from tc_ai_bridge import versification as vt


def test_vendor_data_is_present():
    # If this is False every other test in this module is meaningless —
    # check it explicitly rather than letting later tests fail confusingly.
    assert vt.is_available()


def test_schema_names_cover_all_six_standard_schemas():
    names = vt.schema_names()
    assert set(names) == {"org", "eng", "rsc", "rso", "vul", "lxx"}
    assert names["org"] == "Original"
    assert names["eng"] == "English"


def test_org_ref_is_identity_when_no_mapping_applies():
    result = vt.to_org_ref("GEN", "1", "1", "eng")
    assert result["orgRef"] == "GEN 1:1"
    assert result["mapping"] == "same"


def test_psalm_3_descriptive_title_shifts_eng_to_org():
    """Real-world example: Psalm 3's Hebrew ('org') text opens with a
    descriptive title ("A psalm of David...") counted as verse 1, which most
    English Bibles don't number as its own verse. So eng verse 1 ('O LORD,
    how many are my foes...') is org verse 2, and every later verse in the
    chapter shifts by one too."""
    v1 = vt.to_org_ref("PSA", "3", "1", "eng")
    assert v1["orgRef"] == "PSA 3:2"
    assert v1["mapping"] == "mapped"

    v2 = vt.to_org_ref("PSA", "3", "2", "eng")
    assert v2["orgRef"] == "PSA 3:3"


def test_org_schema_maps_to_itself():
    result = vt.to_org_ref("PSA", "3", "1", "org")
    assert result["orgRef"] == "PSA 3:1"
    assert result["mapping"] == "same"


def test_unknown_schema_raises():
    with pytest.raises(vt.VersificationUnavailable):
        vt.to_org_ref("PSA", "3", "1", "klingon")


def test_back_versification_map_round_trips_the_psalm_3_shift():
    back_map = vt.back_versification_map("PSA", "eng")
    # org 'PSA 3:2' came from eng's 'PSA 3:1' — the inverse of the forward
    # mapping asserted above.
    assert back_map["PSA 3:2"] == "PSA 3:1"
    assert back_map["PSA 3:3"] == "PSA 3:2"
    # The org-only descriptive-title verse (Psalm 3 is one of the 63 psalms
    # where it's counted as its own verse in 'org') maps back to eng's
    # pseudo-verse-0 convention for untitled descriptive titles, not to
    # itself — verified against the real eng.json mappedVerses entries
    # ("PSA 51:0 -> PSA 51:1-2" etc.), not assumed.
    assert back_map["PSA 3:1"] == "PSA 3:0"


def test_back_versification_map_is_scoped_to_the_requested_book():
    back_map = vt.back_versification_map("PSA", "eng")
    assert all(ref.startswith("PSA ") for ref in back_map)


def test_detect_schema_prefers_org_for_org_numbered_psalm_3():
    """Psalm 3 has 9 verses under 'org' numbering (the descriptive title
    counted as verse 1) but only 8 under 'eng' numbering — confirmed against
    the real vendored data/standard_mappings/{org,eng}.json maxVerses
    entries for PSA chapter 3, not assumed. A book whose chapter 3 goes all
    the way up to verse 9 is therefore real, structural evidence that its
    verse numbering follows 'org', not 'eng' — VersificationMatch scores
    that verse 9 as overage against every schema that doesn't expect it."""
    verses = {f"3:{n}": f"text {n}" for n in range(1, 10)}  # verses 1..9
    result = vt.detect_schema("PSA", verses)
    assert result["bestSchema"] == "org"
    assert result["costBySchema"]["org"] < result["costBySchema"]["eng"]


def test_detect_schema_reports_every_standard_schema_cost():
    result = vt.detect_schema("GEN", {"1:1": "In the beginning..."})
    assert set(result["costBySchema"]) == {"org", "eng", "rsc", "rso", "vul", "lxx"}


def test_load_versifications_survives_being_called_twice_in_one_process():
    """Real bug found while integrating this (see NOTICE.md): the vendored
    Versification.load_versifications() keeps class-level state and crashes
    with AttributeError on a second direct call. tc_ai_bridge.versification
    must guard against that so a long-lived bridge-engine process (many
    project opens) never hits it — calling any public function here twice in
    a row is the realistic reproduction of that scenario."""
    first = vt.to_org_ref("PSA", "3", "1", "eng")
    second = vt.to_org_ref("PSA", "3", "1", "eng")
    assert first == second


# -- edge cases -------------------------------------------------------------
#
# Every expected value below was read off the real vendored data first (see
# the session that added this block), not guessed and then patched to match —
# guessing here once already produced two wrong assertions (the PSA 3:1 back-
# map and a tied detect_schema cost) that had to be corrected against actual
# output. These push into shapes the earlier "happy path" tests never
# exercised: merges, splits, unknown books, and USFM verse bridges/segments.


def test_org_ref_handles_a_real_merge_ntoone_mapping():
    """Real eng.json entry: 'NEH 7:68-69 -> NEH 7:68' — two consecutive eng
    verses collapse into one org verse. Both source verses must report the
    SAME merge target and the same merged-source description; neither should
    be silently dropped or mistaken for a 1:1 'mapped' case."""
    v68 = vt.to_org_ref("NEH", "7", "68", "eng")
    v69 = vt.to_org_ref("NEH", "7", "69", "eng")
    assert v68["mapping"] == "merge"
    assert v69["mapping"] == "merge"
    assert v68["orgRef"] == v69["orgRef"] == "NEH 7:68"
    assert v68["mergedWith"] == v69["mergedWith"] == "NEH 7:68-69"


def test_org_ref_handles_a_real_split_onetomany_mapping():
    """Real eng.json entry: 'PSA 51:0 -> PSA 51:1-2' — eng's pseudo-verse-0
    descriptive title expands into two distinct org verses. splitInto must
    enumerate both individually, not just the pretty-printed span."""
    result = vt.to_org_ref("PSA", "51", "0", "eng")
    assert result["mapping"] == "split"
    assert result["orgRef"] == "PSA 51:1-2"
    assert result["splitInto"] == ["PSA 51:1", "PSA 51:2"]


def test_org_ref_handles_a_merge_that_exists_within_the_org_schema_itself():
    """Not every merge is cross-schema: org.json's OWN mappedVerses has
    'HAB 3:19-20 -> HAB 3:19' (a real internal quirk in the Masoretic
    numbering, logged as a real merge when the vendored data loads — see
    load_versifications' own log output). So to_org_ref(..., schema='org')
    is not always a no-op identity mapping, even though 'org' is the target
    every other schema maps into."""
    v19 = vt.to_org_ref("HAB", "3", "19", "org")
    v20 = vt.to_org_ref("HAB", "3", "20", "org")
    assert v19["mapping"] == "merge"
    assert v20["mapping"] == "merge"
    assert v19["orgRef"] == v20["orgRef"] == "HAB 3:19"


def test_org_ref_for_unknown_book_is_a_graceful_identity_not_a_crash():
    """A book code the standard schemas don't recognize at all (custom
    front-matter code, typo, non-canonical book) must not raise — it falls
    through to identity, since 'no mapping entry' and 'unrecognized book'
    look the same to a plain dict lookup. Documents the real behavior rather
    than assuming it errors."""
    result = vt.to_org_ref("ZZZ", "1", "1", "eng")
    assert result["mapping"] == "same"
    assert result["orgRef"] == "ZZZ 1:1"


def test_back_versification_map_for_unknown_book_is_empty_not_a_crash():
    back_map = vt.back_versification_map("ZZZ", "eng")
    assert back_map == {}


def test_org_ref_passes_through_a_usfm_verse_bridge_unchanged():
    """USFM verse bridges like '3-4' are a real, already-known shape
    elsewhere in Bridge (bridge_service.py's numeric-anchor handling for
    findings). The vendored mapping dicts are keyed by single verse ids, so
    a bridge string never matches a key and falls through to identity —
    this is real, current behavior, not a designed feature. A caller passing
    a bridge/segment ref should not expect a meaningful cross-schema
    mapping for it without first splitting the bridge into individual
    verses."""
    result = vt.to_org_ref("GEN", "1", "3-4", "eng")
    assert result["mapping"] == "same"
    assert result["orgRef"] == "GEN 1:3-4"


def test_org_ref_passes_through_a_usfm_verse_segment_unchanged():
    """Same real fallthrough as verse bridges, for a lettered segment
    ('3a') — another shape bridge_service.py already has to special-case
    for numeric anchors elsewhere in the protocol."""
    result = vt.to_org_ref("GEN", "1", "3a", "eng")
    assert result["mapping"] == "same"
    assert result["orgRef"] == "GEN 1:3a"


def test_org_ref_is_case_insensitive_on_book_id():
    """Bridge's own book_id convention is lowercase (TranslationCoreProject
    lowercases it); the vendored data is uppercase USFM codes. Mixed/odd
    casing from a caller should still resolve correctly, not silently miss
    every mapping because of a case mismatch."""
    result = vt.to_org_ref("GeN", "1", "1", "eng")
    assert result["orgRef"] == "GEN 1:1"


def test_detect_schema_with_no_verses_does_not_crash():
    """A book with zero checkable verse text (e.g. every verse still empty
    right after import, before any translation work) is a real reachable
    state — detect_versification() is called lazily on first request, which
    could be before any text exists."""
    result = vt.detect_schema("GEN", {})
    assert result["verseCount"] == 0
    assert result["bestSchema"] in vt.SCHEMAS
    assert set(result["costBySchema"]) == set(vt.SCHEMAS)


def test_detect_schema_ignores_pseudo_books_without_crashing():
    """Front-matter/custom project codes (XXA etc., from BibleStructure's
    own pseudo_books list) aren't real Scripture books in any schema. The
    real VersificationMatch code explicitly skips books outside its own
    known-book list rather than counting them as overage — confirmed here
    against real behavior rather than assumed from reading that branch."""
    result = vt.detect_schema("XXA", {"1:1": "front matter text"})
    assert result["verseCount"] == 1
    assert all(cost == 0 for cost in result["costBySchema"].values())


def test_org_ref_with_empty_book_id_does_not_crash():
    result = vt.to_org_ref("", "1", "1", "eng")
    assert result["mapping"] == "same"


def test_detect_schema_leaves_the_process_wide_stderr_alone(monkeypatch):
    """#248: the scan ran under contextlib.redirect_stderr, which swaps
    sys.stderr for every thread, so other threads' trace lines and tracebacks
    were lost while it ran."""
    stderr = sys.stderr
    seen = []
    real = vt._match_schema_cost

    def spy(*args):
        seen.append(sys.stderr is stderr)
        return real(*args)

    monkeypatch.setattr(vt, "_match_schema_cost", spy)
    vt.detect_schema("GEN", {"1:1": "In the beginning..."})
    assert seen and all(seen)
