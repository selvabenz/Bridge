"""#234: one owner per check. Repeated spaces and zero-width/BOM characters
are Language QA's (spacing.extra, unicode.invisible); the local editorial
check no longer reports them, and still reports a repeated word."""
from __future__ import annotations

from tc_ai_bridge.language_qa import scan_text
from tc_ai_bridge.local_checks import target_editorial_checks


def test_local_editorial_check_leaves_spaces_and_invisible_characters_to_language_qa():
    text = "ஆதியிலே  தேவன் தேவன் வான​த்தையும்﻿ படைத்தார்."
    codes = [issue.code for issue in target_editorial_checks(text)]
    assert codes == ["TA_REPEAT_WORD"]

    rules = {f["rule"] for f in scan_text(text, book="gen", chapter="1", verse="1")["findings"]}
    assert {"spacing.extra", "unicode.invisible"} <= rules
