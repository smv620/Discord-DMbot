"""The first meeting's reminder (#4229 slice 4b; Supervisor's ruling on #4229, 2026-11-21, D): when
the DM is asked whether one character knows another and the two have never been connected, a
feeling the DM kept for one's home toward the other's people is cited beside the question:
"🤔 **Mithral Keep** (Tala's home) is unfriendly toward dwarves, and **Brewner** is a dwarf. You
decide how they feel." (the ux-critic's words on #4229).

Worked out each time the question is shown and never stored. Only the DM's kept feelings
(`confirmed`, open) count, never one DMbot only read, and nothing is written: the DM sets how the
two feel. A people's standing never becomes a character's (the owner's rule, 2026-11-12), which is
why this is a reminder beside the card, not a fact on it. Pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory import place_peoples, place_words, standing
from dmbot.memory.standing import LOCATED_IN, MEMBER_OF, OF_PEOPLE

LEAD = "🤔"
YOU_DECIDE = "You decide how they feel."
LINE_MAX = 400  # (a page holds five questions in Discord's 2,000 characters: a long name is cut)
# What each home is to its character, as the bracket says it.
_HOME_WORD = {LOCATED_IN: "home", MEMBER_OF: "group", OF_PEOPLE: "people"}
# Where a character is at home: a place they live in or come from, or a plain "in" (never one
# they only work in, were last seen in, or once lived in).
_HOME_WAYS = (None, place_words.LIVES, place_words.FROM)


@dataclass(frozen=True, slots=True)
class Tie:
    """One live, confirmed connection of a character's: what kind, to whom, and its detail."""

    predicate: str
    object_id: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Kept:
    """One of the DM's open feelings: who holds it, toward whom (an entry, or a lineage key), and
    its score."""

    holder_id: str
    toward_id: str
    toward_lineage: str
    score: float


def _md(text: str) -> str:
    """A name as Discord shows it, not as formatting (the card's own `_md` does the same)."""
    return re.sub(r"([\\*_~`|>])", r"\\\1", text)


def _bare(name: str) -> str:
    """A people's name without a leading "the", so "the" is never said twice."""
    return re.sub(r"^the\s+", "", name, flags=re.I)


def lineage_key(species: str) -> str:
    """The lineage a species name says ("Hill Dwarf" → "dwarf"), or "" for one DMbot doesn't
    know."""
    for word in reversed(species.casefold().replace("(", " ").replace(")", " ").split()):
        if word in place_peoples.LINEAGES:
            return word
    return ""


def homes(ties: Sequence[Tie]) -> list[tuple[str, str]]:
    """The places, groups and peoples a character is at home in, each with what it is to them
    ("home", "group", "people"), in the order given."""
    out: list[tuple[str, str]] = []
    for t in ties:
        if t.predicate == LOCATED_IN:
            way = place_words.way_of(t.detail)
            if (way.key if way else None) not in _HOME_WAYS or (way and way.past):
                continue
        elif t.predicate not in (MEMBER_OF, OF_PEOPLE):
            continue
        if all(t.object_id != seen for seen, _ in out):
            out.append((t.object_id, _HOME_WORD[t.predicate]))
    return out


def one_way(
    viewer: str,
    viewer_ties: Sequence[Tie],
    other: str,
    other_ties: Sequence[Tie],
    other_lineage: str,
    kept: Sequence[Kept],
    names: Mapping[str, str],
) -> str:
    """How the viewer's home feels toward the other's people, or "". The first home with a feeling
    wins, and a feeling toward the other's own people entry comes before one toward their
    lineage. Only names in `names` are said."""
    if viewer not in names or other not in names:
        return ""
    peoples = [t.object_id for t in other_ties if t.predicate == OF_PEOPLE and t.object_id in names]
    for home, what in homes(viewer_ties):
        if home not in names:
            continue
        held = [k for k in kept if k.holder_id == home]
        whose = f"**{_md(names[home])}** ({_md(names[viewer])}'s {what})"
        for people_id in peoples:
            for k in held:
                if k.toward_id == people_id:
                    toward = standing.TOWARD[standing.step_of(k.score)]
                    return (
                        f"{whose} is {toward} the {_md(_bare(names[people_id]))}, and "
                        f"**{_md(names[other])}** is one of them"
                    )
        if other_lineage:
            for k in held:
                if k.toward_lineage == other_lineage:
                    toward = standing.TOWARD[standing.step_of(k.score)]
                    many = place_peoples.LINEAGES[other_lineage][1]
                    a = "an" if other_lineage[0] in "aeiou" else "a"
                    return (
                        f"{whose} is {toward} {many}, and **{_md(names[other])}** is {a} "
                        f"{other_lineage}"
                    )
    return ""


def line(
    a: str,
    b: str,
    ties: Mapping[str, Sequence[Tie]],
    lineages: Mapping[str, str],
    kept: Sequence[Kept],
    names: Mapping[str, str],
) -> str:
    """The whole reminder for two characters, each way round, or "" when neither side's home
    holds a feeling toward the other's people. "You decide" is said once, at the end."""
    said = [
        c
        for x, y in ((a, b), (b, a))
        if (c := one_way(x, ties.get(x, ()), y, ties.get(y, ()), lineages.get(y, ""), kept, names))
    ]
    if not said:
        return ""
    text = f"{LEAD} {'. '.join(said)}. {YOU_DECIDE}"
    if len(text) > LINE_MAX:  # (long names: the whole first clause is kept, then the ending)
        text = f"{LEAD} {said[0]}. {YOU_DECIDE}"
    return text if len(text) <= LINE_MAX else ""


__all__ = ["LEAD", "LINE_MAX", "YOU_DECIDE", "Kept", "Tie", "homes", "line", "lineage_key"]
