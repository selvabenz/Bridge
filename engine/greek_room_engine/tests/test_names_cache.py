"""#233: the names check romanizes each token and compares each pair once per
language for the life of the process. A second check over the same words --
the book after a one-verse edit -- calls neither uroman nor Smart Edit
Distance again and finds exactly what the first did. Real dependencies, as in
test_names_adapter.py."""
from __future__ import annotations

from collections import OrderedDict

from greek_room_engine.adapters import names_adapter as module
from greek_room_engine.adapters.names_adapter import NamesAdapter


def _shape(findings):
    return sorted((f.original_text, f.suggested_replacement, f.explanation) for f in findings)


def test_unchanged_words_are_not_romanized_or_compared_again(monkeypatch):
    monkeypatch.setattr(module, "_ROMANIZED", OrderedDict())
    monkeypatch.setattr(module, "_DISTANCES", OrderedDict())
    adapter = NamesAdapter()
    occurrences = {"Titus": [("1", "1"), ("1", "4"), ("3", "12")], "Tituss": [("2", "7")],
                   "Crete": [("1", "5"), ("1", "12")], "Cretee": [("1", "13")]}
    first = adapter.check_book(project_id="p", book_id="TIT", lang_code="eng", token_occurrences=occurrences)
    assert first

    uroman, sed = module._ensure_uroman(), module._ensure_sed("eng")
    calls = {"romanize": 0, "distance": 0}
    real_romanize, real_distance = uroman.romanize_string, sed.string_distance_cost

    def romanize(*a, **k):
        calls["romanize"] += 1
        return real_romanize(*a, **k)

    def distance(*a, **k):
        calls["distance"] += 1
        return real_distance(*a, **k)

    monkeypatch.setattr(uroman, "romanize_string", romanize)
    monkeypatch.setattr(sed, "string_distance_cost", distance)

    second = adapter.check_book(project_id="p", book_id="TIT", lang_code="eng", token_occurrences=occurrences)
    assert calls == {"romanize": 0, "distance": 0}
    assert _shape(second) == _shape(first)

    # One new word: romanized once; only pairs involving it are compared.
    occurrences["Titos"] = [("3", "1")]
    third = adapter.check_book(project_id="p", book_id="TIT", lang_code="eng", token_occurrences=occurrences)
    assert calls["romanize"] == 1
    assert calls["distance"] <= 2
    assert {f.original_text for f in first} <= {f.original_text for f in third}


def test_the_caches_are_per_language(monkeypatch):
    monkeypatch.setattr(module, "_ROMANIZED", OrderedDict())
    adapter = NamesAdapter()
    occurrences = {"Titus": [("1", "1")], "Tituss": [("2", "7")]}
    adapter.check_book(project_id="p", book_id="TIT", lang_code="eng", token_occurrences=occurrences)
    adapter.check_book(project_id="p", book_id="TIT", lang_code="deu", token_occurrences=occurrences)
    assert {key[0] for key in module._ROMANIZED} == {"eng", "deu"}
