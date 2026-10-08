"""Two Language QA pass rules on their own (language_qa_jobs): the layer's
de-duplication only defers to a finding that is drawn, and the book finding
limit drops the least certain book-stage findings first."""
from tc_ai_bridge.language_qa_jobs import _already_flagged, _cap_order


def finding(start, end, category="typo", confidence="low", severity="low"):
    return {"chapter": "1", "verse": "1", "start": start, "end": end, "category": category,
            "confidence": confidence, "severity": severity}


def test_a_layer_finding_survives_a_panel_only_overlap():
    listed = {"1:1": [(0, 5, "typo", False)]}
    drawn = {"1:1": [(0, 5, "typo", True)]}
    assert not _already_flagged(finding(2, 4), listed), "the listed one has no mark; keep the layer's"
    assert _already_flagged(finding(2, 4), drawn)
    assert not _already_flagged(finding(2, 4, category="sandhi"), drawn), "another category is another problem"
    assert not _already_flagged({**finding(2, 4), "context": "heading"}, drawn)


def test_book_stage_findings_are_cut_lowest_confidence_first():
    found = [finding(0, 1, confidence="low"), finding(1, 2, confidence="high"),
             finding(2, 3, confidence="medium", severity="low"), finding(3, 4, confidence="medium", severity="high"),
             finding(4, 5, confidence="low")]
    assert _cap_order(found) == [1, 3, 2, 0, 4]
