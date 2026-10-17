"""A clue only some people know (#1649; docs/STORY_MEMORY.md, section 2): the words and the pure
rule. A `clue` is an entry of its own. The DM can switch on "Only some people know this", and then
who knows it is the clue's confirmed `knows` facts. After a session, a claim that has a character
outside that list speaking of or using the clue is a heads-up, never a verdict: a missing `knows`
means DMbot doesn't know, not that the character doesn't, so a clue with the switch off never
warns about anyone.
"""

from __future__ import annotations

from collections.abc import Collection

CLUE = "clue"
KNOWS = "knows"

ONLY_CLUES = "Only a clue or secret can have a list of who knows it."
ONLY_CHARACTERS = "Only a character can know a clue."
NEEDS_SOMEONE = "Add at least one character who knows it first, then try again."
TURNED_OFF = "Nobody is left on the list, so this clue is open to everyone again."

# Words in a claim's relationship that say the character passed the clue on or used it: the
# heads-up is then "likely", not just "possible" (the table can't have learned it by chance).
_TOLD = frozenset(
    {
        "told", "tell", "tells", "telling", "revealed", "reveals", "reveal", "shared", "shares",
        "share", "whispered", "whispers", "confided", "admitted", "explained", "warned",
        "used", "uses", "use", "using", "acted", "cited", "quoted", "said", "says",
        "mentioned", "mentions", "spoke", "speaks",
    }
)  # fmt: skip


def says_they_acted_on_it(relationship: str) -> bool:
    """Does the claim say the character told the clue to someone or used it?"""
    return any(word in _TOLD for word in relationship.lower().replace("-", " ").split())


def outside_audience(character_id: str, audience: Collection[str]) -> bool:
    """Is this character not among those the DM listed? (Only called for a limited clue.)"""
    return character_id not in audience
