"""Turning the 2014 SRD 5.1's game master chapters into traps, diseases, poisons and rules (#1944):
Traps, Diseases, Madness, Objects and Poisons (the pages between Spellcasting's spell lists
and Magic Items). Pure: no PDF, no files.

Every title in the semi-bold sans font at a column's edge starts a section. The sample **traps**
have an italic line under their name (`Mechanical trap`, `Magic trap`), the **Pits** section holds
four traps under bold-italic lead-ins, the three **diseases** follow `Sample Diseases`, and each
**poison** is a bold-italic lead-in `Name (Type). Words` under `Sample Poisons`, with its price in
a table above. Every other kept title is a **rule**, and the tables in the sans fonts become lines:
the three madness tables are rules of their own, the two object tables are lines of `Statistics for
Objects`. The tool stops, naming the page, at a title it has no place for, a trap without its kind
line, a poison with no price or a different type in its table, a table it can't read and a name
that comes twice.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.items51 import COLUMN_EDGES, EDGE_SLACK, _paragraphs
from dmbot.devtools.srd.parse import Rule, SrdError, is_title
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line

SECTION = "Gameplay Toolbox"  # (the DM's chapter: `Entry.dm_side`, as the 5.2.1's)
SANS_BOLD = "Calibri-Bold"
ITALIC = "Cambria-Italic"

# The titles that are one rule each, in the order the chapters have them.
RULES = frozenset(
    {
        "Traps",
        "Traps in Play",
        "Triggering a Trap",
        "Detecting and Disabling a Trap",
        "Trap Effects",
        "Complex Traps",
        "Diseases",
        "Madness",
        "Going Mad",
        "Madness Effects",
        "Curing Madness",
        "Objects",
        "Statistics for Objects",
        "Poisons",
    }
)
TRAPS = frozenset(
    {
        "Collapsing Roof",
        "Falling Net",
        "Fire-Breathing Statue",
        "Poison Darts",
        "Poison Needle",
        "Rolling Sphere",
        "Sphere of Annihilation",
    }
)
PITS = "Pits"
PIT_NAMES = ("Simple Pit", "Hidden Pit", "Locking Pit", "Spiked Pit")
DISEASES = frozenset({"Cackle Fever", "Sewer Plague", "Sight Rot"})
POISONS = "Sample Poisons"
# Titles that only introduce what is under them: left out, and ATTRIBUTION.md names them.
LEFT_OUT = {
    "Sample Traps": "only introduces the traps below it",
    "Sample Diseases": "only introduces the three diseases below it",
}
TRAP_KINDS = ("Mechanical trap", "Magic trap")
POISON_TYPES = ("Contact", "Ingested", "Inhaled", "Injury")

MADNESS_TABLES = {
    "Short-Term Madness": "d100 Effect (lasts 1d10 minutes)",
    "Long-Term Madness": "d100 Effect (lasts 1d10 × 10 hours)",
    "Indefinite Madness": "d100 Flaw (lasts until cured)",
}
OBJECT_TABLES = ("Object Armor Class", "Object Hit Points")
TRAP_TABLES = ("Trap Save DCs and Attack Bonuses", "Damage Severity by Level")
POISON_TABLE = "Poisons"
TABLE_HEADERS = {
    "Object Armor Class": "Substance AC",
    "Object Hit Points": "Size Fragile Resilient",
    "Trap Save DCs and Attack Bonuses": "Trap Danger Save DC Attack Bonus",
    "Damage Severity by Level": "Character Level Setback Dangerous Deadly",
    POISON_TABLE: "Item Type Price per Dose",
    **MADNESS_TABLES,
}
MADNESS_ROW = re.compile(r"^(?P<roll>\d{2,3}(?:–\d{2,3})?) (?P<words>.+)$")
AC_ROW = re.compile(r"^(?P<what>.+?) (?P<ac>\d+)$")
HP_ROW = re.compile(r"^(?P<size>.+?) (?P<fragile>\d+ \(\d+d\d+\)) (?P<resilient>\d+ \(\d+d\d+\))$")
DC_ROW = re.compile(
    r"^(?P<danger>Setback|Dangerous|Deadly) (?P<dc>\d+–\d+) (?P<bonus>\+\d+ to \+\d+)$"
)
SEVERITY_ROW = re.compile(
    r"^(?P<level>\d+(?:st|nd|rd|th)–\d+(?:st|nd|rd|th)) "
    r"(?P<setback>\d+d\d+) (?P<dangerous>\d+d\d+) (?P<deadly>\d+d\d+)$"
)
POISON_ROW = re.compile(rf"^(?P<name>.+?) (?P<type>{'|'.join(POISON_TYPES)}) (?P<price>[\d,]+ gp)$")
POISON_PARAGRAPH = re.compile(
    rf"^(?P<name>.+?) \((?P<type>{'|'.join(POISON_TYPES)})\)\. (?P<text>.+)$"
)
PIT_PARAGRAPH = re.compile(rf"^(?P<name>{'|'.join(PIT_NAMES)})\. (?P<text>.+)$")
# The left-out count (lines under the title) of each, which the build checks so a new paragraph
# can't be lost silently.
EXPECTED_LEFT_OUT = {"Sample Traps": 3, "Sample Diseases": 5, "Pits intro": 1}


@dataclass(frozen=True, slots=True)
class Trap:
    name: str
    type: str  # "Mechanical trap" or "Magic trap" (the 5.1 gives no levels, trigger or duration)
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Disease:
    name: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Poison:
    name: str
    type: str
    price: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class GameMaster:
    rules: list[Rule]
    traps: list[Trap]
    diseases: list[Disease]
    poisons: list[Poison]


@dataclass(slots=True)
class _Section:
    name: str
    page: int
    lines: list[Line]


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


def _sections(lines: Sequence[Line]) -> list[_Section]:
    sections: list[_Section] = []
    for line in lines:
        if is_title(line):
            if not _at_an_edge(line):
                raise SrdError(f"Page {line.page}: a title not at a column's edge: {line.text!r}")
            sections.append(_Section(clean(line.text), line.page, []))
        elif not sections:
            raise SrdError(f"Page {line.page}: lines before the first title: {line.text!r}")
        else:
            sections[-1].lines.append(line)
    return sections


def _tables(lines: Sequence[Line]) -> tuple[list[tuple[int, str, list[str]]], list[Line]]:
    """The tables in a section's lines as (where, caption, rows) with the other lines kept. A
    table is a bold sans caption, its header line, then sans rows; a row of the madness tables
    can wrap, and the lines of its wrap come after it in the sans font too. `where` is the index
    in the kept lines that the table was cut from."""
    kept: list[Line] = []
    found: list[tuple[int, str, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.first_font == SANS_BOLD:
            caption = clean(line.text)
            header = TABLE_HEADERS.get(caption)
            if header is None:
                raise SrdError(f"Page {line.page}: a table the tool doesn't know: {caption!r}")
            i += 1
            heads = []
            while i < len(lines) and lines[i].first_font == SANS_BOLD:
                heads.append(clean(lines[i].text))
                i += 1
            if " ".join(heads) != header:
                raise SrdError(f"Table {caption!r} on page {line.page}: its header is {heads!r}")
            rows: list[str] = []
            while i < len(lines) and lines[i].first_font == "Calibri":
                text = clean(lines[i].text)
                if caption in MADNESS_TABLES and not MADNESS_ROW.match(text) and rows:
                    rows[-1] += " " + text  # (the rest of a wrapped row)
                else:
                    rows.append(text)
                i += 1
            if not rows:
                raise SrdError(f"Table {caption!r} on page {line.page}: no rows")
            found.append((len(kept), caption, rows))
            continue
        if line.first_font.startswith("Calibri"):
            raise SrdError(f"Page {line.page}: sans text outside a table: {line.text!r}")
        kept.append(line)
        i += 1
    return found, kept


def _table_lines(caption: str, rows: list[str], page: int) -> list[str]:
    """One text line for each row."""
    out: list[str] = []
    for row in rows:
        if caption in MADNESS_TABLES:
            found = MADNESS_ROW.match(row)
            if found is None:
                raise SrdError(f"{caption!r} on page {page}: can't read the row {row!r}")
            out.append(f"{found['roll']}: {found['words']}")
        elif caption == "Object Armor Class":
            found = AC_ROW.match(row)
            if found is None:
                raise SrdError(f"{caption!r} on page {page}: can't read the row {row!r}")
            out.append(f"{found['what']}: AC {found['ac']}")
        elif caption == "Object Hit Points":
            hp = HP_ROW.match(row)
            if hp is None:
                raise SrdError(f"{caption!r} on page {page}: can't read the row {row!r}")
            out.append(f"{hp['size']}: fragile {hp['fragile']}, resilient {hp['resilient']}")
        elif caption == "Trap Save DCs and Attack Bonuses":
            dc = DC_ROW.match(row)
            if dc is None:
                raise SrdError(f"{caption!r} on page {page}: can't read the row {row!r}")
            out.append(f"{dc['danger']}: save DC {dc['dc']}, attack bonus {dc['bonus']}")
        elif caption == "Damage Severity by Level":
            sev = SEVERITY_ROW.match(row)
            if sev is None:
                raise SrdError(f"{caption!r} on page {page}: can't read the row {row!r}")
            out.append(
                f"Level {sev['level']}: setback {sev['setback']}, "
                f"dangerous {sev['dangerous']}, deadly {sev['deadly']}"
            )
        else:
            raise SrdError(f"A table the tool can't write as lines: {caption!r}")
    return out


def _with_tables(section: _Section, vocab: Vocabulary, only: Sequence[str]) -> str:
    """The words of a section with the tables named in `only` put in where they are printed."""
    tables, kept = _tables(section.lines)
    out: list[str] = []
    start = 0
    for where, caption, rows in tables:
        if caption not in only:
            continue
        before = _paragraphs(kept[start:where], vocab)
        if before:
            out.append(before)
        out.append(MADNESS_TABLES.get(caption, caption))
        out += _table_lines(caption, rows, section.page)
        start = where
    rest = _paragraphs(kept[start:], vocab)
    if rest:
        out.append(rest)
    return "\n".join(out)


def parse_gm(lines: Sequence[Line], vocab: Vocabulary) -> GameMaster:
    """Everything kept from the game master chapters' lines (from the Traps title up to the Magic
    Items title, not including it)."""
    sections = _sections(lines)
    rules: list[Rule] = []
    traps: list[Trap] = []
    diseases: list[Disease] = []
    poisons: list[Poison] = []
    seen: dict[str, set[str]] = {k: set() for k in ("rule", "trap", "disease", "poison")}

    def once(kind: str, name: str, page: int) -> None:
        if name in seen[kind]:
            raise SrdError(f"The {kind} {name!r} comes twice (page {page})")
        seen[kind].add(name)

    for section in sections:
        name, page = section.name, section.page
        if name in LEFT_OUT:
            continue
        if name in RULES:
            tables, _ = _tables(section.lines)
            if name == "Poisons":
                # the table's rows are read with the poisons below, not put in the text
                text = _with_tables(section, vocab, only=())
            elif name == "Statistics for Objects":
                text = _with_tables(section, vocab, only=OBJECT_TABLES)
            elif name == "Trap Effects":
                text = _with_tables(section, vocab, only=TRAP_TABLES)
            else:
                text = _with_tables(section, vocab, only=())
            if not text:
                raise SrdError(f"Rule {name!r} on page {page}: no description")
            once("rule", name, page)
            rules.append(Rule(name, "", _joined(text), page, ()))
            if name == "Madness Effects":
                for _, caption, rows in tables:
                    if caption in MADNESS_TABLES:
                        words = _joined(
                            "\n".join([MADNESS_TABLES[caption], *_table_lines(caption, rows, page)])
                        )
                        once("rule", caption, page)
                        rules.append(Rule(caption, "", words, page, ()))
            continue
        if name in TRAPS:
            kind, rest = _kind_line(section)
            once("trap", name, page)
            traps.append(Trap(name, kind, _joined(_paragraphs(rest, vocab)), page))
            continue
        if name == PITS:
            kind, rest = _kind_line(section)
            for pit in _pits(rest, vocab, page):
                once("trap", pit[0], page)
                traps.append(Trap(pit[0], kind, pit[1], page))
            continue
        if name in DISEASES:
            once("disease", name, page)
            diseases.append(Disease(name, _joined(_paragraphs(section.lines, vocab)), page))
            continue
        if name == POISONS:
            poisons.extend(_poisons(section, sections, vocab))
            continue
        raise SrdError(f"Page {page}: a title the tool has no place for: {name!r}")
    # the madness tables sit in the sections above, found by `rules` there; check all three came
    for caption in MADNESS_TABLES:
        if caption not in seen["rule"]:
            raise SrdError(f"The table {caption!r} was not found")
    return GameMaster(rules, traps, diseases, poisons)


def _joined(text: str) -> str:
    text = re.sub(r"(?<=[a-z]) - (?=[a-z])", "-", text)  # ("self -" at a line's end, "destructive")
    text = re.sub(r"\n(?=[a-z])", " ", text)
    text = re.sub(r"− (?=\d)", "−", text)
    return re.sub(r"— (?=\w)", "—", text)


def _kind_line(section: _Section) -> tuple[str, list[Line]]:
    """The trap's kind line (italic, first) and the lines after it."""
    lines = section.lines
    if not lines or lines[0].first_font != ITALIC or clean(lines[0].text) not in TRAP_KINDS:
        found = clean(lines[0].text) if lines else ""
        raise SrdError(
            f"Trap {section.name!r} on page {section.page}: no kind line, found {found!r}"
        )
    return clean(lines[0].text), lines[1:]


def _pits(lines: Sequence[Line], vocab: Vocabulary, page: int) -> list[tuple[str, str]]:
    """The four pit traps of the Pits section, each under its bold-italic lead-in. The sentence
    that says there are four comes before the first and is left out."""
    rows = _paragraphs(lines, vocab).split("\n")
    pits: list[tuple[str, list[str]]] = []
    for row in rows:
        found = PIT_PARAGRAPH.match(row)
        if found:
            pits.append((found["name"], [found["text"]]))
        elif pits:
            pits[-1][1].append(row)
        elif row != "Four basic pit traps are presented here.":
            raise SrdError(f"Pits on page {page}: words before the first pit: {row[:40]!r}")
    if [name for name, _ in pits] != list(PIT_NAMES):
        raise SrdError(f"Pits on page {page}: found {[n for n, _ in pits]}, not {list(PIT_NAMES)}")
    return [(name, _joined("\n".join(text))) for name, text in pits]


def _poisons(section: _Section, sections: Sequence[_Section], vocab: Vocabulary) -> list[Poison]:
    """The poisons: their words from the lead-ins under Sample Poisons, their price and type from
    the table above (in the Poisons section), which must agree."""
    prices: dict[str, tuple[str, str]] = {}
    poisons_section = next((s for s in sections if s.name == "Poisons"), None)
    if poisons_section is None:
        raise SrdError("No Poisons section for the price table")
    tables, _ = _tables(poisons_section.lines)
    for _, caption, rows in tables:
        if caption != POISON_TABLE:
            continue
        for row in rows:
            found = POISON_ROW.match(row)
            if found is None:
                raise SrdError(f"The Poisons table on page {poisons_section.page}: row {row!r}")
            prices[found["name"].lower()] = (found["type"], found["price"].replace(" gp", " GP"))
    if _tables(section.lines)[0]:
        raise SrdError(f"Sample Poisons on page {section.page}: a table in the poisons' words")
    out: list[Poison] = []
    seen: set[str] = set()
    for row in _paragraphs(section.lines, vocab).split("\n"):
        found = POISON_PARAGRAPH.match(row)
        if found is None:
            if out or not row.startswith("Each type of poison"):
                raise SrdError(f"Sample Poisons on page {section.page}: can't read {row[:40]!r}")
            continue
        name = found["name"]
        priced = prices.get(name.lower())
        if priced is None:
            raise SrdError(f"Poison {name!r} on page {section.page} has no price in the table")
        if priced[0] != found["type"]:
            raise SrdError(
                f"Poison {name!r}: the table says {priced[0]}, its words {found['type']}"
            )
        if name in seen:
            raise SrdError(f"The poison {name!r} comes twice (page {section.page})")
        seen.add(name)
        out.append(Poison(name, found["type"], priced[1], _joined(found["text"]), section.page))
    if len(out) != len(prices):
        raise SrdError(f"{len(out)} poisons with words, {len(prices)} in the table")
    return out


def left_out(lines: Sequence[Line]) -> dict[str, int]:
    """What the tool leaves out, with how many lines: the list ATTRIBUTION.md names and the
    build checks."""
    found: dict[str, int] = {}
    for section in _sections(lines):
        if section.name in LEFT_OUT:
            found[section.name] = len(section.lines)
        if section.name == PITS:
            found["Pits intro"] = 1  # "Four basic pit traps are presented here." (checked above)
    missing = sorted(set(LEFT_OUT) - set(found))
    if missing:
        raise SrdError(f"Left-out titles that are not in the chapters: {', '.join(missing)}")
    return found
