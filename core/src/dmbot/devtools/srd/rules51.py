"""Turning the 2014 SRD 5.1's core rules chapters into rules (#1943): Using Ability Scores,
Adventuring, Combat and Spellcasting (up to the spell lists). Pure: no PDF, no files.

Every title in the semi-bold sans font at a column's edge is a rule of its own, with the words under
it up to the next title. All the titles are set alike (the PDF has no heading levels), so a title
that comes twice (`Hit Points` in Constitution and in Combat) is told apart by its place in the
chapter: `DUPLICATES` names each one's entry. The tables in a section (set in the Calibri fonts)
are left out because their cells do not read as sentences; the bullets (Symbol font) are kept.
Where the 5.1's rule is the 2024 glossary's (Dash, Cover, Long Rest ...) the entry has the
glossary's name, so the index pairs the two and a 2024 campaign never sees the 2014 one.
When something doesn't look as expected the tool stops and says where, instead of losing a rule.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from dmbot.devtools.srd.items51 import COLUMN_EDGES, EDGE_SLACK, _paragraphs
from dmbot.devtools.srd.parse import Rule, SrdError, is_title
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line, Piece

SECTION = "Rules"
# A title that ends on one of these carries on in the line below ("Advantage and" / "Disadvantage",
# "Using Ability" / "Scores", the chapter's own title).
CARRIES_ON = ("Using Ability", "Advantage and", "Damage Resistance and")
MINUS_ABOVE = 14.0  # a minus sign this far (points) above its line belongs in it
SAME_HEADING = 35.0  # title lines this close (points) with nothing between are one heading
# The four tables, by their captions: their cells are left out (they do not read as sentences).
TABLES = frozenset(
    {"Ability Scores and Modifiers", "Typical Difficulty Classes", "Travel Pace", "Size Categories"}
)

# The five skill lists under Skills ("Strength", "• Athletics") are lines of the Skills rule.
SKILL_LISTS = {
    "Strength (Skills)": "Skills",
    "Dexterity (Skills)": "Skills",
    "Intelligence (Skills)": "Skills",
    "Wisdom (Skills)": "Skills",
    "Charisma (Skills)": "Skills",
}

# How many lines of the real chapters each thing left out has: the four tables and the two
# openings. The build stops when this changes, so a new paragraph there can't be lost silently.
EXPECTED_LEFT_OUT = {
    "Ability Scores and Modifiers": 18,
    "Typical Difficulty Classes": 8,
    "Travel Pace": 10,
    "Size Categories": 8,
    "Using Ability Scores": 19,
    "Spellcasting": 7,
}

# Titles that are not rules but the openings of a chapter, with what they hold. Their words are
# not rules (they introduce what follows), so they are left out and ATTRIBUTION.md names them.
LEFT_OUT = {
    "Using Ability Scores": "the chapter's introduction",
    "Spellcasting": "the chapter's introduction",
}
# A title that comes more than once, by its place in the chapter (0 is the first): the name of
# its rule. A name the 2024 glossary has (Hit Points, Initiative, Difficult Terrain ...) is kept
# for the one that is the same rule there, so the index pairs them.
DUPLICATES: dict[tuple[str, int], str] = {
    ("Strength", 0): "Strength (Skills)",
    ("Dexterity", 0): "Dexterity (Skills)",
    ("Intelligence", 0): "Intelligence (Skills)",
    ("Wisdom", 0): "Wisdom (Skills)",
    ("Charisma", 0): "Charisma (Skills)",
    ("Strength", 1): "Strength",
    ("Dexterity", 1): "Dexterity",
    ("Intelligence", 1): "Intelligence",
    ("Wisdom", 1): "Wisdom",
    ("Charisma", 1): "Charisma",
    ("Attack Rolls and Damage", 0): "Attack Rolls and Damage (Strength)",
    ("Attack Rolls and Damage", 1): "Attack Rolls and Damage (Dexterity)",
    ("Initiative", 0): "Initiative (Dexterity)",
    ("Initiative", 1): "Initiative",
    ("Hit Points", 0): "Hit Points (Constitution)",
    ("Hit Points", 1): "Hit Points",
    ("Spellcasting Ability", 0): "Spellcasting Ability (Intelligence)",
    ("Spellcasting Ability", 1): "Spellcasting Ability (Wisdom)",
    ("Spellcasting Ability", 2): "Spellcasting Ability (Charisma)",
    ("Saving Throws", 0): "Saving Throws",
    ("Saving Throws", 1): "Saving Throws (Spells)",
    ("Difficult Terrain", 0): "Difficult Terrain",
    ("Difficult Terrain", 1): "Difficult Terrain (Combat)",
    ("Reactions", 0): "Reactions",
    ("Reactions", 1): "Reactions (Casting Time)",
    ("Attack Rolls", 0): "Attack Rolls",
    ("Attack Rolls", 1): "Attack Rolls (Spells)",
    ("Range", 0): "Range (Attacks)",  # (the weapon property Range is "Range")
    ("Range", 1): "Range (Spells)",
}


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


# Characters the PDF's text layer gives for others: a modifier letter apostrophe for the curly
# apostrophe the rest of the book has, and a combining long stroke for the em dash.
RESPELLED = {"\u02bc": "\u2019", "\u0336": "\u2014"}


def _mended(lines: Sequence[Line]) -> list[Line]:
    """The lines with the PDF's odd characters put right, and a minus sign the PDF draws a line
    above the number it belongs to (the one in "from minus 5") put back in that line."""
    out: list[Line] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        following = lines[i + 1] if i + 1 < len(lines) else None
        if (
            following is not None
            and clean(line.text) == "−"
            and 0 < line.y - following.y <= MINUS_ABOVE
            and following.page == line.page
        ):
            pieces = sorted([*following.pieces, *line.pieces], key=lambda p: p.x)
            line = Line(following.x, following.y, pieces, following.page)
            i += 1
        fixed = [Piece(p.x, p.y, p.font, _respelled(p.text)) for p in line.pieces]
        out.append(Line(line.x, line.y, fixed, line.page))
        i += 1
    return out


def _respelled(text: str) -> str:
    return "".join(RESPELLED.get(c, c) for c in text)


def _prose_and_tables(lines: Sequence[Line]) -> tuple[list[Line], dict[str, int]]:
    """The lines with their tables taken out, and each table's name with its line count.

    This part of the PDF sets much of its prose in the sans fonts (Calibri, with Calibri-Bold and
    Calibri-BoldItalic for lead-ins) as well as in the serif ones, so a sans line is not a table.
    The tables are four, each starting with a caption in Calibri-Bold (`TABLES`) and running to
    the next serif line or title; every other sans line is renamed to the serif font the prose
    code reads."""
    out: list[Line] = []
    dropped: dict[str, int] = {}
    table = ""
    for line in _mended(lines):
        text = clean(line.text)
        if line.first_font == "Calibri-Bold" and text in TABLES:
            if text in dropped:
                raise SrdError(f"The table {text!r} comes twice (page {line.page})")
            table = text
            dropped[text] = 1
            continue
        if table and line.first_font.startswith("Calibri") and not is_title(line):
            dropped[table] += 1
            continue
        table = ""
        if line.first_font.startswith("Calibri"):
            pieces = [
                Piece(p.x, p.y, p.font.replace("Calibri", "Cambria"), p.text) for p in line.pieces
            ]
            line = Line(line.x, line.y, pieces, line.page)
        out.append(line)
    if set(dropped) != TABLES:
        raise SrdError(f"Tables not found: {sorted(TABLES - set(dropped))}")
    return out, dropped


def _sections(lines: Sequence[Line]) -> list[tuple[str, int, list[Line]]]:
    """(title, page, its lines) for each title in the chapters, in order."""
    sections: list[tuple[str, int, list[Line]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not is_title(line):
            if not sections:
                raise SrdError(f"Page {line.page}: lines before the first title: {line.text!r}")
            sections[-1][2].append(line)
            i += 1
            continue
        if not _at_an_edge(line):
            raise SrdError(
                f"Page {line.page}: a title not at a column's edge: {clean(line.text)!r}"
            )
        title = clean(line.text)
        j = i + 1
        while (
            j < len(lines)
            and title in CARRIES_ON
            and is_title(lines[j])
            and abs(lines[j].x - line.x) <= EDGE_SLACK
            and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
        ):
            title += " " + clean(lines[j].text)
            j += 1
        sections.append((title, line.page, []))
        i = j
    return sections


def _named(
    sections: Sequence[tuple[str, int, list[Line]]],
) -> list[tuple[str, str, int, list[Line]]]:
    """(entry name, title, page, lines), the duplicated titles given their names. Stops at a title
    that comes more than once with no name given."""
    counts: dict[str, int] = {}
    total: dict[str, int] = {}
    for title, _, _ in sections:
        total[title] = total.get(title, 0) + 1
    named = []
    for title, page, body in sections:
        n = counts.get(title, 0)
        counts[title] = n + 1
        if total[title] == 1:
            name = title
        else:
            name = DUPLICATES.get((title, n), "")
            if not name:
                raise SrdError(
                    f"{title!r} comes twice (page {page}) and has no name for number {n + 1}"
                )
        named.append((name, title, page, body))
    unused = [key for key in DUPLICATES if counts.get(key[0], 0) <= key[1]]
    if unused:
        raise SrdError(f"Named duplicates that are not in the chapters: {unused}")
    return named


def parse_rules(lines: Sequence[Line], vocab: Vocabulary) -> list[Rule]:
    """Every rule in the lines of the four chapters, from the first title up to the spell lists."""
    lines, _ = _prose_and_tables(lines)
    rules: list[Rule] = []
    seen: set[str] = set()
    for name, title, page, body in _named(_sections(lines)):
        if title in LEFT_OUT:
            continue
        text = _paragraphs(body, vocab)
        if not text:
            raise SrdError(f"Rule {name!r} on page {page}: no description")
        if name in SKILL_LISTS:
            # The list's bullets are the skills; the last list is followed by two paragraphs
            # of the Skills rule, which stay paragraphs.
            rows = text.splitlines()
            skills = [row.removeprefix("• ") for row in rows if row.startswith("• ")]
            after = [row for row in rows if not row.startswith("• ")]
            if not skills:
                raise SrdError(f"{name!r} on page {page}: no skills in its list")
            at = next((i for i, r in enumerate(rules) if r.name == SKILL_LISTS[name]), None)
            if at is None:
                raise SrdError(f"{name!r} on page {page}: no {SKILL_LISTS[name]!r} before it")
            words = "\n".join([rules[at].text, f"{title}: {', '.join(skills)}", *after])
            rules[at] = Rule(rules[at].name, "", words, rules[at].page, ())
            continue
        if name in seen:
            raise SrdError(f"Rule {name!r} comes twice (page {page})")
        seen.add(name)
        text = re.sub(r"\n(?=[a-z])", " ", text)
        text = re.sub(r"− (?=\d)", "−", text)  # (the minus sign is drawn apart from its number)
        text = re.sub(r"— (?=\w)", "—", text)  # (a dash at a line's end gets a space after it)
        text = re.sub(r"(?m)^(\d)\.(?=[A-Z])", r"\1. ", text)  # ("1.Choose a target.")
        rules.append(Rule(name, "", text, page, ()))
    return rules


def left_out(lines: Sequence[Line]) -> dict[str, int]:
    """What the tool leaves out and how much: each opening (with its lines), each table (with its
    lines). The list ATTRIBUTION.md names and the build
    checks, so a new drop can't pass unnoticed."""
    prose, found = _prose_and_tables(lines)
    for name, title, _, body in _named(_sections(prose)):
        if title in LEFT_OUT:
            found[name] = len(body)
    return found
