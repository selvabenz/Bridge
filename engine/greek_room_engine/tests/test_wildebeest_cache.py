"""The Wildebeest report cache (#230): one wb_ana.process call per (language,
text) for the life of the process, the same findings from a hit as from a
miss, and a failure that is never cached. Runs with a stand-in for wb_ana, so
it needs no wildebeest-nlp install; test_wildebeest_real.py covers the real one."""
from __future__ import annotations

import json

import pytest

from greek_room_engine.adapters import wildebeest_adapter as adapter_module
from greek_room_engine.adapters.wildebeest_adapter import WildebeestAdapter


class _FakeAnalysis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_next = False

    def process(self, *, string: str, lang_code: str, json_output) -> None:
        self.calls.append((lang_code, string))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("wildebeest choked")
        report = {"block": {"LATIN": {"a": {}}}}
        if "​" in string:
            report["block"]["ZERO_WIDTH"] = {"​": {"name": "ZERO WIDTH SPACE"}}
        if "a" in string:
            report["notable-token"] = {"mixed script": {string.split()[-1]: {}}}
        json_output.write(json.dumps(report))


@pytest.fixture
def fake(monkeypatch):
    analysis = _FakeAnalysis()
    monkeypatch.setattr(adapter_module, "wb_ana", analysis)
    monkeypatch.setattr(adapter_module, "_WILDEBEEST_AVAILABLE", True)
    monkeypatch.setattr(adapter_module, "_REPORT_CACHE", type(adapter_module._REPORT_CACHE)())
    return analysis


def _check(adapter, text, lang="tam", ref="GEN 1:1"):
    return adapter.check_verse(project_id="p", lang_code=lang, ref=ref, text=text, params={})


def _shape(findings):
    return [(f.check_type, f.original_text, f.start_offset, f.end_offset, f.explanation) for f in findings]


def test_same_text_is_analysed_once_and_gives_the_same_findings(fake):
    adapter = WildebeestAdapter()
    text = "ஆதியில்​தேவன் தேவன்a"
    first = _check(adapter, text)
    second = _check(adapter, text, ref="EXO 2:3")
    assert len(fake.calls) == 1
    assert _shape(first) == _shape(second)
    assert {f.check_type for f in first} == {"wildebeest.zero_width", "wildebeest.notable_token"}
    # Findings are built per call: the second carries its own book.
    assert {f.book for f in second} == {"EXO"}
    assert first[0] is not second[0]


def test_another_text_or_language_is_analysed_again(fake):
    adapter = WildebeestAdapter()
    _check(adapter, "ஆதியில் தேவன்")
    _check(adapter, "ஆதியில் தேவன்.")
    _check(adapter, "ஆதியில் தேவன்", lang="hin")
    assert len(fake.calls) == 3


def test_a_failed_analysis_is_not_cached(fake):
    adapter = WildebeestAdapter()
    fake.fail_next = True
    assert _check(adapter, "ஆதியில்​தேவன்") == []
    assert [f.check_type for f in _check(adapter, "ஆதியில்​தேவன்")] == ["wildebeest.zero_width"]
    assert len(fake.calls) == 2


def test_the_cache_is_bounded(fake, monkeypatch):
    monkeypatch.setattr(adapter_module, "_REPORT_CACHE_MAX", 2)
    adapter = WildebeestAdapter()
    for text in ("one", "two", "three"):
        _check(adapter, text)
    assert len(adapter_module._REPORT_CACHE) == 2
    _check(adapter, "one")  # evicted: analysed again
    assert len(fake.calls) == 4
