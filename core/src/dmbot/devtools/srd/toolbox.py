"""Turning the SRD 5.2.1's Gameplay Toolbox chapter into rules, poisons, traps and curses (#1935),
and its Combat Encounters section into rules (#2000). Pure: no PDF, no files.

The chapter is a run of titles in the semi-bold sans font at a column's edge, each with serif
paragraphs under it. A **poison** is a title that ends in its price (`Purple Worm Poison (2,000
GP)`) with its type in italics under it; a **trap** has a line in italics saying `Deadly Trap
(Levels 1-4)` and then bold `Trigger:` and `Duration:` lines; a **contagion** has an italic `Magical
Contagion` line; a **curse** is the one bold-italic lead-in under Environmental Curses (Demonic
Possession). Every other title that is kept is a **rule**. The tables set in the sans fonts are
turned into one line per row (the travel terrain, the sample fear and mental stress DCs, each trap's
levels), because their cells do not read as sentences when run together.

The Combat Encounters section (how hard a fight is, the XP budget, choosing monsters and
troubleshooting) is rules too, tagged `Combat Encounters`; its one table, the XP Budget per
Character, becomes one line per level. The Magic Items section after it is left out (a later issue).

The tool stops, naming the page, at a title it has no place for, at a poison without its type, at a
trap without its trigger, duration or effect, at a table it can't read and at a name that comes
twice in one kind, instead of losing something. The sections left out (advice and random tables)
are `LEFT_OUT`, and ATTRIBUTION.md names them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.devtools.srd.parse import (
    BODY_FAMILY,
    COLUMN_EDGES,
    EDGE_SLACK,
    ITALIC_FONT,
    Mender,
    Rule,
    SrdError,
    _clean,
    _paragraphs,
    is_title,
)
from dmbot.devtools.srd.pdf import COLUMN_SPLIT, Line

CHAPTER = "Gameplay Toolbox"
# Where the kept sections end: the magic item rules (categories, values, crafting, sentient items)
# come after the Combat Encounters section, and are left out for now.
AFTER = "Magic Items"
ENCOUNTERS = "Combat Encounters"
ENCOUNTER_TAG = "Combat Encounters"  # the tag of every rule from that section
# A title that ends on one of these carries on in the line below it ("Curses and" / "Magical
# Contagions"). The titles above and below "Environmental Effects" / "Deep Water" are only 18
# points apart too, so the gap alone can't tell the two apart.
CARRIES_ON = ("and", "or", "of", "the", "a", "to", "in")
SAME_HEADING = 20.0
PARAGRAPH_GAP = 15.0  # lines in a paragraph are 12 points apart; a paragraph break is 18

# The titles that become a rule of their own, in the order the chapter has them. Any other title
# the tool meets is a new section it must be told about (stop).
RULES = frozenset(
    {
        "Travel Pace",
        "Good Roads",
        "Slower Travelers",
        "Extended Travel",
        "Special Movement",
        "Travel Terrain",
        "Vehicles",
        "Curses and Magical Contagions",
        "Bestow Curse",
        "Cursed Creatures",
        "Cursed Magic Items",
        "Narrative Curses",
        "Environmental Curses",
        "Magical Contagions",
        "Rest and Recuperation",
        "Deep Water",
        "Extreme Cold",
        "Extreme Heat",
        "Frigid Water",
        "Heavy Precipitation",
        "High Altitude",
        "Slippery Ice",
        "Strong Wind",
        "Thin Ice",
        "Fear and Mental Stress",
        "Fear Effects",
        "Mental Stress Effects",
        "Prolonged Effects",
        "Poison",
        "Purchasing Poison",
        "Harvesting Poison",
        "Traps",
        "Parts of a Trap",
    }
)
# The Combat Encounters section's titles, in the order it has them (#2000). They are rules with the
# section's name as their tag.
ENCOUNTER_RULES = frozenset(
    {
        "Combat Encounters",
        "Combat Encounter Difficulty",
        "Step 1: Choose a Difficulty",
        "Step 2: Determine Your XP Budget",
        "Step 3: Spend Your Budget",
        "Troubleshooting",
        "Many Creatures",
        "Adjustments",
        "CR 0 Creatures",
        "Number of Stat Blocks",
        "Powerful Creatures",
        "Unusual Features",
    }
)
# What is left out and why: advice to the DM who builds the game, and headings with no rule under
# them. The key is the title as printed; the sections under "Creating a Background" are its steps.
LEFT_OUT = {
    "Creating a Background": "advice on building a background",
    "1: Choose Abilities": "a step of building a background",
    "2: Choose a Feat": "a step of building a background",
    "3: Choose Skill Proficiencies": "a step of building a background",
    "4: Choose a Tool Proficiency": "a step of building a background",
    "5: Choose Equipment": "a step of building a background",
    "Curses": "only points to the forms below it",
    "Example Contagions": "only introduces the three below it",
    "Environmental Effects": "a heading with the effects under it",
    "Sample Poisons": "only introduces the poisons below it",
    "Example Traps": "a heading with the traps under it",
}
# How many lines of the real chapter each left-out title has: a new paragraph under one of them
# would be lost silently, so the build stops when this changes (ATTRIBUTION.md names the titles).
EXPECTED_LEFT_OUT = {
    "Creating a Background": 10,
    "1: Choose Abilities": 11,
    "2: Choose a Feat": 2,
    "3: Choose Skill Proficiencies": 4,
    "4: Choose a Tool Proficiency": 2,
    "5: Choose Equipment": 4,
    "Curses": 2,
    "Example Contagions": 4,
    "Environmental Effects": 0,
    "Sample Poisons": 5,
    "Example Traps": 1,
}
# A table's caption: its rows are put in the text of the entry above it.
FOLDED = frozenset({"Sample Fear DCs", "Sample Mental Stress Effects", "XP Budget per Character"})
POISON_TITLE = re.compile(r"^(?P<name>.+) \((?P<price>\d[\d,]* GP)\)$")
POISON_TYPE = re.compile(r"^(?P<type>Contact|Ingested|Inhaled|Injury) Poison$")
_BAND = r"(?:Deadly|Nuisance) Trap \(Levels \d+–\d+\)"
TRAP_TYPE = re.compile(rf"^{_BAND}(?: or {_BAND})?$")
CONTAGION_TYPE = "Magical Contagion"
CURSE_LEAD_IN = "Demonic Possession."
CURSE_SECTION = "Environmental Curses"
TERRAIN_SECTION = "Travel Terrain"
MOVEMENT_SECTION = "Special Movement"

TERRAIN_HEADER = "Terrain Maximum Pace Encounter Distance Foraging DC Navigation DC Search DC"
TERRAIN_ROW = re.compile(
    r"^(?P<terrain>[A-Z][a-z]+) (?P<pace>Fast|Normal|Slow|Special)(?P<mark>[*†]?) "
    r"(?P<distance>\d+d\d+ × 10 feet) (?P<foraging>\d+) (?P<navigation>\d+) (?P<search>\d+)$"
)
FEAR_HEADER = "Example Save DC"
FEAR_ROW = re.compile(r"^(?P<words>.*\S) (?P<dc>\d+)$")
STRESS_HEADERS = ("Save Psychic", "Example DC Damage")
XP_ROW = re.compile(r"^(?P<level>\d{1,2}) (?P<low>[\d,]+) (?P<moderate>[\d,]+) (?P<high>[\d,]+)$")
XP_LEVELS = 20
STRESS_ROW = re.compile(r"^(?P<words>.*\S) (?P<dc>\d+) (?P<damage>\d+d\d+)$")
# The five tables that scale a trap, by their caption: the words of each column and what its
# cells look like. A new caption is a table the tool doesn't know (stop).
_DAMAGE = r"\d+ \(\d+d\d+\)"
LEVELS_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "Levels Bludgeoning Damage Save DC": (("Bludgeoning Damage", _DAMAGE), ("Save DC", r"\d+")),
    "Levels Fire Damage Area of Effect": (
        ("Fire Damage", _DAMAGE),
        ("Area of Effect", r"\d+-foot Cone"),
    ),
    "Levels Pit Depth Bludgeoning Damage": (
        ("Pit Depth", r"\d+ feet"),
        ("Bludgeoning Damage", _DAMAGE),
    ),
    "Levels Poison Damage Save DC": (("Poison Damage", _DAMAGE), ("Save DC", r"\d+")),
    "Levels Pit Depth Damage": (
        ("Pit Depth", r"\d+ feet"),
        ("Damage", rf"{_DAMAGE} Bludgeoning plus {_DAMAGE} Piercing"),
    ),
}
LEVELS_START = re.compile(r"^\d+–\d+ ")


@dataclass(frozen=True, slots=True)
class Poison:
    name: str
    type: str  # "Contact", "Ingested", "Inhaled" or "Injury"
    price: str  # "150 GP" (for one dose)
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Trap:
    name: str
    severity: str  # as printed, with its levels: "Deadly Trap (Levels 1 to 4)"
    trigger: str
    duration: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Curse:
    name: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Toolbox:
    rules: list[Rule]
    poisons: list[Poison]
    traps: list[Trap]
    curses: list[Curse]


@dataclass(slots=True)
class _Section:
    name: str
    page: int
    lines: list[Line] = field(default_factory=list)


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


def _sections(lines: Sequence[Line]) -> list[_Section]:
    """The chapter split at its titles (those at a column's edge; a table's column heads sit in)."""
    if not lines or _clean(lines[0].text) != CHAPTER or not is_title(lines[0]):
        raise SrdError(f"The Gameplay Toolbox's lines don't start with {CHAPTER!r}")
    sections: list[_Section] = []
    i = 1
    while i < len(lines):
        line = lines[i]
        if not (is_title(line) and _at_an_edge(line)):
            if not sections:
                raise SrdError(f"Page {line.page}: lines before the first title: {line.text!r}")
            sections[-1].lines.append(line)
            i += 1
            continue
        name = _clean(line.text)
        j = i + 1
        while (
            j < len(lines)
            and name.split()[-1] in CARRIES_ON
            and is_title(lines[j])
            and abs(lines[j].x - line.x) <= EDGE_SLACK
            and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
        ):
            name += " " + _clean(lines[j].text)
            j += 1
        sections.append(_Section(name, line.page))
        i = j
    return sections


def _fold(sections: list[_Section]) -> list[_Section]:
    """A table's caption joins the section above it; the lines under Travel Terrain that are
    serif (the pace formulas and the paragraph after them, printed in the next column) belong to
    Special Movement, which they finish."""
    kept: list[_Section] = []
    for section in sections:
        if section.name in FOLDED:
            if not kept:
                raise SrdError(f"{section.name!r} on page {section.page} has nothing above it")
            kept[-1].lines.extend(section.lines)
        else:
            kept.append(section)
    by_name = {s.name: s for s in kept}
    terrain, movement = by_name.get(TERRAIN_SECTION), by_name.get(MOVEMENT_SECTION)
    if terrain is None or movement is None:
        raise SrdError(f"No {TERRAIN_SECTION!r} and {MOVEMENT_SECTION!r} sections")
    serif = [ln for ln in terrain.lines if ln.first_font.startswith(BODY_FAMILY)]
    terrain.lines = [ln for ln in terrain.lines if ln not in serif]
    movement.lines.extend(serif)
    return kept


def _bulleted(lines: Sequence[Line], mender: Mender) -> str:
    """Paragraphs of serif lines. A bullet is set in the serif font here (the glossary's are
    sans), and so are a trap's bold `Trigger:` and `Duration:` lines, which end without a full
    stop: none of them would start a paragraph of its own, so each starts its own group. A gap
    wider than a line's own starts one too: not every paragraph here is set in (a trap's first
    one, the one after a list)."""
    groups: list[list[Line]] = []
    for line in lines:
        previous = groups[-1][-1] if groups else None
        gap = previous.y - line.y if previous is not None and previous.page == line.page else 0.0
        starts = line.text.lstrip().startswith(("•", "Trigger:", "Duration:"))
        if not groups or starts or gap > PARAGRAPH_GAP:
            groups.append([])
        groups[-1].append(line)
    return "\n".join(p for g in groups if (p := _paragraphs(g, mender)))


def _formulas(lines: Sequence[Line]) -> tuple[list[str], list[Line]]:
    """The four pace formulas of Special Movement, printed as a boxed block (the labels in bold,
    an "=" on a line of its own, the right-hand sides beside) and the other lines, in order."""
    rest: list[Line] = []
    block: list[Line] = []
    for line in lines:
        edge = min(COLUMN_EDGES, key=lambda e: abs(e - line.x))
        (block if line.x - edge > 10 else rest).append(line)
    clusters: list[list[Line]] = []
    for line in sorted(block, key=lambda ln: -ln.y):
        if clusters and clusters[-1][-1].y - line.y <= 2:
            clusters[-1].append(line)
        else:
            clusters.append([line])
    rows: list[str] = []
    for cluster in clusters:
        pieces = sorted((p for ln in cluster for p in ln.pieces), key=lambda p: p.x)
        words = _clean(" ".join(p.text for p in pieces))
        if "=" in words:
            if not words.replace("=", "").strip():
                raise SrdError(
                    f"Special Movement on page {cluster[0].page}: "
                    "a formula with nothing on either side"
                )
            rows.append(words)
        elif rows:  # the line that finishes the row above ("... hours traveled")
            rows[-1] += " " + words
        else:
            raise SrdError(f"Special Movement on page {cluster[0].page}: a formula with no '='")
    if len(rows) != 4:
        raise SrdError(f"Special Movement: {len(rows)} formulas, not the four it has")
    return rows, rest


def _table(lines: Sequence[Line], start: int, mender: Mender) -> tuple[list[str], int]:
    """The rows of the table whose caption or column heads start at `lines[start]`, one text
    line each, and the index after the table."""
    j = start
    while j < len(lines) and is_title(lines[j]):
        j += 1
    head = _clean(" ".join(ln.text for ln in lines[start:j]))
    k = j
    while k < len(lines) and not lines[k].first_font.startswith(BODY_FAMILY):
        k += 1
    cells = [_clean(ln.text) for ln in lines[j:k]]
    page = lines[start].page
    if head == TERRAIN_HEADER:
        return _terrain(cells, page), k
    if head == FEAR_HEADER:
        return _fear(cells, page, mender), k
    if head == " ".join(STRESS_HEADERS):
        return _stress(cells, page, mender), k
    if head in LEVELS_TABLES:
        return _levels(head, cells, page), k
    if "Encounter Difficulty" in head and "Level" in head:
        return _xp_budget(cells, page), k
    raise SrdError(f"A table on page {page} that the tool doesn't know: {head!r}")


def _terrain(cells: list[str], page: int) -> list[str]:
    """The 11 terrains, one line each, then the table's two notes. The Waterborne row's dagger
    is printed on a line of its own just above it; the note's dagger just above its words."""
    rows: list[str] = []
    notes: list[str] = []
    dagger = False
    for n, cell in enumerate(cells):
        found = TERRAIN_ROW.match(cell)
        if found:
            mark = found["mark"] or ("†" if dagger else "")
            dagger = False
            rows.append(
                f"{found['terrain']}: {found['pace']}{mark} Maximum Pace; "
                f"Encounter Distance {found['distance']}; Foraging DC {found['foraging']}; "
                f"Navigation DC {found['navigation']}; Search DC {found['search']}"
            )
        elif cell == "†":
            ahead = cells[n + 1] if n + 1 < len(cells) else ""
            if TERRAIN_ROW.match(ahead):
                dagger = True
            else:
                notes.append(cell)
        elif cell.startswith("*") or (notes and notes[-1] == "†"):
            if notes and notes[-1] == "†":
                notes[-1] = f"†{cell}"
            else:
                notes.append(cell)
        else:
            raise SrdError(f"Travel Terrain on page {page}: can't read the row {cell!r}")
    if len(rows) != 11 or len(notes) != 2 or "†" in notes:
        raise SrdError(
            f"Travel Terrain on page {page}: {len(rows)} terrains and {len(notes)} notes, "
            "not the 11 and 2 it has"
        )
    return [*rows, *notes]


def _xp_budget(cells: list[str], page: int) -> list[str]:
    """The XP Budget per Character table, one line a level: "Level 3: Low 150, Moderate 225, High
    400 XP per character" (#2000). The column heads ("Low Moderate High") are skipped; any other
    cell that is not a row, or a missing level, stops the build."""
    rows: list[str] = []
    for cell in cells:
        if cell == "Low Moderate High":
            continue
        found = XP_ROW.match(cell)
        if found is None or int(found["level"]) != len(rows) + 1:
            raise SrdError(f"XP Budget per Character on page {page}: can't read the row {cell!r}")
        rows.append(
            f"Level {found['level']}: Low {found['low']}, Moderate {found['moderate']}, "
            f"High {found['high']} XP per character"
        )
    if len(rows) != XP_LEVELS:
        raise SrdError(
            f"XP Budget per Character on page {page}: {len(rows)} levels, "
            f"not the {XP_LEVELS} it has"
        )
    return rows


def _rows_by_dc(
    cells: list[str], page: int, mender: Mender, pattern: re.Pattern[str], label: str
) -> list[tuple[str, re.Match[str]]]:
    """The rows of a table whose cells wrap: the line that ends in the numbers starts a row, and
    the lines under it are the rest of its first cell."""
    rows: list[tuple[str, re.Match[str]]] = []
    for cell in cells:
        found = pattern.match(cell)
        if found:
            rows.append((found["words"], found))
        elif rows:
            words, match = rows[-1]
            joined = mender.join(words, cell) if words.endswith("-") else f"{words} {cell}"
            rows[-1] = (joined, match)
        else:
            raise SrdError(f"{label} on page {page}: a line before the first row: {cell!r}")
    if not rows:
        raise SrdError(f"{label} on page {page}: no rows")
    return rows


def _fear(cells: list[str], page: int, mender: Mender) -> list[str]:
    rows = _rows_by_dc(cells, page, mender, FEAR_ROW, "Sample Fear DCs")
    return [f"DC {m['dc']}: {words}" for words, m in rows]


def _stress(cells: list[str], page: int, mender: Mender) -> list[str]:
    rows = _rows_by_dc(cells, page, mender, STRESS_ROW, "Sample Mental Stress Effects")
    return [f"DC {m['dc']}, {m['damage']} Psychic damage: {words}" for words, m in rows]


def _levels(head: str, cells: list[str], page: int) -> list[str]:
    """A trap's table of levels: "Levels 5-10: Bludgeoning Damage 22 (4d10); Save DC 15". A row
    that wraps (the Spiked Pit's damage) has the rest of its last cell on the next line."""
    columns = LEVELS_TABLES[head]
    joined: list[str] = []
    for cell in cells:
        if LEVELS_START.match(cell):
            joined.append(cell)
        elif joined:
            joined[-1] += " " + cell
        else:
            raise SrdError(f"{head!r} on page {page}: a line before the first row: {cell!r}")
    pattern = re.compile(
        r"^(?P<levels>\d+–\d+) " + " ".join(f"({cell})" for _, cell in columns) + "$"
    )
    out: list[str] = []
    for row in joined:
        found = pattern.match(row)
        if found is None:
            raise SrdError(f"{head!r} on page {page}: can't read the row {row!r}")
        values = found.groups()[1:]
        cols = "; ".join(
            f"{label} {value}" for (label, _), value in zip(columns, values, strict=True)
        )
        out.append(f"Levels {found['levels']}: {cols}")
    if not out:
        raise SrdError(f"{head!r} on page {page}: no rows")
    return out


def _body(lines: Sequence[Line], mender: Mender) -> str:
    """The words of a section's lines: its serif paragraphs, with each table put in as lines."""
    parts: list[str] = []
    serif: list[Line] = []

    def flush() -> None:
        if serif:
            text = _bulleted(serif, mender)
            if text:
                parts.append(text)
            serif.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.first_font.startswith(BODY_FAMILY):
            serif.append(line)
            i += 1
        elif is_title(line):
            flush()
            rows, i = _table(lines, i, mender)
            parts.extend(rows)
        else:
            raise SrdError(
                f"Page {line.page}: a line in {line.first_font} the tool can't place: {line.text!r}"
            )
    flush()
    return "\n".join(parts)


def _italic_head(lines: Sequence[Line]) -> tuple[str, list[Line]]:
    """The italic line(s) that open a poison, trap or contagion (a trap's can wrap) and the lines
    after them."""
    n = 0
    while n < len(lines) and lines[n].first_font.endswith(ITALIC_FONT):
        n += 1
        text = _clean(" ".join(ln.text for ln in lines[:n]))
        if text.count("(") == text.count(")"):
            return text, list(lines[n:])
    return _clean(" ".join(ln.text for ln in lines[:n])), list(lines[n:])


def _split_off(text: str, prefix: str, what: str, page: int, name: str) -> tuple[str, str]:
    """(the value, the rest) of the first paragraph of `text`, which must start with `prefix`."""
    first, _, rest = text.partition("\n")
    if not first.startswith(prefix):
        raise SrdError(f"Trap {name!r} on page {page}: no {what} line: {first[:40]!r}")
    return first.removeprefix(prefix).strip(), rest


def parse_toolbox(lines: Sequence[Line]) -> Toolbox:
    """Everything kept from the Gameplay Toolbox's lines (from its title up to the Combat
    Encounters heading, not including it)."""
    sections = _fold(_sections(lines))
    mender = Mender(ln for s in sections for ln in s.lines if ln.first_font.startswith("Cambria"))
    toolbox = Toolbox([], [], [], [])
    seen: dict[str, set[str]] = {"rule": set(), "poison": set(), "trap": set(), "curse": set()}

    def once(kind: str, name: str, page: int) -> None:
        if name in seen[kind]:
            raise SrdError(f"The {kind} {name!r} comes twice (page {page})")
        seen[kind].add(name)

    for section in sections:
        name, page = section.name, section.page
        if name in LEFT_OUT:
            continue
        poison = POISON_TITLE.match(name)
        if poison:
            head, body = _italic_head(section.lines)
            kind = POISON_TYPE.match(head)
            if kind is None:
                raise SrdError(f"Poison {name!r} on page {page}: no type line, found {head!r}")
            text = _body(body, mender)
            if not text:
                raise SrdError(f"Poison {name!r} on page {page}: no description")
            once("poison", poison["name"], page)
            toolbox.poisons.append(
                Poison(poison["name"], kind["type"], poison["price"], text, page)
            )
            continue
        head, body = _italic_head(section.lines)
        if TRAP_TYPE.match(head):
            text = _body(body, mender)
            trigger, text = _split_off(text, "Trigger:", "Trigger", page, name)
            duration, text = _split_off(text, "Duration:", "Duration", page, name)
            if not text:
                raise SrdError(f"Trap {name!r} on page {page}: no effect")
            once("trap", name, page)
            toolbox.traps.append(Trap(name, head, trigger, duration, text, page))
            continue
        if head == CONTAGION_TYPE:
            text = _body(body, mender)
            if not text:
                raise SrdError(f"Contagion {name!r} on page {page}: no description")
            once("rule", name, page)
            toolbox.rules.append(Rule(name, CONTAGION_TYPE, text, page, ()))
            continue
        if head:
            raise SrdError(
                f"{name!r} on page {page}: an italic line the tool can't place: {head!r}"
            )
        encounter = name in ENCOUNTER_RULES
        if name not in RULES and not encounter:
            raise SrdError(f"Page {page}: a title the tool has no place for: {name!r}")
        body = list(section.lines)
        if name == MOVEMENT_SECTION:
            formulas, body = _formulas(body)
            # The sentence that promises the formulas ends the left column; the paragraph after
            # them (flying and special movement) starts the right one.
            before = _body([ln for ln in body if ln.x < COLUMN_SPLIT], mender)
            after = _body([ln for ln in body if ln.x >= COLUMN_SPLIT], mender)
            text = "\n".join([before, *formulas, after])
        elif name == CURSE_SECTION:
            text, curse = _environmental_curse(body, mender, page)
            once("curse", "Demonic Possession", page)
            toolbox.curses.append(curse)
        else:
            text = _body(body, mender)
        if not text:
            raise SrdError(f"Rule {name!r} on page {page}: no description")
        once("rule", name, page)
        toolbox.rules.append(Rule(name, ENCOUNTER_TAG if encounter else "", text, page, ()))
    return toolbox


def _environmental_curse(body: list[Line], mender: Mender, page: int) -> tuple[str, Curse]:
    """The words of Environmental Curses up to the one curse it describes, and that curse."""
    at = next((i for i, ln in enumerate(body) if _clean(ln.text).startswith(CURSE_LEAD_IN)), None)
    if at is None:
        raise SrdError(f"Environmental Curses on page {page}: no {CURSE_LEAD_IN!r}")
    text = _body(body[:at], mender)
    words = _body(body[at:], mender)
    name = CURSE_LEAD_IN.removesuffix(".")
    # The lead-in is the curse's name: it is the entry's name, so the text starts at the sentence.
    words = words.removeprefix(CURSE_LEAD_IN).strip()
    return text, Curse(name, words, body[at].page)


def left_out(lines: Sequence[Line]) -> dict[str, int]:
    """Each title the tool leaves out, with the lines under it: the list ATTRIBUTION.md names,
    and a test pins, so a new drop can't pass unnoticed."""
    found = {s.name: len(s.lines) for s in _sections(lines) if s.name in LEFT_OUT}
    missing = sorted(set(LEFT_OUT) - set(found))
    if missing:
        raise SrdError(f"Left-out titles that are not in the chapter: {', '.join(missing)}")
    return found
