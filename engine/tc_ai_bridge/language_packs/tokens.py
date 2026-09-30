"""The one tokenizer every Language QA rule shares: words and grapheme clusters."""
from __future__ import annotations

import regex

WORD = regex.compile(r"\p{L}[\p{L}\p{M}]*")
GRAPHEME = regex.compile(r"\X")
