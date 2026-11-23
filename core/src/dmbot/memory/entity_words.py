"""How an entry reads inside a sentence (#5173, session names slice 6; Supervisor's ruling point 6).

A name reads as it is ("Bryn Shander"). A description the story gave a thing or a place that has no
name of its own (`Entity.described`: "Gemstone mine") reads as the story said it: "the gemstone
mine", never as if it were a proper name. Everything that writes an entry into a sentence (the
recap writer's names, answers, cards) goes through `in_sentence`, so they all say it the same way.
Pure: no store, no Discord.
"""

from __future__ import annotations

_ARTICLES = ("the ", "a ", "an ", "this ", "that ")


def in_sentence(name: str, described: bool) -> str:
    """The entry's words inside a sentence: a name as it is; a description lower-cased on its first
    letter (its only capital, `singled_out.proposed`) with "the" before it, unless it starts with an
    article already."""
    text = " ".join(name.split())
    if not described or not text:
        return text
    text = text[:1].lower() + text[1:]
    return text if text.casefold().startswith(_ARTICLES) else f"the {text}"
