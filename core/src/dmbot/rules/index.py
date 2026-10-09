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
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dmbot.campaigns.models import FALLBACK_NONE
from dmbot.rules import aliases

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

    kind: str  # "spell", "condition" or "monster"
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


@dataclass(frozen=True, slots=True)
class Hit:
    """An entry found, and how: the tag to show with it, and the name it was found by."""

    entry: Entry
    tag: str  # "" for the target ruleset's own; "[Legacy 2014]" for an older one
    found_as: str  # the key that matched, which may be an older name
    via_alias: bool = False  # found by a name the newest edition renamed it from

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
        for entry in self.entries:
            self._add(entry.edition, normalize(entry.name), entry)
            if entry.kind == "monster":
                for key in monster_keys(entry.name):
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
        (tagged), else None. `kind` limits it to spells, conditions or monsters; without it, a
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
                    if kind is None or entry.kind == kind:
                        tag = edition_tag(edition, from_fallback=from_fallback)
                        return Hit(entry, tag, found_as, (edition, found_as) in self._alias_keys)
        return None

    def _pool(self, target: str, fallback: str) -> dict[str, Entry]:
        """Every name an entry is found by (in the target ruleset, then the fallback) and
        the entry it leads to; a name in both editions leads to the target's."""
        pool: dict[str, Entry] = {}
        for edition in (target, fallback):
            if edition == FALLBACK_NONE:
                continue
            for key, entries in self._by_edition.get(edition, {}).items():
                pool.setdefault(key, entries[0])
        return pool

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
        for k in [*begins, *near]:
            entry = pool[k]
            if entry not in found:
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
        keys = sorted(pool)
        if key:
            begins = [k for k in keys if k.startswith(key)]
            words = [k for k in keys if k not in begins and f" {key}" in f" {k}"]
            keys = begins + words
        found: list[Entry] = []
        for k in keys:
            if pool[k] not in found:
                found.append(pool[k])
        found.sort(key=lambda e: e.edition != target)  # stable: the target's names first
        return found[:limit]

    def names(self, kind: str, edition: str) -> list[str]:
        """The names of that edition's entries of that kind, in order."""
        return [e.name for e in self.entries if e.kind == kind and e.edition == edition]


def load_folder(folder: Path) -> list[Entry]:
    """Every entry in a data folder (`spells.json`, `conditions.json`, ...). The files
    are DMbot's own, made by `python -m dmbot.devtools.srd`; a wrong shape is a bug."""
    entries: list[Entry] = []
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in data["entries"]:
            details = {k: v for k, v in raw.items() if k not in ("name", "text", "page", "section")}
            if "classes" in details:
                details["classes"] = tuple(details["classes"])
            entries.append(
                Entry(
                    data["kind"],
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
    return Index(entries, aliases.ALL)
