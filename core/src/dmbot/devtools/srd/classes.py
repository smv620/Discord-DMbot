"""Turning the SRD 5.2.1's Classes chapter into class features (#1900). Pure: no PDF, no files.

A class feature is a title in the semi-bold sans font, `Level 1: Rage`, with its description
below it up to the next title. The title of a class (`Barbarian`) and of a subclass
(`Barbarian Subclass:` and the name on the next line, or `Cleric Subclass: Life Domain`) say
which class and subclass the features after it belong to. Anything else in the semi-bold sans
font at a column's edge (`Bard Spell List`, `Beast Shapes`, the option lists of the Sorcerer and
the Warlock) is not a feature and ends the one before it; the class tables, set in the plain
sans font, are left out as the Rules Glossary's tables were, because their cells do not read as
sentences. When something doesn't look as expected the tool stops and says where.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.parse import (
    COLUMN_EDGES,
    EDGE_SLACK,
    Mender,
    SrdError,
    _clean,
    _paragraphs,
    is_title,
)
from dmbot.devtools.srd.pdf import Line
from dmbot.rules.index import CLASS_NAMES

CLASSES = tuple(CLASS_NAMES)
LEVEL_TITLE = re.compile(r"^Level (?P<level>\d+): (?P<name>.+)$")
SUBCLASS_TITLE = re.compile(r"^(?P<klass>[A-Z][a-z]+) Subclass:(?: (?P<name>.+))?$")
SAME_HEADING = 20.0  # title lines this close (points) with no text between are one heading


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    klass: str
    level: int
    subclass: str  # "" for a feature of the class itself
    text: str
    page: int


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


def _heading(lines: Sequence[Line], i: int) -> tuple[str, int]:
    """The words of the heading that starts at `lines[i]` and the index after it: a heading
    that wraps ("Monk Subclass: Warrior of the" / "Open Hand") is its title lines in a row."""
    text = _clean(lines[i].text)
    j = i + 1
    while (
        j < len(lines)
        and is_title(lines[j])
        and _at_an_edge(lines[j])
        and abs(lines[j].x - lines[i].x) <= EDGE_SLACK
        and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
        and not LEVEL_TITLE.match(_clean(lines[j].text))
    ):
        text += " " + _clean(lines[j].text)
        j += 1
    return text, j


def _is_heading(line: Line) -> bool:
    """A title at a column's edge that ends the feature above it: a feature, a class or a
    subclass, or any other heading with words under it (a spell list, a list of options)."""
    return is_title(line) and _at_an_edge(line)


def _is_table_caption(lines: Sequence[Line], i: int) -> bool:
    """A title at the edge with a table under it (`Rogue Features`, then its column heads, set
    a few points in or in the plain sans font). A table sits in the flow of a column and may
    come in the middle of a feature (Sneak Attack, Spellcasting), so it does not end one."""
    text = _clean(lines[i].text)
    if text in CLASSES or LEVEL_TITLE.match(text) or SUBCLASS_TITLE.match(text):
        return False
    after = lines[i + 1] if i + 1 < len(lines) else None
    return after is not None and (
        after.first_font.startswith("GillSans") and not _is_heading(after)
    )


def _body_end(lines: Sequence[Line], start: int) -> int:
    """Where the feature whose title is at `start - 1` ends: at the next heading, not counting
    a table (its caption through the lines before the next body text) that sits inside it."""
    j = start
    while j < len(lines):
        if _is_heading(lines[j]):
            if not _is_table_caption(lines, j):
                return j
            j += 1
            while j < len(lines) and not lines[j].first_font.startswith("Cambria"):
                if _is_heading(lines[j]) and _is_a_new_heading(lines[j]):
                    break
                j += 1
            continue
        j += 1
    return j


def _is_a_new_heading(line: Line) -> bool:
    text = _clean(line.text)
    return text in CLASSES or bool(LEVEL_TITLE.match(text) or SUBCLASS_TITLE.match(text))


def parse_features(lines: Sequence[Line]) -> list[Feature]:
    """Every `Level N: Name` feature in the lines of the Classes chapter, with its class and
    subclass."""
    mender = Mender(lines)
    features: list[Feature] = []
    klass = subclass = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (is_title(line) and _at_an_edge(line)):
            i += 1
            continue
        text = _clean(line.text)
        if text in CLASSES:
            klass, subclass = text, ""
            i += 1
            continue
        if SUBCLASS_TITLE.match(text):
            full, i = _heading(lines, i)
            match = SUBCLASS_TITLE.match(full)
            assert match is not None
            if match["klass"] != klass:
                raise SrdError(f"Subclass {full!r} on page {line.page} is under {klass!r}")
            subclass = match["name"] or ""
            if not subclass:
                raise SrdError(f"Subclass heading on page {line.page} has no name")
            continue
        match = LEVEL_TITLE.match(text)
        end = _body_end(lines, i + 1)
        if match is None:  # a heading that is not a feature (it only ends the one before)
            i = end
            continue
        if not klass:
            raise SrdError(f"Feature {text!r} on page {line.page} comes before any class")
        if match["name"] == f"{klass} Subclass":  # only says "you gain a subclass": no rule in it
            i = end
            continue
        body = [ln for ln in lines[i + 1 : end] if not ln.first_font.startswith("GillSans")]
        words = _paragraphs(body, mender)
        if not words:
            raise SrdError(f"Feature {text!r} on page {line.page}: no description")
        features.append(
            Feature(match["name"], klass, int(match["level"]), subclass, words, line.page)
        )
        i = end
    return features
