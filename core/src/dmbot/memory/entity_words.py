"""How an entry reads inside a sentence (#5173, session names slice 6; Supervisor's ruling point 6).

A name reads as it is ("Bryn Shander"). A thing or a place that has no name of its own carries the
words the story said it in (`Entity.described`: "gemstone mine", "Easthaven ferry", "Garret's old
sled", without an article), and reads as the story said it: "the gemstone mine", never as if it
were a proper name. Everything that writes an entry into a sentence (the recap writer's names,
answers, cards) goes through `in_sentence`, so they all say it the same way. Pure: no store, no
Discord.
"""

from __future__ import annotations

import re

_ARTICLES = ("the ", "a ", "an ", "this ", "that ")
# A possessive first word ("Garret's old sled", "the guards' hut"): no "the" before it (the
# reviewer, on #5173)
_POSSESSIVE = re.compile(r"^\S+(?:['’]s|s['’])(?:\s|$)", re.IGNORECASE)


def in_sentence(name: str, described: str | None) -> str:
    """The entry's words inside a sentence: a name as it is; a description (`described`, the words
    as the story said them) with "the" before it, unless it starts with an article or a
    possessive. The words are never re-cased: they are the story's own."""
    if described is None:
        return " ".join(name.split())
    said = " ".join(described.split())
    if not said:
        return " ".join(name.split())
    if _POSSESSIVE.match(said):
        return said
    if said.casefold().startswith(_ARTICLES):
        return said[:1].lower() + said[1:]
    return f"the {said}"
