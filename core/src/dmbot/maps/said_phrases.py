"""A distance or a direction said at the table, between two kept places (#4670, MapBot slice 4c-2;
docs/design/mapbot_layers.md §9, "When the DM's word and a map disagree"; Supervisor's ruling on
#4670). Pure: no Discord, no database, no AI. The live note reads only the DM's own recorded lines
(the caller checks who spoke and their consent); this module only finds what a line states.

Strict on purpose, as `measure_phrase` is: a line is read only when two names the caller knows (the
campaign's kept places, by `name_key`) sit in one of these shapes, so a stray number or "north" in
talk is never taken for a statement:
- a distance: "Targos is 20 miles from Bryn Shander", "Targos lies about 20 miles away from Bryn
  Shander", "it's 20 miles from Targos to Bryn Shander", "20 miles between Targos and Bryn Shander";
- a direction: "Targos is north of Bryn Shander", "Targos lies to the north-east of Bryn Shander",
  "Targos sits due west of Bryn Shander".
The length's words go through `route_length.parse`, so "two leagues" is 6 miles and a range keeps
its lower end; only lengths in miles are kept (a time is never compared, §9).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.maps import disagree
from dmbot.memory import route_length
from dmbot.memory.models import name_key

FAR, WAY = "far", "way"

_NUMBER = r"(?:\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_NUMBER += r"|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
_LENGTH = (
    rf"(?P<len>(?:(?:about|around|roughly|some|nearly|almost)\s+)?{_NUMBER}"
    rf"(?:\s*(?:or|to|-|–)\s*{_NUMBER})?\s+(?:miles?|leagues?))"
)
_DIRECTION = r"(?P<dir>(?:north|south|east|west)(?:[- ]?(?:north|south|east|west)){0,2})"
_IS = r"(?:is|'s|’s|lies|sits|stands)"
_SHAPES = (
    (FAR, rf"^\s*A\s+{_IS}\s+{_LENGTH}\s+(?:away\s+)?from\s+B\s*$"),
    (FAR, rf"^\s*(?:it\s*{_IS}\s+|that\s*{_IS}\s+)?{_LENGTH}\s+from\s+A\s+to\s+B\s*$"),
    (FAR, rf"^\s*(?:it\s*{_IS}\s+|that\s*{_IS}\s+)?{_LENGTH}\s+between\s+A\s+and\s+B\s*$"),
    (FAR, rf"^\s*from\s+A\s+to\s+B\s+{_IS}\s+{_LENGTH}\s*$"),
    (WAY, rf"^\s*A\s+{_IS}\s+(?:(?:to\s+the|due)\s+)?{_DIRECTION}\s+of\s+B\s*$"),
)
_SENTENCES = re.compile(r"[.!?;\n]+")
_CLAUSES = re.compile(r"\s*(?:,|\band\s+then\b|\bbut\b)\s*", re.I)


@dataclass(frozen=True, slots=True)
class Said:
    """One statement: `kind` FAR (miles from a to b) or WAY (a lies `value`° of b, from north)."""

    kind: str
    a_id: str
    b_id: str
    value: float
    words: str  # the length's or the direction's own words ("20 miles", "north-east")


_MAY = re.compile(r"\b(?:miles?|leagues?)\b|\b(?:north|south|east|west)\w*\s+of\b", re.I)


def may_state(line: str) -> bool:
    """A cheap first look, so the live note reads the campaign's places only for a line that could
    state a distance or a direction: a length word, or a direction followed by "of"."""
    return bool(_MAY.search(line))


def _names_in(clause: str, names: Mapping[str, str]) -> list[tuple[int, int, str]]:
    """Where known names sit in the clause (start, end, entry id), longest first so "Bryn Shander"
    wins over "Bryn", never overlapping."""
    found: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    words = list(re.finditer(r"[\w'’-]+", clause))
    spans = [
        (words[i].start(), words[j].end())
        for i in range(len(words))
        for j in range(i, min(i + 6, len(words)))
    ]
    for start, end in sorted(spans, key=lambda s: s[0] - s[1]):
        if any(start < e and s < end for s, e in taken):
            continue
        said = clause[start:end]
        entry = names.get(name_key(said)) or names.get(
            name_key(re.sub(r"^the\s+", "", said, flags=re.I))
        )
        if entry:
            found.append((start, end, entry))
            taken.append((start, end))
    return sorted(found)


def read(line: str, names: Mapping[str, str]) -> list[Said]:
    """Every distance or direction this line states between two of `names` (name_key → entry id),
    in the line's order. Empty when it states none in a set shape."""
    out: list[Said] = []
    for sentence in _SENTENCES.split(line):
        for clause in _CLAUSES.split(sentence):
            at = _names_in(clause, names)
            if len(at) != 2 or at[0][2] == at[1][2]:
                continue
            (s1, e1, first), (s2, e2, second) = at
            shaped = f"{clause[:s1]}A{clause[e1:s2]}B{clause[e2:]}"
            for kind, shape in _SHAPES:
                m = re.match(shape, shaped, re.I)
                if m is None:
                    continue
                if kind == FAR:
                    length = route_length.parse(m.group("len"))
                    miles = (
                        disagree.in_miles(length.amount, length.unit or "")
                        if length is not None and length.amount is not None
                        else None
                    )
                    if miles is not None:
                        out.append(Said(FAR, first, second, miles, length.words if length else ""))
                else:
                    angle = disagree.angle_of(m.group("dir"))
                    if angle is not None:
                        out.append(Said(WAY, first, second, angle, m.group("dir").casefold()))
                break
    return out
