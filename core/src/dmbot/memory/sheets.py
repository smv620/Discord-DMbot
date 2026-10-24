"""D&D Beyond character sheets (docs/PLAN.md, "D&D Beyond character sheets"; #723).

A player links their own public sheet. DMbot keeps a small snapshot per character per
campaign, made by `parse` from the character service's answer. The parser is an allow
list: it builds the snapshot from named fields only, so nothing else (descriptions,
rules text, notes, pictures) can ever be stored (CLAUDE.md, IP rule). `clean` checks a
snapshot from anywhere else (the database, a backup file, the typed fallback form)
against the same shape, so a hand-edited backup can't smuggle text in either.

D&D Beyond has no official API: the address is the unofficial one its own pages use, and
may change. DMbot never scrapes pages, sends no password or cookie, reads only sheets
their players made public, and fetches rarely (once per character per session).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import aiohttp

from dmbot.memory.models import name_key

SOURCE_DNDBEYOND = "dndbeyond"
SOURCE_TYPED = "typed"
# A typed sheet whose fields were read from the player's own document by the AI (#1125). It is
# "typed" for editing (no link), but is checked like a full sheet.
ORIGIN_DOCUMENT = "document"
SNAPSHOT_VERSION = 1

API = "https://character-service.dndbeyond.com/character/v5/character/{id}"
USER_AGENT = "DMbot (Discord bot for tabletop games; https://github.com/smv620/Discord-DMbot)"
FETCH_TIMEOUT_S = 10
MAX_BYTES = 2 * 1024 * 1024  # a big sheet is a few hundred KB
FETCHES_AT_ONCE = 2  # sheets read at once, across all servers
READS_PER_MINUTE = 20  # D&D Beyond reads a minute, across all servers (a token bucket)
PAUSE_DEFAULT_S = 60.0  # how long every read waits after a 429 or 5xx with no Retry-After
PAUSE_MAX_S = 900.0  # never longer than this, whatever D&D Beyond asks
HOLD_MAX_S = 5.0  # a read held by the limit this long or less waits; longer is refused as busy
SNAPSHOT_MAX_BYTES = 32 * 1024  # a kept snapshot (the database allows 64 KB)
SHEET_URL = "https://www.dndbeyond.com/characters/{id}"
_LINK = re.compile(
    r"^\s*<?https://(?:www\.)?dndbeyond\.com/characters/(\d{1,12})(?:[/?#][^\s>]*)?>?\s*$",
    re.IGNORECASE,
)

NAME_MAX = 60  # a spell, feature or item name; longer is likely text, and dropped
LIST_MAX = 100  # of each kind of name
TYPED_NAMES_MAX = 20  # spell or feature names on the fallback form
LEVEL_MAX = 20
SLOT_LEVEL_MAX = 9  # spell slots exist for levels 1 to 9
SLOT_MAX = 99
SCORE_MAX = 30
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_NAMES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
SKILLS = (
    "acrobatics", "animal-handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight-of-hand", "stealth", "survival",
)  # fmt: skip
SENSES = ("darkvision", "blindsight", "tremorsense", "truesight")
_NAME_LISTS = ("spells", "features", "feats", "items")
# The "full" part of the sheet (#1124): more lists of names, kept as names only (never
# descriptions or rules text). They are trimmed with the others when a snapshot is too big.
_FULL_LISTS = (
    "conditions", "resistances", "immunities", "vulnerabilities", "expertise", "tools",
    "weapon_profs", "armor_profs",
)  # fmt: skip
# D&D Beyond's numbers for the conditions (its own `conditions` list on a character).
CONDITION_IDS = {
    1: "Blinded", 2: "Charmed", 3: "Deafened", 4: "Exhaustion", 5: "Frightened", 6: "Grappled",
    7: "Incapacitated", 8: "Invisible", 9: "Paralyzed", 10: "Petrified", 11: "Poisoned",
    12: "Prone", 13: "Restrained", 14: "Stunned", 15: "Unconscious",
}  # fmt: skip
CONDITIONS = tuple(CONDITION_IDS.values())
EXHAUSTION_MAX = 6
RARITIES = ("common", "uncommon", "rare", "very rare", "legendary", "artifact")
_ARMOR_KINDS = frozenset(["light-armor", "medium-armor", "heavy-armor", "shields"])
_WEAPON_KINDS = frozenset(["simple-weapons", "martial-weapons"])
# Tools are proficiency modifiers whose subtype ends like this ("thieves-tools",
# "herbalism-kit", "dice-set", "navigators-tools", "land-vehicles", "lute").
_TOOL_ENDINGS = ("-tools", "-supplies", "-kit", "-set", "-vehicles", "-instrument", "-utensils")
FLAG_PARTS = ("basic", "abilities", "inventory")  # parts a bad number can be reported for
INVENTORY_MAX = 5000  # items; a number outside this is not a count of items
WEIGHT_MAX = 100_000.0

NOT_A_LINK = (
    "That doesn't look like a D&D Beyond character link. Open your character on D&D "
    "Beyond and copy the address from the top of the page."
)
BUSY = "D&D Beyond is busy just now. DMbot will try again in a little while."
NOT_PUBLIC = (
    "DMbot can't read that sheet yet. On D&D Beyond:\n"
    "1. Open the character and press **Edit**.\n"
    "2. Press **Settings**.\n"
    "3. Set **Character Privacy** to **Public**.\n"
    "Then press **Link my D&D Beyond sheet** again, or just wait: DMbot tries again when the "
    "next session starts."
)


class SheetError(Exception):
    """A sheet couldn't be read. `refused` when D&D Beyond refused (the sheet isn't
    public, or there's no such character): the player can fix that. `limited` when D&D
    Beyond asked us to slow down (429) or had a server problem (5xx), or our own limit held the
    read back: not the sheet's fault, so nobody is penalised for it; `status` is the answer's
    code and `retry_after` the seconds to wait."""

    def __init__(
        self,
        message: str,
        *,
        refused: bool = False,
        limited: bool = False,
        status: int | None = None,
        retry_after: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.refused = refused
        self.limited = limited
        self.status = status
        self.retry_after = retry_after


def character_id(link: str) -> int | None:
    """The character's number from a D&D Beyond character link, or None. Anything after
    the number (a share path, a query) is dropped."""
    match = _LINK.match(link)
    return int(match.group(1)) if match else None


def sheet_url(character: int) -> str:
    """The link as DMbot keeps and shows it."""
    return SHEET_URL.format(id=int(character))


# ---- reading the character service's answer ------------------------------------------


def parse(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The snapshot from the character service's JSON. Only the fields named here are
    read, and only names and numbers come out."""
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else answer
    assert isinstance(data, Mapping)
    modifiers = list(_modifiers(data))
    classes = [c for c in (_class(entry) for entry in _list(data.get("classes"))) if c is not None]
    level = min(LEVEL_MAX, sum(c["level"] for c in classes))
    scores = _scores(data, modifiers)
    con = scores.get("con")
    snapshot: dict[str, Any] = {
        "v": SNAPSHOT_VERSION,
        "source": SOURCE_DNDBEYOND,
        "name": _name(data.get("name")),
        "species": _name(_get(data, "race", "fullName")),
        "classes": classes,
        "level": level,
        "abilities": scores,
        "max_hp": _max_hp(data, level, con),
        "speed": _int(_get(data, "race", "weightSpeeds", "normal", "walk"), 0, 200),
        "proficiency_bonus": 2 + (max(level, 1) - 1) // 4 if classes else None,
        "saves": sorted(
            {
                ABILITIES[i]
                for m in modifiers
                if m.get("type") == "proficiency"
                for i, name in enumerate(_ABILITY_NAMES)
                if m.get("subType") == f"{name}-saving-throws"
            },
            key=ABILITIES.index,
        ),
        "skills": sorted(
            {
                str(m["subType"])
                for m in modifiers
                if m.get("type") in ("proficiency", "expertise") and m.get("subType") in SKILLS
            }
        ),
        "senses": _senses(modifiers),
        "languages": _names(
            m.get("friendlySubtypeName") for m in modifiers if m.get("type") == "language"
        ),
        "spells": _names(_spell_names(data)),
        "features": _names(
            [
                *(_get(t, "definition", "name") for t in _list(_get(data, "race", "racialTraits"))),
                *(
                    _get(f, "definition", "name")
                    for c in _list(data.get("classes"))
                    for f in _list(_get(c, "classFeatures"))
                ),
            ]
        ),
        "feats": _names(_get(f, "definition", "name") for f in _list(data.get("feats"))),
        "items": _names(_get(i, "definition", "name") for i in _list(data.get("inventory"))),
    }
    snapshot["ac"] = _armour_class(data, scores, modifiers)
    snapshot.update(_full(data, modifiers, snapshot["max_hp"]))
    snapshot["flags"] = _flags(data)
    cleaned = clean(snapshot)
    if cleaned is None:
        raise SheetError("The character service's answer had no character in it.")
    return cleaned


def _full(
    data: Mapping[str, Any], modifiers: list[Mapping[str, Any]], max_hp: int | None
) -> dict[str, Any]:
    """The rest of the allow list (#1124): hit points now, conditions, defenses, skill
    expertise, proficiencies, background and the inventory's size. Names and numbers only."""
    # D&D Beyond keeps damage taken (`removedHitPoints`), not the hit points left: what is
    # left is the maximum minus the damage, never below 0.
    removed = _int(data.get("removedHitPoints"), 0, 99999)
    hp = None if max_hp is None or removed is None else max(0, max_hp - removed)
    conditions: list[str] = []
    exhaustion = 0
    for entry in _list(data.get("conditions")):
        name = CONDITION_IDS.get(_int(_get(entry, "id"), 1, 99) or 0)
        if name is not None and name not in conditions:
            conditions.append(name)
        if name == "Exhaustion":
            exhaustion = _int(_get(entry, "level"), 0, EXHAUSTION_MAX) or 0
    proficiencies = [m for m in modifiers if m.get("type") == "proficiency"]
    return {
        "hp": hp,
        "temp_hp": _int(data.get("temporaryHitPoints"), 0, 9999) or 0,
        "conditions": conditions,
        "exhaustion": exhaustion,
        "resistances": _modifier_names(modifiers, "resistance"),
        "immunities": _modifier_names(modifiers, "immunity"),
        "vulnerabilities": _modifier_names(modifiers, "vulnerability"),
        "expertise": sorted(
            {
                str(m["subType"])
                for m in modifiers
                if m.get("type") == "expertise" and m.get("subType") in SKILLS
            }
        ),
        "tools": _names(
            m.get("friendlySubtypeName") for m in proficiencies if _sub(m).endswith(_TOOL_ENDINGS)
        ),
        "weapon_profs": _names(
            m.get("friendlySubtypeName")
            for m in proficiencies
            if _sub(m) in _WEAPON_KINDS or _sub(m).endswith("-weapons")
        ),
        "armor_profs": _names(
            m.get("friendlySubtypeName") for m in proficiencies if _sub(m) in _ARMOR_KINDS
        ),
        "background": _name(_get(data, "background", "definition", "name"))
        or _name(_get(data, "background", "customBackground", "name")),
        "inventory": _inventory(data),
        "slots": _slots(data),
        "pact": _pact(data),
    }


# Spell slots a character has by caster level (the multiclass spellcaster table, SRD 5.2):
# the slots for levels 1 to 9. D&D Beyond's `available` may be 0 for an ordinary caster, whose
# total it works out from the class (`spellRules.levelSpellSlots`), so the total is worked out
# here from the same data when `available` has none (#1176).
_FULL_CASTER = (
    (), (2,), (3,), (4, 2), (4, 3), (4, 3, 2), (4, 3, 3), (4, 3, 3, 1), (4, 3, 3, 2),
    (4, 3, 3, 3, 1), (4, 3, 3, 3, 2), (4, 3, 3, 3, 2, 1), (4, 3, 3, 3, 2, 1),
    (4, 3, 3, 3, 2, 1, 1), (4, 3, 3, 3, 2, 1, 1), (4, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 2, 1, 1, 1), (4, 3, 3, 3, 2, 1, 1, 1, 1), (4, 3, 3, 3, 3, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 1, 1, 1), (4, 3, 3, 3, 3, 2, 2, 1, 1),
)  # fmt: skip


def _class_rows(data: Mapping[str, Any]) -> list[tuple[str, int, int, list[Any]]]:
    """(name, level, multiclass divisor, slot rows) for each class that has a slot table."""
    out = []
    for entry in _list(data.get("classes")):
        rows = _list(_get(entry, "definition", "spellRules", "levelSpellSlots"))
        level = _int(_get(entry, "level"), 1, LEVEL_MAX)
        if not rows or level is None:
            continue
        divisor = _int(_get(entry, "definition", "spellRules", "multiClassSpellSlotDivisor"), 0, 3)
        out.append((str(_get(entry, "definition", "name") or ""), level, divisor or 1, rows))
    return out


def _computed_totals(data: Mapping[str, Any]) -> dict[int, int]:
    """The slots the character's classes give, by spell level: one casting class uses its own
    table; two or more use the multiclass table. A warlock's pact slots are counted apart."""
    casters = [c for c in _class_rows(data) if c[0].casefold() != "warlock"]
    if len(casters) == 1:
        _, level, _, rows = casters[0]
        row = _list(rows[level]) if level < len(rows) else []
        return {i + 1: n for i, v in enumerate(row[:9]) if (n := _int(v, 1, SLOT_MAX))}
    if len(casters) > 1:
        # The 2024 rule: half casters round up, third casters down; full casters count all.
        effective = sum(
            -(-level // divisor) if divisor == 2 else level // divisor
            for _, level, divisor, _ in casters
        )
        table = _FULL_CASTER[min(effective, LEVEL_MAX)]
        return {i + 1: n for i, n in enumerate(table)}
    return {}


def _slots(data: Mapping[str, Any]) -> list[dict[str, int]]:
    """Spell slots for levels 1 to 9: how many there are and how many are used. The total is
    D&D Beyond's `available` when it has one, else worked out from the classes."""
    used: dict[int, int] = {}
    reported: dict[int, int] = {}
    for entry in _list(data.get("spellSlots")):
        level = _int(_get(entry, "level"), 1, SLOT_LEVEL_MAX)
        if level is None:
            continue
        reported[level] = _int(_get(entry, "available"), 0, SLOT_MAX) or 0
        used[level] = _int(_get(entry, "used"), 0, SLOT_MAX) or 0
    totals = _computed_totals(data)
    return clean_slots(
        [
            {
                "level": level,
                "max": reported.get(level) or totals.get(level, 0),
                "used": used.get(level, 0),
            }
            for level in range(1, SLOT_LEVEL_MAX + 1)
        ]
    )


def _pact(data: Mapping[str, Any]) -> dict[str, int] | None:
    """A warlock's pact slots (kept apart: they come back on a short rest): the level they
    are cast at, how many there are and how many are used. Worked out from the warlock's
    own table when D&D Beyond's `pactMagic` has no total."""
    used = 0
    level = total = None
    for entry in _list(data.get("pactMagic")):
        available = _int(_get(entry, "available"), 0, SLOT_MAX) or 0
        used = max(used, _int(_get(entry, "used"), 0, SLOT_MAX) or 0)
        if available:
            level, total = _int(_get(entry, "level"), 1, SLOT_LEVEL_MAX), available
    if total is None:
        for name, class_level, _, rows in _class_rows(data):
            row = _list(rows[class_level]) if class_level < len(rows) else []
            if name.casefold() == "warlock":
                for i, v in enumerate(row[:9]):
                    if _int(v, 1, SLOT_MAX):
                        level, total = i + 1, _int(v, 1, SLOT_MAX)
    return clean_pact({"level": level, "max": total, "used": used})


def clean_pact(value: Any) -> dict[str, int] | None:
    level = _int(_get(value, "level"), 1, SLOT_LEVEL_MAX)
    top = _int(_get(value, "max"), 1, SLOT_MAX)
    if level is None or top is None:
        return None
    return {
        "level": level,
        "max": top,
        "used": min(_int(_get(value, "used"), 0, SLOT_MAX) or 0, top),
    }


def clean_slots(value: Any) -> list[dict[str, int]]:
    """Spell slots, numbers only (#1176): for each level 1 to 9 how many the character has
    (`max`) and how many are used. D&D Beyond calls these `available` and `used`; the
    snapshot's own shape (`max`, `used`) is accepted as well. A level with none is left out,
    and used is never above max. Anything that isn't that shape is dropped."""
    best: dict[int, dict[str, int]] = {}
    for entry in _list(value):
        level = _int(_get(entry, "level"), 1, SLOT_LEVEL_MAX)
        top = _int(
            _get(entry, "max") if _get(entry, "max") is not None else _get(entry, "available"),
            1,
            SLOT_MAX,
        )
        if level is None or top is None:
            continue
        used = min(_int(_get(entry, "used"), 0, SLOT_MAX) or 0, top)
        best[level] = {"level": level, "max": top, "used": used}
    return [best[k] for k in sorted(best)]


def _sub(modifier: Mapping[str, Any]) -> str:
    """A modifier's subtype as text, "" if it isn't text (an odd shape never breaks a read)."""
    value = modifier.get("subType")
    return value if isinstance(value, str) else ""


def _flags(data: Mapping[str, Any]) -> list[str]:
    """Which parts had a number that can't be right (a negative score, damage or amount, a
    class level under 1), so the import status can say ⛔ for them even though `clean` drops
    the bad number. Part names only."""

    def negative(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0

    flags: set[str] = set()
    if any(
        negative(data.get(k)) for k in ("removedHitPoints", "temporaryHitPoints", "baseHitPoints")
    ) or any(
        negative(_get(c, "level"))
        or (_get(c, "level") == 0 and not isinstance(_get(c, "level"), bool))
        for c in _list(data.get("classes"))
    ):
        flags.add("basic")
    if any(
        negative(_get(s, "value"))
        for key in ("stats", "bonusStats", "overrideStats")
        for s in _list(data.get(key))
    ):
        flags.add("abilities")
    if any(
        negative(i.get("quantity")) for i in _list(data.get("inventory")) if isinstance(i, Mapping)
    ):
        flags.add("inventory")
    return sorted(flags)


def _weight(value: Any) -> float | None:
    """A weight in pounds, or None. The range is checked before the number is made a float:
    a huge integer would raise OverflowError and lose the whole sheet."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not 0 <= value <= WEIGHT_MAX:
        return None
    return float(value)


def _modifier_names(modifiers: list[Mapping[str, Any]], kind: str) -> list[str]:
    return _names(
        str(m["friendlySubtypeName"]).capitalize()
        for m in modifiers
        if m.get("type") == kind and isinstance(m.get("friendlySubtypeName"), str)
    )


def _inventory(data: Mapping[str, Any]) -> dict[str, Any]:
    """How many things, what they weigh and how many of each rarity, from the sheet's
    inventory. Counted per line of the inventory (20 arrows are one item). The weight is
    quantity times weight, divided by the bundle size, per line (arrows are weighed per
    bundle). A line with no rarity is common (D&D Beyond says "Common" for ordinary gear)."""
    count, weight = 0, 0.0
    rarity = dict.fromkeys(RARITIES, 0)
    for item in _list(data.get("inventory")):
        if not isinstance(item, Mapping):
            continue
        count += 1
        quantity = _int(item.get("quantity"), 0, 100_000)
        quantity = 1 if quantity is None else quantity  # no amount given: one
        each = _weight(_get(item, "definition", "weight"))
        bundle = _int(_get(item, "definition", "bundleSize"), 1, 1000) or 1
        if each is not None:
            weight += each * quantity / bundle
        word = _get(item, "definition", "rarity")
        key = word.strip().lower() if isinstance(word, str) else "common"
        rarity[key if key in rarity else "common"] += 1
    return {
        "count": min(count, INVENTORY_MAX),
        "weight": round(min(weight, WEIGHT_MAX), 2),
        "rarity": rarity,
    }


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    number = int(value)
    return number if low <= number <= high else None


def _name(value: Any) -> str | None:
    """A short single-line name, or None (anything long is likely text, not a name)."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())  # (a name is never shortened, #2607: a cut one is another name)
    return text if 0 < len(text) <= NAME_MAX else None


def _names(values: Iterable[Any]) -> list[str]:
    """Distinct names in the order given, at most LIST_MAX."""
    out: dict[str, str] = {}
    for value in values:
        name = _name(value)
        if name is not None:
            out.setdefault(name_key(name), name)
        if len(out) >= LIST_MAX:
            break
    return list(out.values())


def _modifiers(data: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    groups = data.get("modifiers")
    if not isinstance(groups, Mapping):
        return
    for group in groups.values():
        for modifier in _list(group):
            if isinstance(modifier, Mapping):
                yield modifier


def _class(entry: Any) -> dict[str, Any] | None:
    name = _name(_get(entry, "definition", "name"))
    level = _int(_get(entry, "level"), 1, LEVEL_MAX)
    if name is None or level is None:
        return None
    return {
        "name": name,
        "level": level,
        "subclass": _name(_get(entry, "subclassDefinition", "name")),
    }


def _scores(data: Mapping[str, Any], modifiers: list[Mapping[str, Any]]) -> dict[str, int]:
    def by_id(key: str) -> dict[int, int]:
        out = {}
        for stat in _list(data.get(key)):
            stat_id, value = _int(_get(stat, "id"), 1, 6), _int(_get(stat, "value"), 1, SCORE_MAX)
            if stat_id is not None and value is not None:
                out[stat_id] = value
        return out

    base, bonus, override = by_id("stats"), by_id("bonusStats"), by_id("overrideStats")
    scores = {}
    for i, ability in enumerate(ABILITIES, 1):
        if i in override:
            scores[ability] = override[i]
            continue
        if i not in base:
            continue
        name = _ABILITY_NAMES[i - 1]
        extra = sum(
            _int(m.get("value"), -10, 10) or 0
            for m in modifiers
            if m.get("type") == "bonus" and m.get("subType") == f"{name}-score"
        )
        scores[ability] = max(1, min(SCORE_MAX, base[i] + bonus.get(i, 0) + extra))
    return scores


def _mod(score: int | None) -> int:
    return 0 if score is None else (score - 10) // 2


def _max_hp(data: Mapping[str, Any], level: int, con: int | None) -> int | None:
    override = _int(data.get("overrideHitPoints"), 1, 9999)
    if override is not None:
        return override
    base = _int(data.get("baseHitPoints"), 1, 9999)
    if base is None:
        return None
    bonus = _int(data.get("bonusHitPoints"), -999, 999) or 0
    return max(1, base + bonus + _mod(con) * level)


def _senses(modifiers: list[Mapping[str, Any]]) -> dict[str, int]:
    senses: dict[str, int] = {}
    for m in modifiers:
        sense = m.get("subType")
        if m.get("type") == "set-base" and sense in SENSES:
            feet = _int(m.get("value"), 1, 1000)
            if feet is not None:
                senses[str(sense)] = max(feet, senses.get(str(sense), 0))
    return senses


def _spell_names(data: Mapping[str, Any]) -> Iterable[Any]:
    for group in _list(data.get("classSpells")):
        for spell in _list(_get(group, "spells")):
            yield _get(spell, "definition", "name")
    spells = data.get("spells")
    if isinstance(spells, Mapping):
        for source in spells.values():
            for spell in _list(source):
                yield _get(spell, "definition", "name")


def _armour_class(
    data: Mapping[str, Any], scores: dict[str, int], modifiers: list[Mapping[str, Any]]
) -> int | None:
    """Armour class from what's worn, as D&D Beyond's own sheet adds it up for the usual
    cases (armour, a shield, flat bonuses). Unusual ones (unarmoured defence, magic
    items with their own rules) may differ: the snapshot is a helper, not the sheet."""
    override = _int(data.get("overrideArmorClass"), 1, 50)
    if override is not None:
        return override
    if "dex" not in scores:
        return None
    dex = _mod(scores["dex"])
    ac, shield = 10 + dex, 0
    for item in _list(data.get("inventory")):
        if not _get(item, "equipped"):
            continue
        base = _int(_get(item, "definition", "armorClass"), 0, 30)
        kind = _int(_get(item, "definition", "armorTypeId"), 1, 4)
        if base is None or kind is None:
            continue
        if kind == 4:  # a shield
            shield = max(shield, base)
        else:  # light, medium (dexterity up to 2), heavy (none)
            ac = max(ac, base + (dex if kind == 1 else min(dex, 2) if kind == 2 else 0))
    bonus = sum(
        _int(m.get("value"), -10, 10) or 0
        for m in modifiers
        if m.get("type") == "bonus" and m.get("subType") == "armor-class"
    )
    return max(1, min(50, ac + shield + bonus))


# ---- checking a snapshot from anywhere ------------------------------------------------


def clean(snapshot: Any) -> dict[str, Any] | None:
    """The snapshot with exactly the allowed fields, each the right shape, or None if it
    isn't one (no name, or the wrong version). Unknown fields are dropped."""
    if not isinstance(snapshot, Mapping) or snapshot.get("v") != SNAPSHOT_VERSION:
        return None
    name = _name(snapshot.get("name"))
    source = snapshot.get("source")
    if name is None or source not in (SOURCE_DNDBEYOND, SOURCE_TYPED):
        return None
    classes = []
    for entry in _list(snapshot.get("classes"))[:4]:
        cls_name, level = _name(_get(entry, "name")), _int(_get(entry, "level"), 1, LEVEL_MAX)
        if cls_name is not None and level is not None:
            classes.append(
                {"name": cls_name, "level": level, "subclass": _name(_get(entry, "subclass"))}
            )
    abilities_in = snapshot.get("abilities")
    abilities = {
        a: v
        for a in ABILITIES
        if isinstance(abilities_in, Mapping)
        and (v := _int(abilities_in.get(a), 1, SCORE_MAX)) is not None
    }
    senses_in = snapshot.get("senses")
    senses = {
        s: v
        for s in SENSES
        if isinstance(senses_in, Mapping) and (v := _int(senses_in.get(s), 1, 1000)) is not None
    }
    out: dict[str, Any] = {
        "v": SNAPSHOT_VERSION,
        "source": source,
        "name": name,
        "species": _name(snapshot.get("species")),
        "classes": classes,
        "level": _int(snapshot.get("level"), 0, LEVEL_MAX) or 0,
        "abilities": abilities,
        "max_hp": _int(snapshot.get("max_hp"), 1, 9999),
        "ac": _int(snapshot.get("ac"), 1, 50),
        "speed": _int(snapshot.get("speed"), 0, 200),
        "proficiency_bonus": _int(snapshot.get("proficiency_bonus"), 2, 6),
        "saves": [a for a in ABILITIES if a in _list(snapshot.get("saves"))],
        "skills": sorted(
            {s for s in _list(snapshot.get("skills")) if isinstance(s, str) and s in SKILLS}
        ),
        "senses": senses,
        "languages": _names(_list(snapshot.get("languages"))),
    }
    for key in _NAME_LISTS:
        out[key] = _names(_list(snapshot.get(key)))
    out.update(_clean_full(snapshot))
    if source == SOURCE_TYPED and snapshot.get("origin") == ORIGIN_DOCUMENT:
        out["origin"] = ORIGIN_DOCUMENT  # read from a document by the AI (#1125): the full checks
    out["flags"] = sorted(f for f in _list(snapshot.get("flags")) if f in FLAG_PARTS)
    # Never bigger than SNAPSHOT_MAX_BYTES as Postgres stores it (UTF-8, not \u escapes):
    # the longest lists lose their last entries. Each pass shrinks a list or returns, so
    # this always ends. With UTF-8 measured, allow-listed content can't reach the None
    # (it fits once the lists are empty): it stays as the guard that ends the loop.
    trimmable = (*_NAME_LISTS, *_FULL_LISTS, "languages", "classes")
    while len(json.dumps(out, ensure_ascii=False).encode()) > SNAPSHOT_MAX_BYTES:
        longest = max(trimmable, key=lambda k: len(out[k]))
        if not out[longest]:
            return None
        out[longest] = out[longest][: len(out[longest]) * 3 // 4]
    return out


def _clean_full(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The full part of a snapshot (#1124), each the right shape. A snapshot kept before
    these fields existed has none of them: that reads as unknown (None, empty, 0), never as
    a wrong value."""
    out: dict[str, Any] = {
        "hp": _int(snapshot.get("hp"), 0, 99999),
        "temp_hp": _int(snapshot.get("temp_hp"), 0, 9999) or 0,
        "exhaustion": _int(snapshot.get("exhaustion"), 0, EXHAUSTION_MAX) or 0,
        "background": _name(snapshot.get("background")),
    }
    conditions = {str(c).casefold() for c in _list(snapshot.get("conditions"))}
    out["conditions"] = [c for c in CONDITIONS if c.casefold() in conditions]
    for key in ("resistances", "immunities", "vulnerabilities", "tools"):
        out[key] = _names(_list(snapshot.get(key)))
    out["weapon_profs"] = _names(_list(snapshot.get("weapon_profs")))
    out["armor_profs"] = _names(_list(snapshot.get("armor_profs")))
    out["expertise"] = sorted({s for s in _list(snapshot.get("expertise")) if s in SKILLS})
    out["slots"] = clean_slots(snapshot.get("slots"))
    out["pact"] = clean_pact(snapshot.get("pact"))
    inventory = snapshot.get("inventory")
    if isinstance(inventory, Mapping):
        rarity_in = inventory.get("rarity")
        weight = _weight(inventory.get("weight"))
        out["inventory"] = {
            "count": _int(inventory.get("count"), 0, INVENTORY_MAX) or 0,
            "weight": round(weight, 2) if weight is not None else 0.0,
            "rarity": {
                r: (_int(rarity_in.get(r), 0, INVENTORY_MAX) or 0)
                if isinstance(rarity_in, Mapping)
                else 0
                for r in RARITIES
            },
        }
    else:
        out["inventory"] = None
    return out


def typed(
    name: str, *, species: str, class_name: str, level: int, names: Iterable[str]
) -> dict[str, Any] | None:
    """A snapshot from the fallback form ("Tell DMbot about your character"): class,
    level, species, and up to TYPED_NAMES_MAX spell or feature names."""
    picked = _names(names)[:TYPED_NAMES_MAX]
    return clean(
        {
            "v": SNAPSHOT_VERSION,
            "source": SOURCE_TYPED,
            "name": name,
            "species": species,
            "classes": [{"name": class_name, "level": level, "subclass": None}],
            "level": level,
            "features": picked,
        }
    )


def hint_names(snapshot: Mapping[str, Any]) -> list[str]:
    """Names on the sheet that speech-to-text should expect: spells first, then
    features, feats and items."""
    return _names(name for key in _NAME_LISTS for name in _list(snapshot.get(key)))


def who(snapshot: Mapping[str, Any]) -> str:
    """ "Species, class and level" for the DM's card: "Hill Dwarf · Cleric 3 / Fighter 1"."""
    classes = " / ".join(
        f"{c['name']} {c['level']}"
        for c in _list(snapshot.get("classes"))
        if isinstance(c, Mapping)
    )
    return " · ".join(p for p in (snapshot.get("species"), classes) if p)


# ---- fetching --------------------------------------------------------------------------


_fetching: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


class _Gate:
    """One limit for the whole bot on how fast D&D Beyond is asked for sheets, so that being
    rate-limited by dndbeyond.com (and so breaking sheet import for every customer) is not
    something a busy evening can cause. A token bucket of READS_PER_MINUTE, and a pause for
    everyone after a 429 or a 5xx, as long as D&D Beyond asks (`Retry-After`). A read held by
    the limit just waits: it is nobody's failure."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tokens = float(READS_PER_MINUTE)
        self._stamp = clock()
        self._paused_until = 0.0

    def _refill(self, now: float) -> None:
        self._tokens = min(
            float(READS_PER_MINUTE),
            self._tokens + (now - self._stamp) * READS_PER_MINUTE / 60.0,
        )
        self._stamp = now

    def wait(self) -> float:
        """Seconds until a read may start (0: now). Takes nothing."""
        now = self._clock()
        self._refill(now)
        paused = max(0.0, self._paused_until - now)
        short = 0.0 if self._tokens >= 1.0 else (1.0 - self._tokens) * 60.0 / READS_PER_MINUTE
        return max(paused, short)

    def take(self) -> float:
        """Take a token if one is free and nothing is paused: 0. Else the seconds to wait."""
        wait = self.wait()
        if wait <= 0.0:
            self._tokens -= 1.0
        return wait

    def pause(self, seconds: float) -> None:
        """D&D Beyond said slow down: every read waits (for at most PAUSE_MAX_S)."""
        seconds = min(PAUSE_MAX_S, max(1.0, seconds))
        self._paused_until = max(self._paused_until, self._clock() + seconds)


GATE = _Gate()


def wait_time() -> float:
    """Seconds until the bot may ask D&D Beyond for a sheet (0: now). Background re-reads look
    first and simply wait, rather than start a read that would be held."""
    return GATE.wait()


def _retry_after(value: str | None) -> float:
    """Seconds from a `Retry-After` header (a number; a date is not read, the default is used)."""
    try:
        return float(value) if value is not None else PAUSE_DEFAULT_S
    except ValueError:
        return PAUSE_DEFAULT_S


def _permit() -> asyncio.Semaphore:
    """The process's FETCHES_AT_ONCE permits, made on first use for the running loop (a
    semaphore belongs to one loop; tests run many)."""
    global _fetching
    loop = asyncio.get_running_loop()
    if _fetching is None or _fetching[0] is not loop:
        _fetching = (loop, asyncio.Semaphore(FETCHES_AT_ONCE))
    return _fetching[1]


def new_session() -> aiohttp.ClientSession:
    """One session for a refresh's sheets (one connection, reused). No cookies kept: DMbot
    never holds any D&D Beyond session (CLAUDE.md, D&D Beyond)."""
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        cookie_jar=aiohttp.DummyCookieJar(),
    )


async def fetch(character: int, *, session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
    """The snapshot of a public character: one GET, 10 s at most including any wait for
    a permit (FETCHES_AT_ONCE across the process). Raises SheetError (`refused` if D&D
    Beyond refused: not public, or no such character)."""
    url = API.format(id=int(character))
    own = session is None
    client = session or new_session()
    try:
        while (held := GATE.take()) > 0.0:
            if held > HOLD_MAX_S:
                raise SheetError(BUSY, limited=True, retry_after=held)
            await asyncio.sleep(held)
        async with (
            asyncio.timeout(FETCH_TIMEOUT_S),
            _permit(),
            client.get(url, allow_redirects=False) as resp,
        ):
            if resp.status in (401, 403, 404):
                raise SheetError(NOT_PUBLIC, refused=True)
            if resp.status == 429 or resp.status >= 500:
                after = _retry_after(resp.headers.get("Retry-After"))
                GATE.pause(after)  # every sheet waits, not just this one
                raise SheetError(
                    f"D&D Beyond answered {resp.status}",
                    limited=True,
                    status=resp.status,
                    retry_after=after,
                )
            if resp.status != 200:
                raise SheetError(f"D&D Beyond answered {resp.status}", status=resp.status)
            body = bytearray()
            async for piece in resp.content.iter_chunked(64 * 1024):
                body += piece
                if len(body) > MAX_BYTES:
                    raise SheetError("The sheet was too big to read.")
        # json.loads holds the GIL even in a thread; the thread helps `parse` only. The
        # worst stall measured is ~13 ms, once per character per session.
        answer = await asyncio.to_thread(_json, bytes(body))
        if answer.get("success") is False:
            raise SheetError(NOT_PUBLIC, refused=True)
        return await asyncio.to_thread(parse, answer)
    except SheetError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, OverflowError) as exc:
        raise SheetError(f"Couldn't read the sheet: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.close()


def _json(body: bytes) -> Mapping[str, Any]:
    answer = json.loads(body)
    if not isinstance(answer, Mapping):
        raise ValueError("not a JSON object")
    return answer
