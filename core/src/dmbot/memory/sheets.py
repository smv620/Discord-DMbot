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
import re
from collections.abc import Iterable, Mapping
from typing import Any

import aiohttp

from dmbot.memory.models import name_key

SOURCE_DNDBEYOND = "dndbeyond"
SOURCE_TYPED = "typed"
SNAPSHOT_VERSION = 1

API = "https://character-service.dndbeyond.com/character/v5/character/{id}"
USER_AGENT = "DMbot (Discord bot for tabletop games; https://github.com/smv620/Discord-DMbot)"
FETCH_TIMEOUT_S = 10
MAX_BYTES = 1024 * 1024  # a big sheet is a few hundred KB
SHEET_URL = "https://www.dndbeyond.com/characters/{id}"
_LINK = re.compile(
    r"^\s*<?https://(?:www\.)?dndbeyond\.com/characters/(\d{1,12})(?:[/?#][^\s>]*)?>?\s*$",
    re.IGNORECASE,
)

NAME_MAX = 60  # a spell, feature or item name; longer is likely text, and dropped
LIST_MAX = 100  # of each kind of name
TYPED_NAMES_MAX = 20  # spell or feature names on the fallback form
LEVEL_MAX = 20
SCORE_MAX = 30
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_NAMES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
_SKILLS = (
    "acrobatics", "animal-handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight-of-hand", "stealth", "survival",
)  # fmt: skip
_SENSES = ("darkvision", "blindsight", "tremorsense", "truesight")
_NAME_LISTS = ("spells", "features", "feats", "items")

NOT_A_LINK = (
    "That doesn't look like a D&D Beyond character link. Open your character on D&D "
    "Beyond and copy the address from the top of the page."
)
NOT_PUBLIC = (
    "DMbot can't read that sheet yet. On D&D Beyond, open the character, press Edit, then "
    "Settings, and set Character Privacy to Public. Then press Link my character again."
)


class SheetError(Exception):
    """A sheet couldn't be read. `public` is False when D&D Beyond refused (the sheet
    isn't public, or there's no such character): the player can fix that."""

    def __init__(self, message: str, *, public: bool = True) -> None:
        super().__init__(message)
        self.public = public


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
                if m.get("type") in ("proficiency", "expertise") and m.get("subType") in _SKILLS
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
    cleaned = clean(snapshot)
    if cleaned is None:
        raise SheetError("The character service's answer had no character in it.")
    return cleaned


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
    number = int(value)
    return number if low <= number <= high else None


def _name(value: Any) -> str | None:
    """A short single-line name, or None (anything long is likely text, not a name)."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
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
        if m.get("type") == "set-base" and sense in _SENSES:
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
        for s in _SENSES
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
        "skills": sorted(s for s in set(_list(snapshot.get("skills"))) if s in _SKILLS),
        "senses": senses,
        "languages": _names(_list(snapshot.get("languages"))),
    }
    for key in _NAME_LISTS:
        out[key] = _names(_list(snapshot.get(key)))
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


async def fetch(character: int, *, session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
    """The snapshot of a public character: one GET, 10 s at most. Raises SheetError
    (`public=False` if D&D Beyond refused: not public, or no such character)."""
    url = API.format(id=int(character))
    own = session is None
    client = session or aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_S):
            async with client.get(url, allow_redirects=False) as resp:
                if resp.status in (401, 403, 404):
                    raise SheetError(NOT_PUBLIC, public=False)
                if resp.status != 200:
                    raise SheetError(f"D&D Beyond answered {resp.status}")
                body = await resp.content.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise SheetError("The sheet was too big to read.")
                answer = await asyncio.to_thread(_json, body)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise SheetError(f"Couldn't reach D&D Beyond: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.close()
    if answer.get("success") is False:
        raise SheetError(NOT_PUBLIC, public=False)
    return await asyncio.to_thread(parse, answer)


def _json(body: bytes) -> Mapping[str, Any]:
    import json

    answer = json.loads(body)
    if not isinstance(answer, Mapping):
        raise ValueError("not a JSON object")
    return answer
