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
    from collections.abc import Mapping

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


_RARITIES = re.compile(r"Very Rare|Uncommon|Common|Rare|Legendary|Artifact")


def short_rarity(rarity: str) -> str:
    """The rarity as a short heading word: "uncommon", or "uncommon to very rare" for an item
    that comes in several ("Uncommon (+1), Rare (+2), or Very Rare (+3)"), and nothing for
    "Rarity Varies", which is no rarity."""
    found = _RARITIES.findall(rarity)
    if not found:
        return ""
    low, high = found[0].lower(), found[-1].lower()
    return low if low == high else f"{low} to {high}"


def item_line(entry: Entry) -> str:
    """ "Wondrous Item, uncommon, needs attunement by a Wizard": what the line under an item's
    name says, in plain words."""
    d = entry.details
    kind = str(d.get("category", ""))
    if d.get("subtype"):
        kind += f" ({d['subtype']})"
    parts = [kind, str(d.get("rarity", "")).lower()]
    if d.get("attunement"):
        by = str(d.get("attunement_by", ""))
        parts.append(f"needs attunement by {by}" if by else "needs attunement")
    return ", ".join(p for p in parts if p)


def weapon_line(entry: Entry) -> str:
    """ "Martial Melee, 1d8 Slashing, Versatile (1d10), mastery Sap": the Weapons table's row."""
    d = entry.details
    parts = [str(d.get("category", "")), f"{d.get('damage', '')} {d.get('damage_type', '')}"]
    if d.get("properties"):
        parts.append(str(d["properties"]))
    if d.get("mastery"):  # (the SRD 5.1 has none)
        parts.append(f"mastery {d['mastery']}")
    return ", ".join(p.strip() for p in parts if p.strip())


def armor_line(entry: Entry) -> str:
    """ "Heavy armor, AC 18, needs Strength 15, Disadvantage on Stealth": the Armor table's row."""
    d = entry.details
    what = "Shield" if d.get("category") == "Shield" else f"{d.get('category', '')} armor"
    parts = [what, f"AC {d.get('ac', '')}"]
    if str(d.get("strength", "—")) != "—":
        parts.append(f"needs Strength {str(d['strength']).removeprefix('Str ')}")
    if str(d.get("stealth", "—")) != "—":
        parts.append(f"{d['stealth']} on Stealth")
    return ", ".join(parts)


def _weight_words(d: Mapping[str, object]) -> str:
    """ "3 lb.", or "weight varies"; nothing for a dash (it has none to say)."""
    weight = str(d.get("weight", ""))
    return "weight varies" if weight == "Varies" else "" if weight in ("", "—") else weight


def gear_line(entry: Entry, *, card: bool) -> str:
    """ "Adventuring Gear, 5 GP, 3 lb." for the AI. On the card the heading has the price, so only
    the weight is left ("3 lb."); a mount's or a vehicle's words (its capacity, its weight, its
    speed) are the entry's text, which the card shows, so it has no line."""
    d = entry.details
    weight = _weight_words(d)
    if card:
        return weight if d.get("group") == "Adventuring Gear" else ""
    cost = str(d.get("cost", ""))
    return ", ".join(p for p in (str(d.get("group", "")), cost, weight) if p)


def tool_line(entry: Entry, *, card: bool) -> str:
    """ "Other Tools, Dexterity, 25 GP, 1 lb." for the AI; "25 GP, 1 lb." on the card, whose
    heading has the ability."""
    d = entry.details
    cost = str(d.get("cost", ""))
    cost = "price varies" if cost == "Varies" else cost
    if card:  # the heading has the ability, or (when there is none) the price
        parts = [_weight_words(d)] if not d.get("ability") else [cost, _weight_words(d)]
    else:
        parts = [str(d.get("group", "")), str(d.get("ability", "")), cost, _weight_words(d)]
    return ", ".join(p for p in parts if p)


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
    elif entry.kind == "feature":
        if card:  # the card's heading already says "(feature: Rogue 7)"
            return []
        who = f"{d.get('class', '')} level {d.get('level', '')}".strip()
        found = [("", f"Class feature, {who}" + (f", {d['subclass']}" if has("subclass") else ""))]
    elif entry.kind == "item":
        if card:  # the card's heading already says "(item: Wondrous Item, uncommon)"
            return []
        found = [("", item_line(entry))]
    elif entry.kind == "feat":  # the card's heading already says "(feat: Origin)"
        found = [] if card else [("", f"{d['category']} feat" if has("category") else "Feat")]
        found.append(("Prerequisite", str(d.get("prerequisite", ""))))
    elif entry.kind == "weapon":  # the card shows the whole row as its text
        found = [] if card else [("", weapon_line(entry))]
    elif entry.kind == "armor":
        found = [] if card else [("", armor_line(entry))]
    elif entry.kind == "gear":
        found = [("", gear_line(entry, card=card))]
    elif entry.kind == "tool":
        found = [("", tool_line(entry, card=card))]
    elif entry.kind == "poison":  # the card's heading already says "(poison: Injury)"
        found = [] if card else [("", f"{d.get('type', '')} poison")]
        found.append(("Price", f"{d.get('price', '')} a dose" if has("price") else ""))
    elif entry.kind == "trap":
        found = [
            # (the heading says "trap"; the 5.1 gives the kind, "Mechanical trap", not levels)
            ("", str(d.get("severity") or d.get("type", "")).replace(" Trap", "")),
            ("Trigger", str(d.get("trigger", ""))),
            ("Duration", str(d.get("duration", ""))),
        ]
    elif entry.kind == "species":
        found = [("", f"Size: {d.get('size', '')}"), ("", f"Speed: {d.get('speed', '')}")]
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


_CARD_COLONS = frozenset(
    {"Casting time", "Range", "Components", "Duration", "Prerequisite", "Trigger", "Price"}
)


def full_text(entry: Entry) -> str:
    """The facts and then the text: everything DMbot knows about the entry, to search, quote or
    check an answer against."""
    header = line(entry)
    return f"{header.rstrip('.')}. {entry.text}" if header else entry.text


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
