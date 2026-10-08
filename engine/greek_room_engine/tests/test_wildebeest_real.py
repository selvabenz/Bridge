"""
Real-engine coverage for WildebeestAdapter, using the actual wildebeest-nlp
package rather than the fallback mock.

Skipped automatically wherever wildebeest-nlp isn't installed (a fresh
clone, most CI environments, or Python 3.13 — see docs/BUILD_LOG.md
for why 3.13 currently can't install it at all). The rest of the test suite
must stay green without this optional dependency; only this file requires it.
"""
import pytest

pytest.importorskip("wildebeest", reason="real wildebeest-nlp package not installed")

from greek_room_engine.engine import GreekRoomEngine


def test_real_engine_is_actually_active():
    engine = GreekRoomEngine()
    info = engine.info()
    assert info["adapters"]["wildebeest"]["usingRealEngine"] is True
    assert info["adapters"]["wildebeest"]["version"] != "mock-0.0.0"


def test_real_engine_flags_mixed_script_token():
    engine = GreekRoomEngine()
    text = "தேவன்aஆதி"
    findings = engine.check_verse(
        project_id="p", lang_code="tam", ref="TIT 1:1", text=text, checks=["wildebeest"],
    )
    hits = [f for f in findings if f.check_type == "wildebeest.notable_token"]
    assert len(hits) == 1
    assert hits[0].start_offset is not None and hits[0].end_offset is not None
    assert text[hits[0].start_offset:hits[0].end_offset] == hits[0].original_text


def test_real_engine_flags_zero_width_character():
    engine = GreekRoomEngine()
    text = "ஆதியில்​தேவன்"
    findings = engine.check_verse(
        project_id="p", lang_code="tam", ref="TIT 1:1", text=text, checks=["wildebeest"],
    )
    hits = [f for f in findings if f.check_type == "wildebeest.zero_width"]
    assert len(hits) == 1
    assert hits[0].original_text == "​"
    assert text[hits[0].start_offset:hits[0].end_offset] == "​"


def test_real_engine_flags_non_canonical_form_and_suggests_nfc():
    engine = GreekRoomEngine()
    text = "café word"  # NFD-decomposed é
    findings = engine.check_verse(
        project_id="p", lang_code="eng", ref="TIT 1:1", text=text, checks=["wildebeest"],
    )
    hits = [f for f in findings if f.check_type == "wildebeest.non_canonical"]
    assert len(hits) == 1
    assert hits[0].suggested_replacement == "é"  # precomposed é (NFC)


def test_real_engine_produces_no_findings_for_clean_text():
    engine = GreekRoomEngine()
    findings = engine.check_verse(
        project_id="p", lang_code="tam", ref="TIT 1:1",
        text="ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார்", checks=["wildebeest"],
    )
    assert findings == []


def test_real_engine_failure_degrades_to_no_findings_not_a_crash(monkeypatch):
    from greek_room_engine.adapters import wildebeest_adapter

    def broken_process(*args, **kwargs):
        raise RuntimeError("simulated real-engine failure")

    monkeypatch.setattr(wildebeest_adapter.wb_ana, "process", broken_process)
    # An earlier test analysed this text; a cache hit (#230) would never reach
    # the broken process. Start from an empty cache so the failure path runs.
    monkeypatch.setattr(wildebeest_adapter, "_REPORT_CACHE", type(wildebeest_adapter._REPORT_CACHE)())

    engine = GreekRoomEngine()
    findings = engine.check_verse(
        project_id="p", lang_code="tam", ref="TIT 1:1",
        text="தேவன்aஆதி", checks=["wildebeest"],
    )
    assert findings == []
