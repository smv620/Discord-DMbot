"""Turning the SRD 5.2.1's weapons, armor and weapon properties into entries (#1919). Pure: no PDF,
no files.

The Weapons and Armor tables are set in the sans fonts, one row to a line, but a wide cell wraps
and the last columns of many rows are printed apart from the rest of the row, further right on
the same line (the Monk's class table is the same). So a row is its main line, then the
continuation line under it (a property list that wraps), then whatever is printed further right at
the same height. The properties and the mastery properties are headed by a title in the
semi-bold sans font at a column's edge with their words under it. When something doesn't look as
expected the tool stops and says where, instead of losing a row.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.items import _at_an_edge
from dmbot.devtools.srd.parse import Mender, SrdError, _clean, _paragraphs, is_title
from dmbot.devtools.srd.pdf import Line

PROPERTIES = (
    "Ammunition", "Finesse", "Heavy", "Light", "Loading", "Range", "Reach", "Thrown", "Two-Handed",
    "Versatile",
)  # fmt: skip
MASTERIES = ("Cleave", "Graze", "Nick", "Push", "Sap", "Slow", "Topple", "Vex")
# The Rules Glossary already has a "Reach" (a creature's reach); the weapon property of that name
# says something else, so it keeps both words in its name rather than shadow or drop either.
RENAMED = {"Reach": "Reach (Weapon Property)"}
SECTION_TITLES = ("Properties", "Mastery Properties")  # introductions, left out
WEAPON_TAG, MASTERY_TAG = "Weapon Property", "Mastery Property"
ROW_LEFT = 63.0  # a table row's first line starts at the name column, a little in from this
SAME_ROW = 1.0  # points: a fragment printed further right on the same line
CONTINUES = 14.0  # a wrapped cell sits at most this far under its row
TAIL_FROM = 200.0  # x: what starts here or further right is not the name column

WEAPON_ROW = re.compile(
    r"^(?P<name>[A-Z][A-Za-z' -]*?) (?P<damage>\d+(?:d\d+)?) "
    r"(?P<type>Bludgeoning|Piercing|Slashing) (?P<rest>.+)$"
)
COST = re.compile(r"\s+(?P<cost>[\d,]+ [GSC]P)$")
WEIGHT = re.compile(r"\s+(?P<weight>\d+(?:/\d+)? lb\.|—)$")
ARMOR_ROW = re.compile(
    r"^(?P<name>[A-Z][A-Za-z' -]*?) (?P<ac>\+?\d+(?: \+ Dex modifier(?: \(max 2\))?)?) "
    r"(?P<strength>—|Str \d+) (?P<stealth>—|Disadvantage) "
    r"(?P<weight>\d+ lb\.|—) (?P<cost>[\d,]+ [GSC]P)$"
)
WEAPON_CATEGORY = re.compile(r"^(?P<category>(?:Simple|Martial) (?:Melee|Ranged)) Weapons$")
ARMOR_CATEGORY = re.compile(r"^(?P<category>Light|Medium|Heavy|Shield)(?: Armor)? \((?P<don>.+)\)$")


@dataclass(frozen=True, slots=True)
class Weapon:
    name: str
    category: str  # "Simple Melee", "Martial Ranged", ...
    damage: str  # "1d8", or "1"
    damage_type: str
    properties: str  # as printed, "Thrown (Range 20/60), Versatile (1d8)"; "" for none
    mastery: str
    weight: str
    cost: str
    page: int


@dataclass(frozen=True, slots=True)
class Armor:
    name: str
    category: str  # "Light", "Medium", "Heavy", "Shield"
    ac: str  # as printed: "11 + Dex modifier", "16", "+2"
    strength: str  # "Str 13", or "—" for none
    stealth: str  # "Disadvantage", or "—"
    weight: str
    cost: str
    don_doff: str  # "1 Minute to Don or Doff"
    page: int


@dataclass(frozen=True, slots=True)
class Property:
    name: str
    tag: str  # "Weapon Property" or "Mastery Property"
    text: str
    page: int


def _rows(lines: Sequence[Line], left: tuple[float, float]) -> list[tuple[Line, str]]:
    """Each table row's main line, with the continuation under it and whatever is printed
    further right at the same height joined on, in that order."""
    mains = [ln for ln in lines if left[0] <= ln.x <= left[1]]
    rows: list[tuple[Line, str]] = []
    for main in mains:
        text = _clean(main.text)
        if not text or text.startswith("Name "):
            continue
        rows.append((main, text))
    _no_stray_lines(lines, rows)
    out: list[tuple[Line, str]] = []
    for i, (main, text) in enumerate(rows):
        below = [
            ln
            for ln in lines
            if ln.page == main.page
            and TAIL_FROM <= ln.x < 300
            and 0 < main.y - ln.y <= CONTINUES
            and (i + 1 == len(rows) or ln.y > rows[i + 1][0].y)
        ]
        tails = sorted(
            (
                ln
                for ln in lines
                if ln.page == main.page and ln.x >= 300 and abs(ln.y - main.y) <= SAME_ROW
            ),
            key=lambda ln: ln.x,
        )
        joined = " ".join(
            [text, *(_clean(ln.text) for ln in below), *(_clean(t.text) for t in tails)]
        )
        out.append((main, joined))
    return out


def _no_stray_lines(lines: Sequence[Line], rows: Sequence[tuple[Line, str]]) -> None:
    """Every line of the table's name columns is a row, a heading, the column heads, the
    continuation of a row or something printed further right on a row's line; anything else
    would be glued to the wrong row or lost."""
    mains = {id(main) for main, _ in rows}
    for ln in lines:
        text = _clean(ln.text)
        if not text or id(ln) in mains:
            continue
        if ln.first_font.endswith("Italic") or text.startswith(("Name ", "Armor Armor ")):
            continue  # a heading, or the column heads
        if ln.x >= 300:
            continue  # printed further right: joined to the row at its height, if any
        if TAIL_FROM <= ln.x < 300 and any(0 < main.y - ln.y <= CONTINUES for main, _ in rows):
            continue  # the continuation of a row
        if is_title(ln) and ln.x < ROW_LEFT:
            continue  # the table's own title
        raise SrdError(f"Equipment table: a line that is no row: {text!r}")


def parse_weapons(lines: Sequence[Line]) -> list[Weapon]:
    """Every weapon in the lines of the Weapons table page."""
    weapons: list[Weapon] = []
    seen: set[str] = set()
    category = ""
    headings = {
        ln.y: _clean(ln.text)
        for ln in lines
        if ln.first_font.endswith("Italic") and ln.x < ROW_LEFT
    }
    rows = _rows([ln for ln in lines if ln.x >= 50], (60.0, 64.0))
    ordered = sorted(
        [*((main.y, (main, joined), "") for main, joined in rows),
         *((y, None, text) for y, text in headings.items())],
        key=lambda item: -item[0],
    )  # fmt: skip
    for _, row_lines, heading in ordered:
        if row_lines is None:
            match = WEAPON_CATEGORY.match(heading)
            if match is None:
                raise SrdError(f"Weapons table: a heading it can't read: {heading!r}")
            category = match["category"]
            continue
        main, joined = row_lines
        weapons.append(_weapon(joined, category, main.page, seen))
    if not weapons:
        raise SrdError("Weapons table: no weapons found")
    return weapons


def _weapon(joined: str, category: str, page: int, seen: set[str]) -> Weapon:
    match = WEAPON_ROW.match(joined)
    if match is None or not category:
        raise SrdError(f"Weapons table: a row it can't read: {joined!r}")
    rest = match["rest"]
    cost = COST.search(rest)
    if cost is None:
        raise SrdError(f"Weapons table: {match['name']!r} has no cost: {joined!r}")
    rest = rest[: cost.start()]
    weight = WEIGHT.search(rest)
    if weight is None:
        raise SrdError(f"Weapons table: {match['name']!r} has no weight: {joined!r}")
    rest = rest[: weight.start()]
    words = rest.split(" ")
    mastery = words[-1] if words else ""
    if mastery not in MASTERIES:
        raise SrdError(f"Weapons table: {match['name']!r} has the mastery {mastery!r}")
    properties = " ".join(words[:-1]).strip()
    properties = "" if properties == "—" else properties
    name = match["name"]
    if name in seen:
        raise SrdError(f"Weapons table: {name!r} comes twice")
    seen.add(name)
    return Weapon(
        name, category, match["damage"], match["type"], properties, mastery,
        weight["weight"], cost["cost"], page,
    )  # fmt: skip


def parse_armor(lines: Sequence[Line]) -> list[Armor]:
    """Every armor, and the Shield, in the lines of the page the Armor table is on."""
    armors: list[Armor] = []
    seen: set[str] = set()
    head = next((ln for ln in lines if _clean(ln.text).startswith("Armor Armor Class")), None)
    if head is None:
        raise SrdError("Armor table: its column heads were not found")
    lines = [ln for ln in lines if ln.y < head.y and "Cambria" not in ln.first_font]
    headings = {
        ln.y: _clean(ln.text) for ln in lines if ln.first_font.endswith("Italic") and ln.x < 70
    }
    rows = _rows([ln for ln in lines if ln.x >= 60], (70.0, 80.0))
    ordered = sorted(
        [*((main.y, (main, joined), "") for main, joined in rows),
         *((y, None, text) for y, text in headings.items())],
        key=lambda item: -item[0],
    )  # fmt: skip
    category = don = ""
    for _, row_lines, heading in ordered:
        if row_lines is None:
            match = ARMOR_CATEGORY.match(heading)
            if match is None:
                raise SrdError(f"Armor table: a heading it can't read: {heading!r}")
            category, don = match["category"], match["don"]
            continue
        main, joined = row_lines
        row = ARMOR_ROW.match(joined)
        if row is None or not category:
            raise SrdError(f"Armor table: a row it can't read: {joined!r}")
        if row["name"] in seen:
            raise SrdError(f"Armor table: {row['name']!r} comes twice")
        seen.add(row["name"])
        armors.append(
            Armor(
                row["name"], category, row["ac"], row["strength"], row["stealth"], row["weight"],
                row["cost"], don, main.page,
            )
        )  # fmt: skip
    if not armors:
        raise SrdError("Armor table: no armor found")
    return armors


def parse_properties(lines: Sequence[Line]) -> list[Property]:
    """Every weapon property and mastery property in the lines of the Equipment chapter's first
    pages. Only the named ones are entries; a title with words under it that is not one of them
    or an introduction would be lost, so it stops the tool."""
    start = next(
        (i for i, ln in enumerate(lines) if is_title(ln) and _clean(ln.text) == "Properties"), None
    )
    if start is None:
        raise SrdError("Equipment: the Properties section was not found")
    lines = lines[start:]  # what comes before it (coins, the Weapons text) is not a property
    mender = Mender(lines)
    found: dict[str, Property] = {}
    expected = {*PROPERTIES, *MASTERIES}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (is_title(line) and _at_an_edge(line)):
            i += 1
            continue
        name = _clean(line.text)
        end = i + 1
        while end < len(lines) and not (is_title(lines[end]) and _at_an_edge(lines[end])):
            end += 1
        if name in expected:
            body = [ln for ln in lines[i + 1 : end] if ln.first_font.startswith("Cambria")]
            text = _paragraphs(body, mender)
            if not text:
                raise SrdError(f"Property {name!r} on page {line.page}: no description")
            if name in found:
                raise SrdError(f"Property {name!r} comes twice (page {line.page})")
            tag = WEAPON_TAG if name in PROPERTIES else MASTERY_TAG
            shown = RENAMED.get(name, name)
            found[name] = Property(shown, tag, text, line.page)
        elif name in SECTION_TITLES:
            pass  # an introduction; its words are left out
        elif _starts_text(lines, i + 1, end):
            raise SrdError(f"Equipment: the heading {name!r} on page {line.page} has text under it")
        i = end
    missing = sorted(expected - set(found))
    if missing:
        raise SrdError(f"Equipment: the properties {missing} were not found")
    ordered = [*PROPERTIES, *MASTERIES]
    return [found[name] for name in ordered]


def _starts_text(lines: Sequence[Line], start: int, end: int) -> bool:
    return any(ln.first_font.startswith("Cambria") for ln in lines[start:end])
