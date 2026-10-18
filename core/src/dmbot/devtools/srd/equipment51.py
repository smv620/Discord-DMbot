"""Turning the 2014 SRD 5.1's Equipment chapter into entries (#1931). Pure: no PDF, no files.

The 5.1 chapter keeps its weapons, armor, gear, tools, mounts and vehicles in tables set in the
Calibri fonts (one row to a line, a wrapped cell or the last cell printed further right on the
same line), under italic sub-headings (`Simple Melee Weapons`, `Light Armor`); and its words in the
serif fonts, as paragraphs that start with a bold-italic lead-in (`Acid. As an action, ...`). So:

- a **weapon** or an **armor** is a table row, and the 5.1 has no weapon mastery;
- a **weapon property** is a lead-in paragraph under `Weapon Properties`;
- an **adventuring gear** entry is a lead-in paragraph with its price and weight from the
  Adventuring Gear table; a table row with no paragraph is an entry too, with its row as its words,
  and the small tables inside the table (Arcane focus, Ammunition) are one entry each;
- a **tool** is a row of the Tools table, with its lead-in paragraph;
- a **mount** or a **vehicle** is a row of the Mounts and Vehicles tables.

When something doesn't look as expected the tool stops and says where, instead of losing a row.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.equipment import Armor, Property, Weapon
from dmbot.devtools.srd.gear import Gear, Tool
from dmbot.devtools.srd.items51 import _paragraphs
from dmbot.devtools.srd.parse import SrdError, is_title
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line

LEFT_EDGE, RIGHT_EDGE = 57.6, 328.6  # where a column of the 5.1 pages starts
SAME_ROW = 1.0  # points: lines this close in height are one printed row
HALF_PAGE = 300.0  # x: a table in a column of its own starts on one side of this
LEAD_IN = "Cambria-BoldItalic"
PROPERTIES = (
    "Ammunition",
    "Finesse",
    "Heavy",
    "Light",
    "Loading",
    "Range",
    "Reach",
    "Special",
    "Thrown",
    "Two-Handed",
    "Versatile",
)
# The Rules Glossary of the 5.2.1 has a "Reach" (a creature's); the weapon property keeps both words
# in its name, as the 5.2.1's does, so neither is shadowed.
RENAMED = {"Reach": "Reach (Weapon Property)"}
WEAPON_TAG = "Weapon Property"
SMALL_WORDS = frozenset({"or", "and", "of", "the", "a", "in"})
MONEY = r"(?P<cost>[\d,]+) (?P<coin>cp|sp|ep|gp|pp)"
WEIGHT = r"(?P<weight>—|\d+(?:½|/\d+)? lb\.(?: \(full\))?)"
WEAPON_ROW = re.compile(
    rf"^(?P<name>[A-Z][A-Za-z', ]*?) {MONEY} (?P<damage>\d+d\d+|\d+|—)"
    r"(?: (?P<type>bludgeoning|piercing|slashing))? "
    rf"{WEIGHT} (?P<properties>.+)$"
)
ARMOR_ROW = re.compile(
    rf"^(?P<name>[A-Z][A-Za-z' ]*?) {MONEY} (?P<ac>\+?\d+(?: \+ Dex modifier(?: \(max 2\))?)?) "
    rf"(?P<strength>—|Str \d+) (?P<stealth>—|Disadvantage) {WEIGHT}$"
)
DONNING_ROW = re.compile(
    r"^(?P<category>Light Armor|Medium Armor|Heavy Armor|Shield) "
    r"(?P<don>\d+ (?:minutes?|actions?)) (?P<doff>\d+ (?:minutes?|actions?))$"
)
WEAPON_CATEGORY = re.compile(r"^(?P<category>(?:Simple|Martial) (?:Melee|Ranged)) Weapons$")
ARMOR_CATEGORY = re.compile(r"^(?P<category>Light|Medium|Heavy) Armor$")
GEAR_ROW = re.compile(rf"^(?P<name>[A-Z].*?) {MONEY} {WEIGHT}$")
TOOL_ROW = GEAR_ROW
MOUNT_ROW = re.compile(
    rf"^(?P<name>[A-Z].*?) {MONEY} (?P<speed>\d+ ft\.) (?P<capacity>[\d,]+ lb\.)$"
)
DRAWN_ROW = GEAR_ROW
SHIP_ROW = re.compile(rf"^(?P<name>[A-Z].*?) {MONEY} (?P<speed>[\d½]+ mph)$")
DRAWN_VEHICLES = ("Carriage", "Cart", "Chariot", "Sled", "Wagon")
# Rows of the Tack, Harness, and Drawn Vehicles table that are not vehicles (see ATTRIBUTION.md).
LEFT_OUT_TACK = ("Barding", "Bit and bridle", "Feed", "Saddlebags", "Stabling")


@dataclass(frozen=True, slots=True)
class Row:
    page: int
    y: float
    font: str  # the font of the row's first (leftmost) line
    x: float
    text: str


def title_case(name: str) -> str:
    """ "Climber's kit" is "Climber's Kit", "Case, map or scroll" is "Case, Map or Scroll"."""
    words = name.replace("'", "’").split(" ")
    return " ".join(
        w if (i and w.lower() in SMALL_WORDS) else w[:1].upper() + w[1:]
        for i, w in enumerate(words)
    )


def money(cost: str, coin: str) -> str:
    return f"{cost} {coin.upper()}"


def rows_of(lines: Sequence[Line], *, columns: bool = False) -> list[Row]:
    """The table lines (the Calibri fonts) as printed rows: lines at one height on a page joined
    from the left. With `columns`, the two columns of a page are separate (two tables side by
    side); without, a wide table's right-hand cells join their row."""
    table = [ln for ln in lines if ln.first_font.startswith("Calibri")]
    grouped: list[list[Line]] = []

    def order(ln: Line) -> tuple[int, int, float, float]:
        return (ln.page, int(columns and ln.x >= HALF_PAGE), -ln.y, ln.x)

    for ln in sorted(table, key=order):
        for group in grouped:
            head = group[0]
            if (
                head.page == ln.page
                and abs(head.y - ln.y) <= SAME_ROW
                and (not columns or (head.x < HALF_PAGE) == (ln.x < HALF_PAGE))
            ):
                group.append(ln)
                break
        else:
            grouped.append([ln])
    rows = []
    for group in grouped:
        group.sort(key=lambda ln: ln.x)
        rows.append(
            Row(
                group[0].page,
                group[0].y,
                group[0].first_font,
                group[0].x,
                " ".join(clean(ln.text) for ln in group),
            )
        )
    return sorted(rows, key=lambda r: (r.page, int(columns and r.x >= HALF_PAGE), -r.y, r.x))


def properties_text(text: str) -> str:
    """ "Finesse, light, thrown (range 20/60)" as the 5.2.1 prints it: "Finesse, Light, Thrown
    (Range 20/60)"; a dash is none."""
    if text == "—":
        return ""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    out = []
    for part in parts:
        head, bracket, tail = part.partition(" (")
        head = "-".join(w[:1].upper() + w[1:] for w in head.split("-"))
        tail = re.sub(r"^range ", "Range ", tail)
        out.append(f"{head}{bracket}{tail}")
    return ", ".join(out)


def parse_weapons(rows: Sequence[Row]) -> list[Weapon]:
    """Every weapon, from the rows of the Weapons table."""
    weapons: list[Weapon] = []
    seen: set[str] = set()
    category = ""
    for row in rows:
        if row.font.endswith("Bold"):
            continue  # the table's caption and header
        if row.font.endswith("Italic"):
            match = WEAPON_CATEGORY.match(row.text)
            if match is None:
                raise SrdError(f"Weapons table: a heading it can't read: {row.text!r}")
            category = match["category"]
            continue
        match = WEAPON_ROW.match(row.text)
        if match is None or not category:
            raise SrdError(f"Weapons table: a row it can't read (page {row.page}): {row.text!r}")
        name = title_case(match["name"])
        if name in seen:
            raise SrdError(f"Weapons table: {name!r} comes twice")
        seen.add(name)
        kind = (match["type"] or "").capitalize()
        damage = "" if match["damage"] == "—" else match["damage"]
        if bool(damage) != bool(kind):
            raise SrdError(f"Weapons table: {name!r} has damage {damage!r} and type {kind!r}")
        weapons.append(
            Weapon(
                name,
                category,
                damage,
                kind,
                properties_text(match["properties"]),
                "",
                match["weight"],
                money(match["cost"], match["coin"]),
                row.page,
            )
        )
    if not weapons:
        raise SrdError("Weapons table: no weapons found")
    return weapons


def parse_armor(rows: Sequence[Row]) -> list[Armor]:
    """Every armor and the Shield, from the Armor table's rows and the Donning and Doffing
    table's (`rows` run from the first to the second)."""
    armors: list[Armor] = []
    times: dict[str, str] = {}
    seen: set[str] = set()
    category = ""
    donning = False
    for row in rows:
        if row.font.endswith("Bold"):
            donning = donning or row.text.startswith("Donning")
            continue
        if donning:
            match = DONNING_ROW.match(row.text)
            if match is None:
                raise SrdError(f"Donning table: a row it can't read: {row.text!r}")
            times[match["category"].removesuffix(" Armor")] = (
                f"{match['don']} to don and {match['doff']} to doff"
            )
            continue
        if row.font.endswith("Italic"):
            heading = ARMOR_CATEGORY.match(row.text)
            category = heading["category"] if heading else row.text
            if not heading and row.text != "Shield":
                raise SrdError(f"Armor table: a heading it can't read: {row.text!r}")
            continue
        match = ARMOR_ROW.match(row.text)
        if match is None or not category:
            raise SrdError(f"Armor table: a row it can't read (page {row.page}): {row.text!r}")
        name = title_case(match["name"])
        if name in seen:
            raise SrdError(f"Armor table: {name!r} comes twice")
        seen.add(name)
        armors.append(
            Armor(
                name,
                category,
                match["ac"],
                match["strength"],
                match["stealth"],
                match["weight"],
                money(match["cost"], match["coin"]),
                "",
                row.page,
            )
        )
    missing = sorted({a.category for a in armors} - set(times))
    if missing or not armors:
        raise SrdError(f"Armor tables: no armor, or no time to don for {missing}")
    return [
        Armor(
            a.name,
            a.category,
            a.ac,
            a.strength,
            a.stealth,
            a.weight,
            a.cost,
            times[a.category],
            a.page,
        )
        for a in armors
    ]


def lead_in_blocks(
    lines: Sequence[Line], vocab: Vocabulary
) -> tuple[list[tuple[str, str, int]], str]:
    """The lead-in paragraphs of `lines`, each as (name, its words, page), and the words before
    the first lead-in. A paragraph that starts without a lead-in belongs to the one above."""
    starts = [i for i, ln in enumerate(lines) if ln.first_font == LEAD_IN]
    intro = _paragraphs([ln for ln in lines[: starts[0] if starts else len(lines)]], vocab)
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        text = _paragraphs(lines[start:end], vocab)
        name, dot, rest = text.partition(". ")
        if not dot:
            raise SrdError(
                f"A paragraph on page {lines[start].page} has no name before its first stop"
            )
        blocks.append((vocab.repair(name), rest, lines[start].page))
    return blocks, intro


def parse_properties(lines: Sequence[Line], vocab: Vocabulary) -> list[Property]:
    """The weapon properties, from the lines from the Weapon Properties title to the Improvised
    Weapons title. Only the eleven named are entries; stops at a missing or a repeated one."""
    blocks, _ = lead_in_blocks([ln for ln in lines if not is_title(ln)], vocab)
    found: dict[str, Property] = {}
    for name, text, page in blocks:
        if name not in PROPERTIES:
            raise SrdError(f"Weapon properties: {name!r} on page {page} is not one of the eleven")
        if name in found:
            raise SrdError(f"Weapon property {name!r} comes twice (page {page})")
        found[name] = Property(RENAMED.get(name, name), WEAPON_TAG, text, page)
    missing = [n for n in PROPERTIES if n not in found]
    if missing:
        raise SrdError(f"Weapon properties: {missing} were not found")
    return [found[n] for n in PROPERTIES]


def _key(name: str) -> str:
    """The key that matches a table row to its paragraph ("Climber's kit (1 piece)")."""
    return re.sub(r"\s*\([^)]*\)", "", name).strip().lower().replace("'", "’")


@dataclass(slots=True)
class _Entry:
    printed: str  # the table's name: "Acid (vial)"
    cost: str
    weight: str
    page: int
    lines: list[str]  # the rows under a heading (Arcane focus), one line each
    group: bool = False


def _rows_text(entry: _Entry) -> str:
    weight = f", {entry.weight}" if entry.weight != "—" else ""
    return f"{entry.printed}: {entry.cost}{weight}" + ("" if weight.endswith(".") else ".")


def _table(rows: Sequence[Row], pattern: re.Pattern[str], where: str) -> list[_Entry]:
    """The table's entries in order: a row is one; an italic heading is one, with the indented rows
    under it as its lines."""
    entries: list[_Entry] = []
    heading: _Entry | None = None
    for row in rows:
        if row.font.endswith("Bold"):
            continue  # the caption and the header
        if row.font.endswith("Italic"):
            heading = _Entry(title_case(row.text), "Varies", "Varies", row.page, [], True)
            entries.append(heading)
            continue
        match = pattern.match(row.text)
        if match is None:
            raise SrdError(f"{where}: a row it can't read (page {row.page}): {row.text!r}")
        indented = (
            heading is not None and row.x - (LEFT_EDGE if row.x < HALF_PAGE else RIGHT_EDGE) > 5.0
        )
        cost, weight = money(match["cost"], match["coin"]), match["weight"]
        if indented and heading is not None:
            heading.lines.append(
                _rows_text(_Entry(title_case(match["name"]), cost, weight, row.page, []))
            )
            continue
        heading = None
        entries.append(_Entry(match["name"], cost, weight, row.page, []))
    return entries


def _described(
    entries: list[_Entry], blocks: Sequence[tuple[str, str, int]], where: str
) -> list[tuple[str, _Entry, str]]:
    """Each entry with the name it is filed under and its words. A paragraph names its entry
    ("Acid"), or several: "Rope" is the hempen and the silk, which become one entry that lists
    both rows. An entry with no paragraph says its row."""
    wanted: dict[str, tuple[str, str]] = {}
    for name, text, _ in blocks:
        if _key(name) in wanted:
            raise SrdError(f"{where}: the paragraph {name!r} comes twice")
        wanted[_key(name)] = (name, text)
    used: set[str] = set()
    out: list[tuple[str, _Entry, str]] = []
    for entry in entries:
        key = _key(entry.printed)
        if key in wanted:
            used.add(key)
            out.append((wanted[key][0], entry, wanted[key][1]))
            continue
        owner = next((k for k in wanted if key.startswith(f"{k},")), None)
        if owner is None:
            plain = re.sub(r"\s*\([^)]*\)", "", entry.printed)
            out.append((title_case(plain), entry, ""))
        elif owner not in used:
            used.add(owner)
            rows = [e for e in entries if _key(e.printed).startswith(f"{owner},")]
            name, text = wanted[owner]
            if len(rows) == 1:
                out.append((name, rows[0], text))
            else:
                merged = _Entry(name, "Varies", "Varies", entry.page, [_rows_text(e) for e in rows])
                out.append((name, merged, text))
    missing = sorted(set(wanted) - used)
    if missing:
        raise SrdError(f"{where}: paragraphs with no row in the table: {missing}")
    return out


def parse_gear(rows: Sequence[Row], lines: Sequence[Line], vocab: Vocabulary) -> list[Gear]:
    """The adventuring gear: `rows` are the Item Cost Weight table's, `lines` the serif lines of
    the section's paragraphs."""
    blocks, _ = lead_in_blocks([ln for ln in lines if not is_title(ln)], vocab)
    entries = _table(rows, GEAR_ROW, "Adventuring Gear table")
    gear: list[Gear] = []
    seen: set[str] = set()
    for name, entry, words in _described(entries, blocks, "Adventuring Gear"):
        text = "\n".join([words or (_rows_text(entry) if not entry.group else ""), *entry.lines])
        text = text.strip("\n")
        if not text:
            raise SrdError(f"Gear {name!r}: no words")
        if name in seen:
            raise SrdError(f"Gear {name!r} comes twice")
        seen.add(name)
        gear.append(Gear(name, "Adventuring Gear", entry.cost, entry.weight, text, entry.page))
    if not gear:
        raise SrdError("Adventuring Gear: none found")
    return gear


def parse_tools(rows: Sequence[Row], lines: Sequence[Line], vocab: Vocabulary) -> list[Tool]:
    """The tools: `rows` are the Tools table's, `lines` the serif lines of its paragraphs. The
    artisan's tools share one paragraph; a Gaming Set and a Musical Instrument are one tool each,
    with their kinds as its variants (the 5.1 has no ability, Utilize or Craft lines)."""
    blocks, _ = lead_in_blocks([ln for ln in lines if not is_title(ln)], vocab)
    words = {_key(n): (n, t) for n, t, _ in blocks}
    artisan = words.get("artisan’s tools", ("", ""))[1]
    if not artisan:
        raise SrdError("Tools: no paragraph for the artisan's tools")
    tools: list[Tool] = []
    kinds: dict[str, list[str]] = {}  # a heading with variants -> its rows
    slots: dict[str, int] = {}  # ... and where its tool goes
    heading = ""
    for row in rows:
        if row.font.endswith("Bold") or row.text.startswith("Vehicles "):
            continue  # the caption, the header; vehicles are in the section after
        if row.font.endswith("Italic"):
            heading = row.text
            if _key(heading) != "artisan’s tools":
                kinds[heading], slots[heading] = [], len(tools)
                tools.append(Tool("", "", "", "", "", "", "", "", row.page))  # (filled below)
            continue
        match = TOOL_ROW.match(row.text)
        if match is None:
            raise SrdError(f"Tools table: a row it can't read (page {row.page}): {row.text!r}")
        cost, weight = money(match["cost"], match["coin"]), match["weight"]
        name = title_case(match["name"])
        indented = bool(heading) and row.x - RIGHT_EDGE > 5.0
        if indented and _key(heading) == "artisan’s tools":
            own = words.get(_key(match["name"]), ("", ""))[1]  # (the Disguise Kit has its own)
            tools.append(
                Tool(
                    name, "Artisan’s Tools", cost, "", weight, "", "", "", row.page, own or artisan
                )
            )
        elif indented:
            kinds[heading].append(
                _variant(f"{name} {cost}" + (f" {weight}" if weight != "—" else ""))
            )
        else:
            heading = ""
            text = words.get(_key(match["name"]), ("", ""))[1]
            if not text:
                raise SrdError(f"Tool {name!r}: no paragraph")
            tools.append(Tool(name, "Other Tools", cost, "", weight, "", "", "", row.page, text))
    for head, at in slots.items():
        name, text = words.get(_key(head), (title_case(head), ""))
        if not text or not kinds[head]:
            raise SrdError(f"Tool {head!r}: no paragraph or no kinds")
        tools[at] = Tool(
            name,
            "Other Tools",
            "Varies",
            "",
            "Varies",
            "",
            "",
            ", ".join(kinds[head]),
            tools[at].page,
            text,
        )
    names = [t.name for t in tools]
    if not tools or len(names) != len(set(names)):
        raise SrdError("Tools: none found, or a name comes twice")
    return tools


def _variant(line: str) -> str:
    """ "Bagpipes 30 GP 6 lb." is "Bagpipes (30 GP, 6 lb.)", "Dice set 1 SP" "Dice set (1 SP)"."""
    match = re.match(r"^(?P<name>.+?) (?P<cost>[\d,]+ [A-Z]{2})(?: (?P<weight>\S+ lb\.))?$", line)
    if match is None:
        raise SrdError(f"A tool variant it can't read: {line!r}")
    inner = match["cost"] + (f", {match['weight']}" if match["weight"] else "")
    return f"{match['name']} ({inner})"


def parse_mounts(rows: Sequence[Row]) -> list[Gear]:
    """The mounts, the vehicles pulled by an animal and the waterborne vehicles, from the rows of
    the three tables of the Mounts and Vehicles section. The tack and the stabling are left out."""
    gear: list[Gear] = []
    seen: set[str] = set()
    section = ""
    heading = ""
    for row in rows:
        if row.font.endswith("Bold"):
            for caption, name in (
                ("Mounts and Other Animals", "mounts"),
                ("Tack, Harness", "tack"),
                ("Waterborne Vehicles", "ships"),
            ):
                if row.text.startswith(caption):
                    section = name
            continue
        if row.font.endswith("Italic"):
            heading = row.text  # (Saddle: its rows are left out)
            continue
        name = ""
        if section == "mounts":
            match = MOUNT_ROW.match(row.text)
            if match is None:
                raise SrdError(f"Mounts table: a row it can't read: {row.text!r}")
            name = title_case(match["name"])
            entry = Gear(
                name,
                "Mount",
                money(match["cost"], match["coin"]),
                "",
                "",
                row.page,
                (("speed", match["speed"]), ("capacity", match["capacity"])),
            )
        elif section == "tack":
            if (row.x - 57.6 > 5.0 and heading) or row.text.startswith(LEFT_OUT_TACK):
                continue
            match = DRAWN_ROW.match(row.text)
            if match is None or match["name"] not in DRAWN_VEHICLES:
                raise SrdError(f"Tack and drawn vehicles table: a row it can't read: {row.text!r}")
            name = match["name"]
            entry = Gear(
                name, "Vehicle", money(match["cost"], match["coin"]), match["weight"], "", row.page
            )
        elif section == "ships":
            match = SHIP_ROW.match(row.text)
            if match is None:
                raise SrdError(f"Waterborne vehicles table: a row it can't read: {row.text!r}")
            name = title_case(match["name"])
            entry = Gear(
                name,
                "Vehicle",
                money(match["cost"], match["coin"]),
                "",
                "",
                row.page,
                (("speed", match["speed"]),),
            )
        else:
            raise SrdError(f"Mounts and vehicles: a row outside a table: {row.text!r}")
        if name in seen:
            raise SrdError(f"Mounts and vehicles: {name!r} comes twice")
        seen.add(name)
        gear.append(entry)
    if not any(g.group == "Mount" for g in gear) or len(gear) < 3:
        raise SrdError("Mounts and vehicles: a table was not found")
    return gear


def parse_special_weapons(lines: Sequence[Line], vocab: Vocabulary) -> dict[str, str]:
    """The Lance's and the Net's rules, the lead-in paragraphs under the Special Weapons title: the
    Special property points to them, so they are kept with those two weapons."""
    blocks, _ = lead_in_blocks([ln for ln in lines if not is_title(ln)], vocab)
    found = {name: text for name, text, _ in blocks}
    if sorted(found) != ["Lance", "Net"]:
        raise SrdError(f"Special weapons: expected the Lance and the Net, found {sorted(found)}")
    return found
