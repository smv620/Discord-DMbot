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

FAR, WAY = "far", "way"

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
]
_COMPILED = [
    (kind, re.compile(_LEAD + shape.format(a=_NAME.format("a"), b=_NAME.format("b")) + _TAIL, re.I))
    for kind, shape in _SHAPES
]
_ARTICLE = re.compile(r"^(?:the)\s+", re.I)


@dataclass(frozen=True, slots=True)
class Asked:
    kind: str  # FAR or WAY
    a: str  # as said: "how far is A from B", "which way is A from B"
    b: str


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
            return Asked(kind, a, b)
    return None


def without_article(name: str) -> str:
    return _ARTICLE.sub("", name).strip()
