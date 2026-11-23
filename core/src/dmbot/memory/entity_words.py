"""How an entry reads inside a sentence (#5173, session names slice 6; Supervisor's ruling point 6).

A name reads as it is ("Bryn Shander"). A description the story gave a thing or a place that has no
name of its own (`Entity.described`: "Gemstone mine") reads as the story said it: "the gemstone
mine", never as if it were a proper name. Everything that writes an entry into a sentence (the
recap writer's names, answers, cards) goes through `in_sentence`, so they all say it the same way.
Pure: no store, no Discord.
"""

from __future__ import annotations

import re

_ARTICLES = ("the ", "a ", "an ", "this ", "that ")
# A possessive first word ("Garret's old sled", "the guards' hut"): the story's own capital, and no
# "the" before it (the reviewer, on #5173)
_POSSESSIVE = re.compile(r"^\S+(?:['’]s|s['’])(?:\s|$)", re.IGNORECASE)


def as_said(name: str) -> str:
    """A description's words as the story said them, without an article: DMbot's proposal gives
    it one capital (`singled_out.proposed`), taken back here. Words the story capitalised stay: a
    possessive first word ("Garret's old sled"), or a capital later ("old Bryn Shander gate")."""
    text = " ".join(name.split())
    if not text or _POSSESSIVE.match(text) or any(w[:1].isupper() for w in text.split()[1:]):
        return text
    return text[:1].lower() + text[1:]


def in_sentence(name: str, described: bool) -> str:
    """The entry's words inside a sentence: a name as it is; a description as the story said it
    (`as_said`), with "the" before it unless it starts with an article or a possessive."""
    text = " ".join(name.split())
    if not described or not text:
        return text
    said = as_said(text)
    if _POSSESSIVE.match(said):
        return said
    if said.casefold().startswith(_ARTICLES):
        return said[:1].lower() + said[1:]
    return f"the {said}"
