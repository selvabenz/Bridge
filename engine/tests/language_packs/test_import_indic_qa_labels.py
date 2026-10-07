"""scripts/import_indic_qa_labels.py: a workbook row is anchored only when
its text is unambiguous in the verse (`external` marker: imports a script)."""
from import_indic_qa_labels import anchor, verse_key


def test_a_finding_that_occurs_once_is_anchored():
    assert anchor("वे बडे़ लोग थे।", "बडे़", "") == (3, 7)


def test_the_workbooks_ellipsis_is_not_part_of_the_finding():
    assert anchor("वे बडे़ लोग थे।", "…बडे़…", "") == (3, 7)


def test_a_repeated_finding_needs_its_context_to_be_anchored():
    text = "लिये और फिर लिये गये"
    assert anchor(text, "लिये", "") is None
    assert anchor(text, "लिये", "…फिर लिये गये") == (12, 16)


def test_a_finding_that_is_not_in_the_verse_is_not_anchored():
    assert anchor("वे लोग थे।", "बडे़", "") is None


def test_a_verse_reference_finds_its_bridge_or_segment():
    chapters = {"1": {"1": "a", "3-4": "b", "5a": "c"}}
    assert verse_key(chapters, "1", "1") == "1"
    assert verse_key(chapters, "1", "4") == "3-4"
    assert verse_key(chapters, "1", "5") == "5a"
    assert verse_key(chapters, "1", "9") is None
