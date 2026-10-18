"""The rules index: look a spell or a condition up by name (docs/PLAN.md, "Rules
edition" and "Lookups must match renamed content"; #866).

What is in it: the System Reference Documents 5.2.1 and 5.1 (CC-BY-4.0, attribution in
`data/srd52/ATTRIBUTION.md` and `data/srd51/ATTRIBUTION.md`), and nothing from any other
book. Every entry carries what a rules alert must show: its source, the section and the
page, and, for older content, its edition tag.

- **Names:** an entry is found by its name with case, punctuation and apostrophes ignored
  ("Melf's acid arrow", "melfs acid arrow"), and by the older names the newest edition
  renamed it from (`dmbot.rules.aliases`).
- **Order:** the campaign's target ruleset first, then its fallback; the first that has
  the name wins, so an older entry is used only when no newer one matches any name. A hit
  from the fallback is tagged (`[Legacy 2014]`). House rules and homebrew sit above all
  of this and are not in the index.
- More data is added by loading another folder of the same shape, with its own `edition`
  (the 2014 SRD 5.1 is the legacy fallback); nothing here is specific to 2024.
"""

from __future__ import annotations

import difflib
import functools
import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dmbot.campaigns.models import FALLBACK_NONE
from dmbot.rules import aliases

log = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parent / "data"
SUGGEST_CUTOFF = 0.7  # how alike two names must be to be offered as "did you mean"
LEGACY = "2014"  # the edition whose content is always tagged legacy; update when a newer
# edition ships (then 2024 becomes legacy too, and gets its own tag)

_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")  # a name's last bracket: "(Legacy)", "(Svirfneblin)"
_APOSTROPHES = re.compile(r"[’‘ʼ'`]")
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(name: str) -> str:
    """A name's key: case, punctuation and apostrophes (curly or straight) ignored, spacing
    squeezed. "Melf's Acid Arrow", "melfs acid arrow" and "Melf's  Acid-Arrow" are one key."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _APOSTROPHES.sub("", text)
    return _NOT_ALNUM.sub(" ", text).strip()


_COUNTS = frozenset(
    ["a", "an", "the", "some", "many", "several", "few"]
    + ["two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    + [str(n) for n in range(1, 100)]
)
RULE = "rule"  # the kind of a Rules Glossary entry
FEATURE = (
    "feature"  # a class or subclass feature ("Rage", "Evasion"); the same name can be in several
)
ITEM = "item"  # a magic item ("Bag of Holding", "Cloak of Protection")
FEAT = "feat"  # a feat ("Alert", "Lucky")
SPECIES = "species"  # a character species ("Goliath")
BACKGROUND = "background"  # a character background ("Sage")
WEAPON = "weapon"  # a weapon of the Weapons table ("Longsword")
ARMOR = "armor"  # an armor of the Armor table, and the Shield ("Plate Armor")
GEAR = "gear"  # adventuring gear, a mount or a vehicle ("Healer's Kit", "Horse, Riding", "Rowboat")
TOOL = "tool"  # a tool ("Thieves' Tools")
# All ten are found only when asked for: their names are everyday words ("Action", "Damage",
# "Rope of Climbing", "Alert", "Sage", "Club"), so the name pool the live cards, the list and "did
# you mean" read from leaves them out.
ASKED_FOR = frozenset({RULE, FEATURE, ITEM, FEAT, SPECIES, BACKGROUND, WEAPON, ARMOR, GEAR, TOOL})
ORIGIN_KINDS = (FEAT, SPECIES, BACKGROUND)  # looked up after the features and the items
EQUIPMENT_KINDS = (WEAPON, ARMOR, GEAR, TOOL)  # and then these (#1919, #1927)
# The kinds a list entry says in brackets when its name is also another's: "Shield (armor)"
NAMED_KINDS = (RULE, ITEM, *ORIGIN_KINDS, *EQUIPMENT_KINDS)
# The SRD 5.2.1's twelve classes: a class in a question picks that class's feature (#1900).
CLASS_NAMES = (
    *("Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk"),
    *("Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"),
)
CLASS_WORDS = frozenset(name.lower() for name in CLASS_NAMES)
PEOPLES = ("elf", "gnome", "dwarf", "halfling", "human", "orc")  # "Elf, Drow": say "Drow"


def monster_keys(name: str) -> list[str]:
    """The other names a creature is found by: without its bracket ("Gnome, Deep"), the
    bracket's own word ("Svirfneblin"), the comma turned round ("Deep Gnome") and each side
    of a slash ("Succubus", "Incubus")."""
    plain = _SUFFIX.sub("", name)
    found = [plain]
    inner = re.search(r"\(([^)]*)\)\s*$", name)
    if inner:
        found.append(inner.group(1))
    head, comma, tail = plain.partition(", ")
    if comma:
        found.append(f"{tail} {head}")
    if comma and not inner and head.lower() in PEOPLES:
        found.append(tail)  # "Elf, Drow" is also just "Drow"
    if "/" in plain:  # "Succubus/Incubus" is also each of the two
        found.extend(plain.split("/"))
    own = normalize(name)
    return [k for k in dict.fromkeys(normalize(n) for n in found) if k and k != own]


_POSSESSIVE_S = re.compile(r"['’]s\b")


def gear_keys(name: str) -> list[str]:
    """The other names a piece of gear, a mount or a tool is found by: the comma turned round
    ("Horse, Riding" is "Riding Horse"), and without the possessive's "s" ("Healer's Kit" is
    "Healer Kit"), because a question is read with it taken off (`words_of`)."""
    found = [name]
    head, comma, tail = name.partition(", ")
    if comma:
        found.append(f"{tail} {head}")
    found += [_POSSESSIVE_S.sub("", n) for n in found]
    own = normalize(name)
    return [k for k in dict.fromkeys(normalize(n) for n in found) if k and k != own]


def item_keys(name: str) -> list[str]:
    """The other names a magic item is found by: without its bracket ("Stone of Good Luck"), the
    bracket's own word ("Luckstone"), for a name that gives its bonuses ("Wand of the War Mage,
    +1, +2, or +3") the name before them, and for "Potions of ..." the singular. The bare
    "Armor", "Weapon" and "Shield" of the "+1, +2, or +3" items are not offered: they are
    everyday words."""
    plain = _SUFFIX.sub("", name)
    found = [plain]
    inner = re.search(r"\(([^)]*)\)\s*$", name)
    if inner:
        found.append(inner.group(1))
    if plain.startswith("Potions of "):  # "Potions of Healing" is asked for as "potion of healing"
        found.append("Potion of " + plain.removeprefix("Potions of "))
    head, plus, _ = plain.partition(", +")
    if plus and len(head.split()) > 1:
        found.append(head)
    own = normalize(name)
    return [k for k in dict.fromkeys(normalize(n) for n in found) if k and k != own]


def edition_tag(edition: str, *, from_fallback: bool) -> str:
    """The tag shown with content: `[Legacy 2014]` for the 2014 rules wherever they
    appear; another edition's own tag when it came from the fallback ruleset; none for
    the target ruleset's own content."""
    if edition == LEGACY:
        return f"[Legacy {edition}]"
    return f"[{edition}]" if from_fallback else ""


@dataclass(frozen=True, slots=True)
class Entry:
    """One rule in the index."""

    kind: str  # "spell", "condition", "monster" or "rule" (a Rules Glossary entry, #1884)
    name: str
    edition: str  # "2024", "2014"
    source: str  # "SRD 5.2.1"
    section: str  # "Spell Descriptions"
    page: int
    text: str  # the source's words
    # A spell's level, school, ... Read only: the index is shared, so nobody may change it in
    # place. Not part of an entry's hash (a mapping isn't hashable).
    details: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # Every entry, however it was made, gets its own read-only copy.
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @property
    def citation(self) -> str:
        """What an alert cites: `SRD 5.2.1, Spell Descriptions, p. 131`."""
        return f"{self.source}, {self.section}, p. {self.page}"

    @property
    def facts(self) -> str:
        """The metadata the SRD keeps apart from the text (range, casting time, AC, ...), as one
        plain line. See dmbot.rules.facts: the one place that knows the fields (#1063)."""
        from dmbot.rules import facts

        return facts.line(self)

    @property
    def full_text(self) -> str:
        """The facts, then the text: what to read, search or check an answer against."""
        from dmbot.rules import facts

        return facts.full_text(self)


@dataclass(frozen=True, slots=True)
class Hit:
    """An entry found, and how: the tag to show with it, and the name it was found by."""

    entry: Entry
    tag: str  # "" for the target ruleset's own; "[Legacy 2014]" for an older one
    found_as: str  # the key that matched, which may be an older name
    via_alias: bool = False  # found by a name the newest edition renamed it from
    from_fallback: bool = False  # the target ruleset had nothing: found in the fallback

    @property
    def citation(self) -> str:
        """The entry's citation with its tag: `SRD 5.1, Spells, p. 7 [Legacy 2014]`."""
        return f"{self.entry.citation} {self.tag}" if self.tag else self.entry.citation

    @property
    def renamed(self) -> bool:
        """Found by a name the newest edition renamed it from ("Goblin" for the Goblin Warrior),
        not by another way of saying its own ("Deep Gnome", "Svirfneblin")."""
        return self.via_alias


class Index:
    """Entries by edition and name. Immutable once built."""

    def __init__(
        self,
        entries: Iterable[Entry],
        alias_pairs: Sequence[tuple[str, str]] = (),
        alias_edition: str = aliases.EDITION,
    ) -> None:
        self.entries: tuple[Entry, ...] = tuple(entries)
        self._by_edition: dict[str, dict[str, list[Entry]]] = {}
        self._alias_keys: set[tuple[str, str]] = set()  # (edition, key) added by an alias
        self._pools: dict[tuple[str, str], tuple[dict[str, Entry], list[str]]] = {}
        for entry in self.entries:
            self._add(entry.edition, normalize(entry.name), entry)
            if entry.kind == "monster":
                for key in monster_keys(entry.name):
                    self._add(entry.edition, key, entry)
            elif entry.kind == ITEM:
                for key in item_keys(entry.name):
                    self._add(entry.edition, key, entry)
            elif entry.kind in (GEAR, TOOL):
                for key in gear_keys(entry.name):
                    self._add(entry.edition, key, entry)
        for older, current in alias_pairs:
            found = self._by_edition.get(alias_edition, {}).get(normalize(current))
            if not found:
                raise ValueError(
                    f"The alias {older!r} points to {current!r}, which isn't in the data"
                )
            self._alias_keys.add((alias_edition, normalize(older)))
            for entry in list(found):
                self._add(alias_edition, normalize(older), entry)

    def _add(self, edition: str, key: str, entry: Entry) -> None:
        bucket = self._by_edition.setdefault(edition, {}).setdefault(key, [])
        if entry not in bucket:
            bucket.append(entry)

    def lookup(
        self, name: str, target: str, fallback: str = FALLBACK_NONE, *, kind: str | None = None
    ) -> Hit | None:
        """The entry for `name` in the target ruleset, else in the fallback ruleset
        (tagged), else None. `kind` limits it to spells, conditions, monsters or rules (glossary
        entries, found only when asked for); without it, a
        name that is more than one (the SRD has none: a test pins that) gives the one loaded
        first."""
        key = normalize(name)
        if not key:
            return None
        keys = [key]
        plain = normalize(_SUFFIX.sub("", name))  # "Goblin (Legacy)" is found as "Goblin"
        if plain and plain != key:
            keys.append(plain)
        for edition, from_fallback in ((target, False), (fallback, True)):
            if edition == FALLBACK_NONE or (from_fallback and edition == target):
                continue
            for found_as in keys:
                for entry in self._by_edition.get(edition, {}).get(found_as, []):
                    # Glossary entries and features are found only by asking (see `_pool`).
                    if entry.kind == kind or (kind is None and entry.kind not in ASKED_FOR):
                        tag = edition_tag(edition, from_fallback=from_fallback)
                        via_alias = (edition, found_as) in self._alias_keys
                        return Hit(entry, tag, found_as, via_alias, from_fallback)
        return None

    def _pool(self, target: str, fallback: str) -> dict[str, Entry]:
        """Every name an entry is found by (in the target ruleset, then the fallback) and
        the entry it leads to; a name in both editions leads to the target's."""
        cached = self._pools.get((target, fallback))
        if cached is None:  # the index never changes, so the answer can be kept
            pool: dict[str, Entry] = {}
            for edition in (target, fallback):
                if edition == FALLBACK_NONE:
                    continue
                for key, entries in self._by_edition.get(edition, {}).items():
                    # A glossary entry's name is an everyday word ("Action", "Damage"): it is
                    # found only when asked for (kind="rule"), never offered as a name to spot
                    # in the table's talk, to complete or to suggest.
                    named = next((e for e in entries if e.kind not in ASKED_FOR), None)
                    if named is not None:
                        pool.setdefault(key, named)
            cached = (pool, sorted(pool))
            self._pools[(target, fallback)] = cached
        return cached[0]

    def names_pool(self, target: str, fallback: str = FALLBACK_NONE) -> Mapping[str, Entry]:
        """Every name an entry is found by in the target ruleset, then the fallback, and the
        entry it leads to (a name in both leads to the target's). Read only: shared."""
        return MappingProxyType(self._pool(target, fallback))

    def _sorted_keys(self, target: str, fallback: str) -> list[str]:
        self._pool(target, fallback)
        return self._pools[(target, fallback)][1]

    def suggest(
        self, typed: str, target: str, fallback: str = FALLBACK_NONE, *, limit: int = 5
    ) -> list[Entry]:
        """Entries whose names are close to what was typed, never more than `limit`: the ones
        it begins ("fireb" for Fireball), then the ones spelled nearly the same. Only to
        suggest ("Did you mean..."): nothing here is a match, and nothing is picked."""
        key = normalize(typed)
        if not key:
            return []
        pool = self._pool(target, fallback)
        begins = sorted(k for k in pool if k.startswith(key))
        near = difflib.get_close_matches(key, list(pool), n=limit * 3, cutoff=SUGGEST_CUTOFF)
        found: list[Entry] = []
        seen: set[int] = set()  # the pool's entries are shared: who they are is enough
        for k in [*begins, *near]:
            entry = pool[k]
            if id(entry) not in seen:
                seen.add(id(entry))
                found.append(entry)
            if len(found) == limit:
                break
        return found

    def typeahead(
        self, typed: str, target: str, fallback: str = FALLBACK_NONE, *, limit: int = 25
    ) -> list[Entry]:
        """Entries for a name being typed: names that begin with it first, then names with
        a word that begins with it, the target ruleset's before the fallback's, each group
        in alphabetical order. With nothing typed, the target ruleset's first names."""
        key = normalize(typed)
        pool = self._pool(target, fallback)
        keys = self._sorted_keys(target, fallback)
        if key:
            begins = [k for k in keys if k.startswith(key)]
            starts = set(begins)
            keys = begins + [k for k in keys if k not in starts and f" {key}" in f" {k}"]
        found: list[Entry] = []
        seen: set[int] = set()
        for k in keys:
            entry = pool[k]
            if id(entry) not in seen:
                seen.add(id(entry))
                found.append(entry)
        found.sort(key=lambda e: e.edition != target)  # stable: the target's names first
        return found[:limit]

    def lookup_all(
        self,
        name: str,
        target: str,
        fallback: str = FALLBACK_NONE,
        *,
        kind: str,
        prefer: str = "",
    ) -> list[Hit]:
        """Every entry of `kind` with this name: the target ruleset's, and the fallback's (tagged)
        for a class the target has none for, because a feature name like "Evasion" belongs to
        several classes and the fallback is used only where the target has nothing. (A class is
        the only thing compared: a subclass's feature counts as its class's.) With `prefer`
        (a class: "rogue") only that class's entries, if it has one."""
        key = normalize(name)
        if not key:
            return []
        hits: list[Hit] = []
        taken: set[str] = set()  # the classes the newer ruleset already gave this name
        for edition, from_fallback in ((target, False), (fallback, True)):
            if edition == FALLBACK_NONE or (from_fallback and edition == target):
                continue
            found = [e for e in self._by_edition.get(edition, {}).get(key, []) if e.kind == kind]
            tag = edition_tag(edition, from_fallback=from_fallback)
            hits += [
                Hit(e, tag, key, False, from_fallback) for e in found if _class_of(e) not in taken
            ]
            taken |= {_class_of(e) for e in found}
        if prefer:
            wanted = [h for h in hits if _class_of(h.entry) == normalize(prefer)]
            hits = wanted or hits
        return hits

    def asked_names(
        self,
        typed: str,
        target: str,
        fallback: str = FALLBACK_NONE,
        *,
        kinds: Sequence[str],
        limit: int = 25,
    ) -> list[Entry]:
        """Entries of the kinds found only when asked for (glossary entries, features) for a
        name being typed, the target ruleset's first: names that begin with it, then names with
        a word that begins with it. They are kept out of `typeahead` and `suggest` (see
        `_pool`), so the list asks for them itself."""
        key = normalize(typed)
        if not key:
            return []
        found: list[tuple[int, str, Entry]] = []
        seen: set[int] = set()
        newer: set[tuple[str, str, str]] = set()  # (kind, name, class) the target already has
        for rank, edition in enumerate((target, fallback)):
            if edition == FALLBACK_NONE or (rank and edition == target):
                continue
            for name, entries in self._by_edition.get(edition, {}).items():
                for entry in entries:
                    if entry.kind not in kinds or id(entry) in seen:
                        continue
                    # The fallback's version of what the target also has is not offered.
                    if rank and (entry.kind, name, _class_of(entry)) in newer:
                        continue
                    if name.startswith(key):
                        order = 0
                    elif f" {key}" in f" {name}":
                        order = 1
                    else:
                        continue
                    seen.add(id(entry))
                    found.append((rank * 2 + order, name, entry))
                    if not rank:
                        newer.add((entry.kind, name, _class_of(entry)))
        found.sort(key=lambda item: item[:2])
        return [entry for _, _, entry in found[:limit]]

    def is_monster(self, name: str) -> bool:
        """Is `name` a kind of monster the rules have, in any edition: by its name, its
        other names, its plural ("goblins") or with a count or article in front ("three
        goblins", "the goblin")? A proper name ("Snot") is not."""
        words = normalize(name).split()
        while words and words[0] in _COUNTS:
            words.pop(0)
        key = " ".join(words)
        if not key:
            return False
        tries = [key]
        if key.endswith("ies"):
            tries.append(key[:-3] + "y")
        if key.endswith("ves"):
            tries.append(key[:-3] + "f")  # wolves
        if key.endswith("es"):
            tries.append(key[:-2])
        if key.endswith("s"):
            tries.append(key[:-1])
        return any(
            entry.kind == "monster"
            for found in self._by_edition.values()
            for t in tries
            for entry in found.get(t, ())
        )

    def names(self, kind: str, edition: str) -> list[str]:
        """The names of that edition's entries of that kind, in order."""
        return [e.name for e in self.entries if e.kind == kind and e.edition == edition]


def _class_of(entry: Entry) -> str:
    """The class a feature belongs to, as a key ("" for an entry that has none)."""
    return normalize(str(entry.details.get("class", "")))


def load_folder(folder: Path) -> list[Entry]:
    """Every entry in a data folder (`spells.json`, `conditions.json`, ...). The files
    are DMbot's own, made by `python -m dmbot.devtools.srd`; a wrong shape is a bug."""
    entries: list[Entry] = []
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in data["entries"]:
            skipped = (
                "name",
                "text",
                "page",
                "section",
                "kind",
            )  # (a file may say a kind per entry)
            details = {k: v for k, v in raw.items() if k not in skipped}
            for listed in ("classes", "see_also"):  # shared, so read only: a tuple
                if listed in details:
                    details[listed] = tuple(details[listed])
            entries.append(
                Entry(
                    raw.get("kind", data["kind"]),
                    raw["name"],
                    data["edition"],
                    data["source"],
                    raw.get("section", data.get("section", "")),  # a file of one section or not
                    int(raw["page"]),
                    raw["text"],
                    details,
                )
            )
    return entries


@functools.cache
def srd() -> Index:
    """The index of what DMbot ships: the SRD 5.2.1 and, as legacy, the SRD 5.1, with the
    renamed spells' older names."""
    entries = load_folder(DATA / "srd52") + load_folder(DATA / "srd51")
    for kind, what in (
        (RULE, "Rules Glossary"),
        (FEATURE, "class features"),
        (ITEM, "magic items"),
        (FEAT, "feats"),
        (SPECIES, "species"),
        (BACKGROUND, "backgrounds"),
        (WEAPON, "weapons"),
        (ARMOR, "armor"),
        (GEAR, "adventuring gear, mounts and vehicles"),
        (TOOL, "tools"),
    ):
        if not any(e.kind == kind for e in entries):  # an older build of the data: carry on
            log.warning("The %s are not in the data; the DM sidebar will not cite them", what)
    return Index(entries, aliases.ALL)
