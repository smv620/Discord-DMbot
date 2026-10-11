"""An entry's metadata, in one place (#1063): a spell's level, casting time, range, components
and duration; a creature's size and type, AC, HP, speed, CR, senses and initiative.

The SRD keeps these apart from an entry's text, and "what's the range of fireball" was answered
"not stated" because the sidebar read only the text (#1039). Owner, 2026-10-11: an entry's
metadata is part of the entry wherever DMbot reads, searches, quotes or checks it. So every
reader asks `Entry.facts` (the header line) or `Entry.full_text` (the header and the text)
instead of choosing for itself, and this module is the only place that knows which fields
exist. A field an entry lacks is left out; a condition has none.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dmbot.rules.index import Entry

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}
_COMPONENT_WORDS = {"V": "Verbal", "S": "Somatic", "M": "Material"}
# Phrases that name a field in a question, for the fallback that answers with that field alone.
# Whole words only (the question is split into words first), and nothing that is also everyday
# talk ("last", "reach", "cr"): a wrong field would be stated as fact with an SRD page.
ASKED_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Casting time", ("casting time", "cast time", "take to cast", "how long to cast")),
    ("Range", ("range", "how far")),
    ("Duration", ("duration", "how long does", "how long do", "how long will", "how long is")),
    ("Components", ("components", "component")),
    ("AC", ("ac", "armor class")),
    ("HP", ("hp", "hit points")),
    ("Speed", ("speed", "how fast")),
    ("CR", ("challenge rating",)),
)
_WORDS = re.compile(r"[a-z']+")


def level_words(entry: Entry) -> str:
    """ "3rd-level Evocation", "Evocation cantrip", "1st-level Divination (ritual)"."""
    level, school = int(entry.details.get("level", 0)), str(entry.details.get("school", ""))
    ritual = " (ritual)" if entry.details.get("ritual") else ""
    if level == 0:
        return f"{school} cantrip"
    return f"{_ORDINALS.get(level, f'{level}th')}-level {school}{ritual}"


def components_words(components: str) -> str:
    """ "V, S, M (a ball of bat guano)" in words: "Verbal, Somatic, Material (a ball of bat
    guano)". The letters alone make an answer that says "verbal" sound like a guess."""
    letters, paren, rest = components.partition("(")
    words = ", ".join(_COMPONENT_WORDS.get(p.strip(), p.strip()) for p in letters.split(","))
    return f"{words} ({rest}" if paren else words


def pairs(entry: Entry, *, card: bool = False) -> list[tuple[str, str]]:
    """(label, value) for each fact, in order; a label of "" stands for a value on its own.
    `card=False` is the words the AI reads (components spelled out, plus a creature's senses and
    initiative); `card=True` is what the rule card shows (ordinal level, components as written)."""
    d = entry.details

    def has(key: str) -> bool:
        return d.get(key) not in (None, "")

    if entry.kind == "spell":
        if card:
            kind = level_words(entry)
        else:
            level, school = int(d.get("level", 0)), str(d.get("school", ""))
            kind = f"Level {level} {school}".strip() if level else f"{school} cantrip".strip()
        found = [
            ("", kind),
            ("Casting time", str(d.get("casting_time", ""))),
            ("Range", str(d.get("range", ""))),
            (
                "Components",
                str(d.get("components", ""))
                if card
                else components_words(str(d.get("components", ""))),
            ),
            ("Duration", str(d.get("duration", ""))),
        ]
    elif entry.kind == "monster":
        found = [
            ("", f"{d.get('size', '')} {d.get('type', '')}".strip()),
            ("AC", str(d["ac"]) if has("ac") else ""),
            ("HP", f"{d['hp']} ({d['hit_dice']})" if has("hp") and has("hit_dice") else ""),
            ("Speed", str(d.get("speed", ""))),
            ("CR", str(d.get("cr", ""))),
        ]
        if not card:
            found += [
                ("Senses", str(d.get("senses", ""))),
                ("Initiative", str(d.get("initiative", ""))),
            ]
    else:
        return []
    return [(label, value) for label, value in found if value]


def line(entry: Entry) -> str:
    """The facts as one plain line for the AI: "Level 3 Evocation; Casting time Action; ..."."""
    return "; ".join(f"{label} {value}".strip() for label, value in pairs(entry))


def card_parts(entry: Entry) -> list[str]:
    """The facts as the rule card shows them, one string each (unescaped; the card escapes and
    joins them): ["3rd-level Evocation", "Casting time: Action", ...]. Pinned by a test."""
    return [
        f"{label}: {value}" if label in _CARD_COLONS else f"{label} {value}".strip()
        for label, value in pairs(entry, card=True)
    ]


_CARD_COLONS = frozenset({"Casting time", "Range", "Components", "Duration"})


def full_text(entry: Entry) -> str:
    """The facts and then the text: everything DMbot knows about the entry, to search, quote or
    check an answer against."""
    header = line(entry)
    return f"{header}. {entry.text}" if header else entry.text


def asked_field(entry: Entry, question: str) -> str | None:
    """The one fact the question asks for ("Range 150 feet"), or None. Used when the model failed
    and the entry itself must answer: a question about a field gets the field, not the
    description. A question that names two fields, or none the entry has, gets None (the
    description is the safer answer than the wrong field)."""
    words = _WORDS.findall(question.lower())
    asked = f" {' '.join(words)} "
    have = dict(pairs(entry))
    found = []
    for label, phrases in ASKED_FIELDS:
        if label not in have:
            continue
        if label == "Duration" and "cast" in words:
            continue  # "how long does it take to cast" is the casting time, not the duration
        if any(f" {p} " in asked for p in phrases):
            found.append(label)
    return f"{found[0]} {have[found[0]]}" if len(found) == 1 else None
