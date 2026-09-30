"""The `indic` tier: rule kinds written once and driven by a pack's data
(docs/LANGUAGE_QA_PACKS.md). A pack never ships code; a rule names its kind
in `match.type`, and a need no kind covers becomes a new kind here, with
generic tests, usable by every language."""
from .confusion import ConfusionSet, ConfusionSetError, clusters  # noqa: F401
from .kinds import BOOK_KINDS, KINDS, PAIR_KINDS, VERSE_KINDS, compile_match  # noqa: F401
