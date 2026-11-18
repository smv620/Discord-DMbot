"""Recognising "how far" and "which way" questions about two places (#4670, MapBot layers slice
4b; docs/design/mapbot_layers.md §9). Pure: no Discord, no database, no AI. A strict list of
shapes, so anything else goes to the usual answer: "how far can I jump", "which way did he go" and
"how far to the next town" are not taken (one name each side, both needed).

The question is first cleaned of the wake phrase and politeness, as the sidebar does; what is left
must be one of the shapes below. The names are returned as said; which places they are is the
store's (an exact or kept other name of a kept place only, as for pins)."""

from __future__ import annotations

import re
from dataclasses import dataclass

FAR, WAY, LONG = "far", "way", "long"
# How the party travels and how fast (#4260 slice 10a): said after the places, or not at all
FOOT, MOUNTED, WAGON, BOAT = "foot", "mounted", "wagon", "boat"
_HOW = {
    "on foot": FOOT, "walking": FOOT,
    "on horseback": MOUNTED, "by horse": MOUNTED, "riding": MOUNTED, "mounted": MOUNTED,
    "by wagon": WAGON, "by cart": WAGON, "by carriage": WAGON,
    "by boat": BOAT, "by ship": BOAT, "by sea": BOAT, "by river": BOAT,
}  # fmt: skip
_TRAVEL = r"(?:\s+(?P<how>" + "|".join(sorted(_HOW, key=len, reverse=True)) + r"))?"
_PACE = r"(?:\s+at an? (?P<pace>fast|normal|slow) pace)?"

_NAME = r"(?P<{}>[^?,;.!]{{2,80}}?)"
_LEAD = r"^(?:hey\s+)?(?:dm\s*bot[,:]?\s+)?(?:please\s+)?(?:can you tell me\s+)?"
_TAIL = r"(?:\s+(?:please|on the map))?\s*[?.!]*\s*$"
_SHAPES = [
    (FAR, r"how far (?:is it )?from {a} to {b}"),
    (FAR, r"how far (?:away )?is {a} from {b}"),
    (FAR, r"how far is it between {a} and {b}"),
    (FAR, r"what(?:'s| is) the distance (?:from|between) {a} (?:to|and) {b}"),
    (FAR, r"how many (?:miles|feet) (?:is it )?from {a} to {b}"),
    (WAY, r"which (?:way|direction) is {a} from {b}"),
    (WAY, r"what direction is {a} (?:from|of) {b}"),
    (WAY, r"in which direction is {a} from {b}"),
    (
        LONG,
        r"how long (?:will|does|would) it take(?: us| them| the party)? to"
        r" (?:get|travel|go|walk|ride|sail) from {a} to {b}" + _TRAVEL + _PACE,
    ),
    (LONG, r"how long is the (?:trip|journey|walk|ride|road) from {a} to {b}" + _TRAVEL + _PACE),
    (LONG, r"how many days (?:is it )?from {a} to {b}" + _TRAVEL + _PACE),
]
_COMPILED = [
    (kind, re.compile(_LEAD + shape.format(a=_NAME.format("a"), b=_NAME.format("b")) + _TAIL, re.I))
    for kind, shape in _SHAPES
]
_ARTICLE = re.compile(r"^(?:the)\s+", re.I)


@dataclass(frozen=True, slots=True)
class Asked:
    kind: str  # FAR, WAY or LONG
    a: str  # as said: "how far is A from B", "which way is A from B"
    b: str
    how: str = ""  # LONG only: FOOT, MOUNTED, WAGON or BOAT as said, "" if not said
    pace: str = ""  # LONG only: "fast", "normal" or "slow" as said, "" if not said


def read(question: str) -> Asked | None:
    """The question as one of the shapes, or None. Names are trimmed; a leading "the" stays (the
    store tries the name with and without it)."""
    text = " ".join(question.split())
    for kind, pattern in _COMPILED:
        found = pattern.match(text)
        if found is None:
            continue
        a, b = found["a"].strip(), found["b"].strip()
        if a and b and a.casefold() != b.casefold():
            if kind != LONG:
                return Asked(kind, a, b)
            how = _HOW.get((found["how"] or "").casefold(), "")
            return Asked(kind, a, b, how, (found["pace"] or "").casefold())
    return None


def without_article(name: str) -> str:
    return _ARTICLE.sub("", name).strip()
