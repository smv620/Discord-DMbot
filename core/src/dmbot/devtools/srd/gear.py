"""Turning the SRD 5.2.1's tools, adventuring gear, mounts and vehicles into entries (#1927). Pure:
no PDF, no files.

A **tool** is a title `Thieves' Tools (25 GP)` in the semi-bold sans font, then lines that each
start with a label in the same font (`Ability: Dexterity Weight: 1 lb.`, `Utilize:`, `Craft:` or
`Variants:`); a line that wraps is set in a little under it. An **adventuring gear** entry is the
same kind of title (`Healer's Kit (5 GP)`) with its words under it in the serif font, and any small
table inside it (the Arcane Focuses) kept as the plain lines it is printed in; its weight is in the
Adventuring Gear table that has a page of its own. **Mounts** and **vehicles** are rows of three
tables; a ship's row is printed in two pieces, the rest of it further right on the same line. When
something doesn't look as expected the tool stops and says where, instead of losing an entry.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.equipment import SAME_ROW
from dmbot.devtools.srd.items import SAME_HEADING, _at_an_edge
from dmbot.devtools.srd.parse import COLUMN_EDGES, Mender, SrdError, _clean, _paragraphs, is_title
from dmbot.devtools.srd.pdf import Line

CONTINUED = (4.0, 14.0)  # a wrapped line of a tool is set in from the column's edge by about this
GEAR_HEADING = "Adventuring Gear"
GROUP_TITLES = ("Artisan’s Tools", "Other Tools")  # tool groups: their words are an introduction
# The price after a name: "(25 GP)", "(Varies)", and the Spell Scroll's two prices in one bracket.
TITLE = re.compile(r"^(?P<name>[A-Z][^()]*?) \((?P<cost>Varies|[\d,]+ [GSC]P)\)$")
WRAPPED_TITLE = re.compile(r"^(?P<name>[A-Z][^()]*?) \((?P<cost>[^()]+; [^()]+)\)$")
# The PDF prints one and a half in a sentence as "11/2" ("holds up to 11/2 pints"); its tables
# print "1½". No other number in the gear text reads that way.
_FRACTION = re.compile(r"(?<![\d/.,])11/2\b")
ABILITY_LINE = re.compile(
    r"^Ability: (?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
    r"Weight: (?P<weight>Varies|—|\d+ lb\.)$"
)
LABELS = ("Utilize", "Craft", "Variants")
WEIGHT = r"(?:—|Varies|\d+(?:½|/\d+)? lb\.(?: \(full\))?)"
COST = r"(?:Varies|[\d,]+ [GSC]P)"
GEAR_ROW = re.compile(rf"^(?P<name>[A-Z].*?) (?P<weight>{WEIGHT}) (?P<cost>{COST})$")
MOUNT_ROW = re.compile(r"^(?P<name>[A-Z].*?) (?P<capacity>[\d,]+ lb\.) (?P<cost>[\d,]+ [GSC]P)$")
DRAWN_ROW = re.compile(r"^(?P<name>[A-Z].*?) (?P<weight>\d+ lb\.|—) (?P<cost>[\d,]+ [GSC]P)$")
# Speed, then crew, passengers, cargo, AC, hit points and damage threshold (each one cell, a dash
# where the ship has none), then the price.
SHIP_ROW = re.compile(
    r"^(?P<name>[A-Z][A-Za-z ]*?) (?P<speed>[\d½/]+ mph) (?P<cells>(?:\S+ ){6})(?P<cost>[\d,]+ GP)$"
)
SHIP_CELLS = ("crew", "passengers", "cargo", "ac", "hp", "damage_threshold")
# Rows of the Tack, Harness, and Drawn Vehicles table that are not vehicles: see ATTRIBUTION.
NOT_VEHICLES = ("Feed per day", "Saddle", "Exotic", "Military", "Riding", "Stabling per day")


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    group: str  # "Artisan's Tools" or "Other Tools"
    cost: str  # "25 GP", or "Varies"
    ability: str
    weight: str  # "1 lb.", or "—" or "Varies"
    utilize: str
    craft: str  # "" if the tool crafts nothing
    variants: str  # "" if it has none
    page: int
    text: str = ""  # the SRD 5.1's words about the tool (it has no Utilize or Craft lines)


@dataclass(frozen=True, slots=True)
class Gear:
    name: str
    group: str  # "Adventuring Gear", "Mount" or "Vehicle"
    cost: str
    weight: str  # "5 lb.", "—" or "Varies"; "" for a mount (it has a carrying capacity)
    text: str
    page: int
    extra: tuple[tuple[str, str], ...] = ()  # more fields of a mount's or a ship's row


def _is_title_at_edge(line: Line) -> bool:
    return is_title(line) and _at_an_edge(line)


def _wrapped_with(lines: Sequence[Line], i: int) -> int:
    """The index after the heading that starts at `lines[i]` (a title can wrap to a second line:
    the Spell Scroll's price)."""
    j = i + 1
    while (
        _clean(lines[i].text).count("(") > _clean(" ".join(ln.text for ln in lines[i:j])).count(")")
        and j < len(lines)
        and is_title(lines[j])
        and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
    ):
        j += 1
    return j


def _heading(lines: Sequence[Line], i: int) -> tuple[str, int]:
    j = _wrapped_with(lines, i)
    return _clean(" ".join(ln.text for ln in lines[i:j])), j


def _starts_entry(lines: Sequence[Line], i: int) -> bool:
    """A title with a price after the name: `Backpack (2 GP)`."""
    if not _is_title_at_edge(lines[i]):
        return False
    text, _ = _heading(lines, i)
    return TITLE.match(text) is not None or WRAPPED_TITLE.match(text) is not None


def parse_tools(lines: Sequence[Line]) -> list[Tool]:
    """Every tool in the lines from the Artisan's Tools heading to the Adventuring Gear heading."""
    mender = Mender(lines)
    tools: list[Tool] = []
    seen: set[str] = set()
    group = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _is_title_at_edge(line):
            i += 1
            continue
        text, j = _heading(lines, i)
        if text in GROUP_TITLES:
            group = text
            i = j
            continue
        match = TITLE.match(text)
        if match is None:
            raise SrdError(f"Tools: a heading it can't read on page {line.page}: {text!r}")
        name = match["name"]
        end = j
        while end < len(lines) and not _starts_entry(lines, end) and not _is_group(lines, end):
            end += 1
        if name in seen:
            raise SrdError(f"Tool {name!r} comes twice (page {line.page})")
        seen.add(name)
        tools.append(_tool(name, match["cost"], group, lines[j:end], mender, line.page))
        i = end
    if not tools:
        raise SrdError("Tools: none found")
    return tools


def _is_group(lines: Sequence[Line], i: int) -> bool:
    return _is_title_at_edge(lines[i]) and _clean(lines[i].text) in GROUP_TITLES


def _tool(
    name: str, cost: str, group: str, block: Sequence[Line], mender: Mender, page: int
) -> Tool:
    where = f"Tool {name!r} on page {page}"
    fields: dict[str, str] = {}
    label = ""
    for ln in block:
        text = _clean(ln.text)
        edge = min(COLUMN_EDGES, key=lambda e: abs(e - ln.x))  # the two columns
        wrapped = CONTINUED[0] < ln.x - edge < CONTINUED[1]
        if wrapped:
            if not label:
                raise SrdError(f"{where}: a wrapped line with nothing before it: {text!r}")
            fields[label] = (
                mender.join(fields[label], text)
                if fields[label].endswith("-")
                else f"{fields[label]} {text}"
            )
            continue
        head = text.partition(":")[0]
        if head == "Ability":
            label = "Ability"
            fields[label] = text
        elif head in LABELS:
            label = head
            fields[label] = text.partition(":")[2].strip()
        else:
            raise SrdError(f"{where}: a line it can't read: {text!r}")
    ability = ABILITY_LINE.match(fields.get("Ability", ""))
    if ability is None:
        raise SrdError(f"{where}: no readable Ability and Weight line: {fields.get('Ability')!r}")
    if not fields.get("Utilize"):
        raise SrdError(f"{where}: no Utilize line")
    return Tool(
        name, group, cost, ability["ability"], ability["weight"], fields["Utilize"],
        fields.get("Craft", ""), fields.get("Variants", ""), page,
    )  # fmt: skip


def parse_gear(lines: Sequence[Line]) -> list[Gear]:
    """Every adventuring gear entry, from the lines of the Adventuring Gear heading to the Mounts
    and Vehicles heading: the entries in alphabetical order, and the table of weights."""
    heads = [i for i, ln in enumerate(lines) if is_title(ln) and _clean(ln.text) == GEAR_HEADING]
    if len(heads) != 2:
        raise SrdError(f"Adventuring Gear: expected a heading and its table, found {len(heads)}")
    table_page = lines[heads[1]].page
    table = _weights([ln for ln in lines if ln.page == table_page and not is_title(ln)])
    stream = [ln for ln in lines[heads[0] + 1 :] if ln.page != table_page]
    mender = Mender(stream)
    gear: list[Gear] = []
    seen: set[str] = set()
    i = 0
    while i < len(stream):
        if not _starts_entry(stream, i):
            i += 1
            continue
        text, j = _heading(stream, i)
        match = TITLE.match(text) or WRAPPED_TITLE.match(text)
        if match is None:  # (_starts_entry said it was one)
            raise SrdError(f"Gear: a heading it can't read on page {stream[i].page}: {text!r}")
        name, cost = match["name"], match["cost"]
        end = j
        while end < len(stream) and not _starts_entry(stream, end):
            end += 1
        words = _FRACTION.sub("1½", _paragraphs(stream[j:end], mender))
        if not words:
            raise SrdError(f"Gear {name!r} on page {stream[i].page}: no description")
        if name in seen:
            raise SrdError(f"Gear {name!r} comes twice (page {stream[i].page})")
        seen.add(name)
        weight, row_cost = _from_table(name, table, stream[i].page)
        if row_cost != cost and ";" not in cost:
            raise SrdError(f"Gear {name!r}: costs {cost!r} in its entry, {row_cost!r} in the table")
        gear.append(Gear(name, GEAR_HEADING, cost, weight, words, stream[i].page))
        i = end
    unused = sorted(
        set(table) - {g.name for g in gear} - {"Spell Scroll (Cantrip)", "Spell Scroll (Level 1)"}
    )
    if unused:
        raise SrdError(f"Adventuring Gear table: rows with no entry: {unused}")
    if not gear:
        raise SrdError("Adventuring Gear: none found")
    return gear


def _weights(lines: Sequence[Line]) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for ln in lines:
        text = _clean(ln.text)
        if not text or text.startswith("Item "):
            continue
        row = GEAR_ROW.match(text)
        if row is None:
            raise SrdError(f"Adventuring Gear table: a row it can't read: {text!r}")
        if row["name"] in table:
            raise SrdError(f"Adventuring Gear table: {row['name']!r} comes twice")
        table[row["name"]] = (row["weight"], row["cost"])
    return table


def _from_table(name: str, table: dict[str, tuple[str, str]], page: int) -> tuple[str, str]:
    if name in table:
        return table[name]
    # One entry, two rows (a cantrip's and a level 1's). Neither row has a weight (a dash), which
    # is why the entry says none instead of reading one from a row.
    if name == "Spell Scroll":
        return "—", "Cantrip, 30 GP; Level 1, 50 GP"
    raise SrdError(f"Gear {name!r} on page {page} has no row in the Adventuring Gear table")


def parse_mounts(lines: Sequence[Line]) -> list[Gear]:
    """The mounts, the vehicles pulled by an animal and the ships, from the lines from the
    Mounts and Other Animals heading to the Crew heading (a ship's row is in two pieces)."""
    gear: list[Gear] = []
    seen: set[str] = set()

    def add(entry: Gear) -> None:
        if entry.name in seen:
            raise SrdError(f"{entry.group} {entry.name!r} comes twice (page {entry.page})")
        seen.add(entry.name)
        gear.append(entry)

    section = ""
    for i, ln in enumerate(lines):
        text = _clean(ln.text)
        if is_title(ln) and _at_an_edge(ln):
            if text in (
                "Mounts and Other Animals",
                "Tack, Harness, and Drawn Vehicles",
                "Airborne and Waterborne Vehicles",
            ):
                section = text
            elif not text.startswith(("Item ", "Ship ")):  # (a table's header row)
                section = ""  # Large Vehicles, Crew...: words, not rows
            continue
        if is_title(ln) or not text:
            continue
        if section == "Mounts and Other Animals":
            row = MOUNT_ROW.match(text)
            if row is None:
                raise SrdError(f"Mounts table: a row it can't read: {text!r}")
            add(
                Gear(
                    row["name"],
                    "Mount",
                    row["cost"],
                    "",
                    "",
                    ln.page,
                    (("capacity", row["capacity"]),),
                )
            )
        elif section == "Tack, Harness, and Drawn Vehicles":
            row = DRAWN_ROW.match(text)
            if row is None:
                if text == "Saddle":  # a heading over three rows
                    continue
                raise SrdError(f"Drawn vehicles table: a row it can't read: {text!r}")
            if row["name"] in NOT_VEHICLES:
                continue
            add(Gear(row["name"], "Vehicle", row["cost"], row["weight"], "", ln.page))
        elif section == "Airborne and Waterborne Vehicles" and ln.x < 100:
            add(_ship(lines, i, text))
    if not any(g.group == "Mount" for g in gear) or not any(g.group == "Vehicle" for g in gear):
        raise SrdError("Mounts and vehicles: a table was not found")
    return gear


def _ship(lines: Sequence[Line], i: int, text: str) -> Gear:
    main = lines[i]
    tails = sorted(
        (
            ln
            for ln in lines
            if ln.page == main.page and ln.x >= 300 and abs(ln.y - main.y) <= SAME_ROW
        ),
        key=lambda ln: ln.x,
    )
    joined = " ".join([text, *(_clean(t.text) for t in tails)])
    row = SHIP_ROW.match(joined)
    if row is None:
        raise SrdError(f"Vehicles table: a row it can't read: {joined!r}")
    cells = row["cells"].split()
    extra = (("speed", row["speed"]), *zip(SHIP_CELLS, cells, strict=True))
    return Gear(row["name"], "Vehicle", row["cost"], "", "", main.page, extra)
