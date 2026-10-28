"""A person the story describes but never names (#2790, section 3). Pure.

"Ken went to meet his sister at the lake" says Ken has a sister and nothing about who she is.
The relationship is a stated fact, so it is kept, with a new entry for her that has no name: it
hangs off Ken (`anchor`), the story's word for her is "sister" (`what`) and the relationship is
sibling (`role`). The label shown to the DM is built from those parts every time, so renaming or
merging Ken never leaves "Old Ken's sister" behind. Nothing here guesses a name: the DM gives one
later ("Give them a name"), and DMbot only ever proposes it as a question.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from dmbot.memory.models import name_key

SUFFIX = "(name unknown)"
GROUP = "Not named yet"  # the heading on the name list
MAX_WHAT = 100
MAX_ROLE = 40
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)


def tidy(what: str) -> str:
    """The story's word for them, in one line: "sister", "a hooded figure"."""
    return " ".join(what.split())[:MAX_WHAT].strip()


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def label(what: str, anchor_name: str | None = None) -> str:
    """ "Ken's sister (name unknown)", or "a hooded figure (name unknown)" with nobody to hang
    them off."""
    word = tidy(what)
    if anchor_name:
        return f"{possessive(anchor_name)} {_ARTICLE.sub('', word)} {SUFFIX}"
    return f"{word} {SUFFIX}"


def same_person_key(what: str, role: str | None) -> str:
    """What makes two descriptions the same person when they hang off the same entry: the same
    relationship and the same word, ignoring case, an article and punctuation. "His other sister"
    is a different word, so it is a different person."""
    return f"{(role or '').casefold()}|{name_key(_ARTICLE.sub('', tidy(what)))}"


def shown(what: str, anchor_id: str | None, names: Mapping[str, str]) -> str:
    """The label for display, with the anchor's name as it is now (`names`: entry id -> name). An
    anchor that cannot be found is left out rather than named wrongly."""
    return label(what, names.get(anchor_id) if anchor_id else None)
