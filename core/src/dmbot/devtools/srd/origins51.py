"""Turning the 2014 SRD 5.1's races, its one background and its one feat into entries (#1928). Pure:
no PDF, no files.

A **race** is a title (`Dwarf`) and, right under it, a second title (`Dwarf Traits`); then its
traits as paragraphs that start with an indented bold-italic lead-in (`Darkvision. ...`). A
**subrace** is a title with no `Traits` title under it (`Hill Dwarf`) after its race; it stays
inside the race's entry as a heading line and its paragraphs, because a subrace has the traits of
its race besides its own (the issue asks for no separate entries). A table in a race (the Draconic
Ancestry, set in the Calibri fonts) is left out. The one **background**, Acolyte, is a title
with its words, three bold labelled lines and a `Feature:` title with its words; its tables of
personal characteristics are left out. The one **feat**, Grappler, is a title, an italic
`Prerequisite:` line and its words. When something doesn't look as expected the tool stops and says
where, instead of losing a race.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.items51 import _is_body, _paragraphs
from dmbot.devtools.srd.parse import SrdError, is_title
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line

SIZE = re.compile(r"[Yy]our size is (?P<size>Tiny|Small|Medium|Large)\b")
SPEED = re.compile(r"[Yy]our base walking speed is (?P<speed>\d+ feet)\b")
LABELS = ("Skill Proficiencies", "Languages", "Equipment")
PREREQUISITE = re.compile(r"^Prerequisite: (?P<prerequisite>.+)$")


@dataclass(frozen=True, slots=True)
class Race:
    name: str
    size: str  # "Medium"
    speed: str  # "25 feet"
    text: str  # the traits, one paragraph each, and each subrace under its heading
    page: int


@dataclass(frozen=True, slots=True)
class Background:
    name: str
    skills: str
    languages: str
    equipment: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Feat:
    name: str
    prerequisite: str
    text: str
    page: int


def _titles(lines: Sequence[Line]) -> list[int]:
    return [i for i, ln in enumerate(lines) if is_title(ln)]


def _text(lines: Sequence[Line], vocab: Vocabulary, heading: str = "") -> str:
    """The words of `lines` with each title as a heading line of its own (after `heading`, such
    as "Subrace: "), and the lines of the paragraphs between the titles run together."""
    out: list[str] = []
    start = 0
    for i in [*_titles(lines), len(lines)]:
        block = _paragraphs(lines[start:i], vocab)
        if block:
            out.append(block)
        if i < len(lines):
            out.append(heading + vocab.repair(clean(lines[i].text)))
        start = i + 1
    return "\n".join(out)


def parse_races(lines: Sequence[Line], vocab: Vocabulary) -> list[Race]:
    """Every race in the lines of the Races chapter: a title followed by the same words and
    `Traits`. What comes before the first (the chapter's introduction to racial traits) is
    left out."""
    starts = [
        i
        for i in range(len(lines) - 1)
        if is_title(lines[i])
        and is_title(lines[i + 1])
        and clean(lines[i + 1].text) == f"{clean(lines[i].text)} Traits"
    ]
    if not starts:
        raise SrdError("Races: no race found")
    races: list[Race] = []
    seen: set[str] = set()
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        name = vocab.repair(clean(lines[start].text))
        block = lines[start + 2 : end]  # (after the race's title and its "Traits" title)
        # A subrace's heading says so: its own "Ability Score Increase." is not the race's.
        text = _text(block, vocab, heading="Subrace: ")
        for ln in block:  # a table is left out; the text says so, as the 5.2.1 species do
            if not _is_body(ln) and ln.first_font.endswith("Bold") and not is_title(ln):
                text += f"\n(The {clean(ln.text)} table is not included here.)"
                break
        where = f"Race {name!r} on page {lines[start].page}"
        size, speed = SIZE.search(text), SPEED.search(text)
        if size is None or speed is None:
            raise SrdError(f"{where}: can't read its size or its speed")
        if not text:
            raise SrdError(f"{where}: no traits")
        if name in seen:
            raise SrdError(f"{where}: comes twice")
        seen.add(name)
        races.append(Race(name, size["size"], speed["speed"], text, lines[start].page))
    return races


def parse_background(lines: Sequence[Line], vocab: Vocabulary) -> Background:
    """The Acolyte, from the lines of its title up to its Suggested Characteristics."""
    if not lines or not is_title(lines[0]):
        raise SrdError("Background: the lines don't start with its title")
    name = vocab.repair(clean(lines[0].text))
    body = lines[1:]
    fields: dict[str, str] = {}
    for label in LABELS:
        found = [ln for ln in body if clean(ln.text).startswith(f"{label}:")]
        if len(found) != 1:
            raise SrdError(f"Background {name!r}: {len(found)} lines say {label!r}, not one")
    rows = _text(body, vocab).splitlines()
    for row in rows:
        label, colon, value = row.partition(":")
        if colon and label in LABELS:
            fields[label] = value.strip()
    # The three labelled lines first, as the 5.2.1's backgrounds have them, then the words.
    labelled = [r for r in rows if r.partition(":")[0] in LABELS]
    text = "\n".join([*labelled, *(r for r in rows if r not in labelled)])
    unread = [label for label in LABELS if label not in fields]
    if unread:
        raise SrdError(f"Background {name!r}: can't read the line(s) {', '.join(unread)}")
    if "Feature:" not in text:
        raise SrdError(f"Background {name!r}: no Feature")
    return Background(
        name,
        fields["Skill Proficiencies"],
        fields["Languages"],
        fields["Equipment"],
        text,
        lines[0].page,
    )


def parse_feat(lines: Sequence[Line], vocab: Vocabulary) -> Feat:
    """The Grappler: a title, an italic prerequisite line, then its words."""
    if len(lines) < 3 or not is_title(lines[0]):
        raise SrdError("Feat: the lines don't start with its title")
    name = vocab.repair(clean(lines[0].text))
    match = PREREQUISITE.match(clean(lines[1].text))
    if match is None:
        raise SrdError(f"Feat {name!r}: no Prerequisite line: {clean(lines[1].text)!r}")
    body = [ln for ln in lines[2:] if _is_body(ln)]
    text = _paragraphs(body, vocab)
    if not text:
        raise SrdError(f"Feat {name!r}: no description")
    return Feat(name, match["prerequisite"], text, lines[0].page)
