"""The rules index: look a spell or a condition up by name (docs/PLAN.md, "Rules
edition" and "Lookups must match renamed content"; #866).

What is in it: the System Reference Document 5.2.1 (CC-BY-4.0, attribution in
`data/srd52/ATTRIBUTION.md`), and nothing from any other book. Every entry carries what a
rules alert must show: its source, the section and the page, and, for older content, its
edition tag.

- **Names:** an entry is found by its name with case, punctuation and apostrophes ignored
  ("Melf's acid arrow", "melfs acid arrow"), and by the older names the newest edition
  renamed it from (`dmbot.rules.aliases`).
- **Order:** the campaign's target ruleset first, then its fallback; the first that has
  the name wins, so an older entry is used only when no newer one matches any name. A hit
  from the fallback is tagged (`[Legacy 2014]`). House rules and homebrew sit above all
  of this and are not in the index.
- More data (the 2014 SRD 5.1, as legacy) is added by loading another folder of the same
  shape, with its own `edition`; nothing here is specific to 2024.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmbot.campaigns.models import FALLBACK_NONE
from dmbot.rules import aliases

DATA = Path(__file__).resolve().parent / "data"
LEGACY = "2014"  # the edition whose content is always tagged legacy

_APOSTROPHES = re.compile(r"[’‘ʼ'`]")
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(name: str) -> str:
    """A name's key: case, punctuation and apostrophes (curly or straight) ignored, spacing
    squeezed. "Melf's Acid Arrow", "melfs acid arrow" and "Melf's  Acid-Arrow" are one key."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _APOSTROPHES.sub("", text)
    return _NOT_ALNUM.sub(" ", text).strip()


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

    kind: str  # "spell" or "condition"
    name: str
    edition: str  # "2024", "2014"
    source: str  # "SRD 5.2.1"
    section: str  # "Spell Descriptions"
    page: int
    text: str  # the source's words
    details: Mapping[str, Any] = field(default_factory=dict)  # a spell's level, school, ...

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

    @property
    def citation(self) -> str:
        """The entry's citation with its tag: `SRD 5.1, Spells, p. 7 [Legacy 2014]`."""
        return f"{self.entry.citation} {self.tag}" if self.tag else self.entry.citation

    @property
    def renamed(self) -> bool:
        """Found under an older name than the entry's own."""
        return self.found_as != normalize(self.entry.name)


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
        for entry in self.entries:
            self._add(entry.edition, normalize(entry.name), entry)
        for older, current in alias_pairs:
            found = self._by_edition.get(alias_edition, {}).get(normalize(current))
            if not found:
                raise ValueError(
                    f"The alias {older!r} points to {current!r}, which isn't in the data"
                )
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
        (tagged), else None. `kind` limits it to spells or conditions; without it, a name
        that is both (the SRD has none) gives the one loaded first."""
        key = normalize(name)
        if not key:
            return None
        for edition, from_fallback in ((target, False), (fallback, True)):
            if edition == FALLBACK_NONE or (from_fallback and edition == target):
                continue
            for entry in self._by_edition.get(edition, {}).get(key, []):
                if kind is None or entry.kind == kind:
                    return Hit(entry, edition_tag(edition, from_fallback=from_fallback), key)
        return None

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
            details = {k: v for k, v in raw.items() if k not in ("name", "text", "page")}
            if "classes" in details:
                details["classes"] = tuple(details["classes"])
            entries.append(
                Entry(
                    data["kind"],
                    raw["name"],
                    data["edition"],
                    data["source"],
                    data["section"],
                    int(raw["page"]),
                    raw["text"],
                    details,
                )
            )
    return entries


@functools.cache
def srd() -> Index:
    """The index of what DMbot ships: the SRD 5.2.1, with the renamed spells' older names."""
    return Index(load_folder(DATA / "srd52"), aliases.SPELL_ALIASES)
