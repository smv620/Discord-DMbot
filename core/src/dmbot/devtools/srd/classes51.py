"""Turning the 2014 SRD 5.1's Classes chapter into class features (#1903). Pure: no PDF, no files.

The 5.1 chapter is laid out differently from 5.2.1's (see `classes`):

- A feature's title is a semi-bold sans heading with **no level in it** (`Rage`), and its
  description follows in the body font. The level is in the class's table, in a "Features"
  column: a row starts with its level (`1st`) and lists the names of the features gained, wrapped
  over several lines and with the table's numbers between the words ("Relentless 4 +3" then
  "Rage"). A feature is a heading whose name is in that table, and its level is the first row that
  names it (Ability Score Improvement is at 4, as in 5.2.1).
- The class's subclass (a "path", "college", "domain"...) has no table. Its section is headed by its
  name, which the SRD gives for each class (`SUBCLASSES`; the tool stops if one is missing), and
  its features are headings whose first sentence says the level ("At 3rd level, ...").
- Any other semi-bold heading ("Cantrips", "Ritual Casting", "Archery") is part of the feature
  above it and is kept in its text as a line of its own, except the option lists in
  `OPTION_LISTS`, which are left out as 5.2.1's were. The proficiencies and the equipment
  (headed or labelled lines that the PDF sets around the table, sometimes in the middle of a
  feature) and the class tables themselves are left out.

When something doesn't look as expected the tool stops and says where.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.devtools.srd.classes import Feature
from dmbot.devtools.srd.parse import EDGE_SLACK, SrdError
from dmbot.devtools.srd.parse51 import Vocabulary, _paragraphs, clean
from dmbot.devtools.srd.pdf import Line, Piece
from dmbot.rules.index import CLASS_NAMES

CLASSES = tuple(CLASS_NAMES)
LEFT_EDGE = 57.6  # the 5.1 pages' columns start here and at 328.6
EDGES_51 = (57.6, 328.6)
# Headings and labelled lines of a class's start, set around its table; none is a feature.
INTRO_HEADINGS = frozenset(["Class Features", "Hit Points", "Proficiencies", "Equipment"])
LABELS = ("Hit Dice:", "Hit Points at", "Armor:", "Weapons:", "Tools:", "Saving Throws:", "Skills:")
# The one subclass the SRD gives each class, by the name its heading has.
SUBCLASSES = {
    "Barbarian": "Path of the Berserker",
    "Bard": "College of Lore",
    "Cleric": "Life Domain",
    "Druid": "Circle of the Land",
    "Fighter": "Champion",
    "Monk": "Way of the Open Hand",
    "Paladin": "Oath of Devotion",
    "Ranger": "Hunter",
    "Rogue": "Thief",
    "Sorcerer": "Draconic Bloodline",
    "Warlock": "The Fiend",
    "Wizard": "School of Evocation",
}
# Features whose description is followed by the list of their options, each a heading of its own
# (the fighting styles, the metamagic options, the invocations, the pacts). The lists are long and
# a question about the feature is not a question about all of them, so they are left out, as the
# 5.2.1 parser leaves its option lists out.
OPTION_LISTS = frozenset(["Fighting Style", "Metamagic", "Eldritch Invocations"])
# Sections that are not features and end the class's list of them: the introductions to the
# subclasses ("Martial Archetypes") and the flavour text after the last subclass. They and
# everything under them, up to the subclass's heading, are left out.
TRAILING = frozenset([
    "Martial Archetypes", "Monastic Traditions", "Sacred Oaths", "Ranger Archetypes",
    "Roguish Archetypes", "Sorcerous Origins", "Arcane Traditions",
    "Sacred Plants and Wood", "Druids and the Gods", "Breaking Your Oath", "Your Pact Boon",
    "Your Spellbook",
])  # fmt: skip
# Headings in a subclass's section that are not features and are left out, with the text under
# them: the list of the subclass's spells and the tenets of the oath. Any other heading there with
# no level in its first sentence stops the tool, so a feature is never dropped without a word.
SUBCLASS_INTROS = frozenset(["Tenets of Devotion", "Oath Spells", "Expanded Spell List"])
# What the class table puts in its Features cell that is not a feature of its own: a slot for the
# subclass's ("Path feature"), a note that an earlier feature gets better ("Wild Shape
# improvement") or the ordinal's last letters, which the Warlock's table sets apart.
NOT_A_FEATURE = re.compile(r"(?:\bfeature|\bimprovements?|^(?:st|nd|rd|th))$")
MOST_WORDS = 6  # a table "item" longer than this is the prose of another table's page
# A row's cells wrap over at most this many lines (the longest is 4); the last row would otherwise
# take in the plain-font text that follows the table on the Wizard's page.
MOST_LINES = 5
ROW = re.compile(r"^(?P<level>\d{1,2})(?:st|nd|rd|th)\b")
_BRACKETS = re.compile(r"\([^)]*\)")
_NUMBERS = re.compile(r"\b\d+d\d+\b|(?<![A-Za-z])[+\-−]?\d[\d/]*(?![A-Za-z])|\bft\.?")
SAME_ROW = 1.0  # points: a line this near a row's height is on that row
COLUMN_SPLIT_51 = 300.0
LEVELS = 20
WORDS = re.compile(r"[A-Za-z][A-Za-z’'-]*")
# "At 3rd level", "Starting at 3rd level", "By 7th level", "When you reach 6th level", and "At 3rd,
# 5th, 7th, and 9th level" (the first of the levels is the one it is gained at).
LEVEL_PHRASE = re.compile(
    r"\b(?:at|by|reach|reaching)\s+(?P<level>\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE
)
FIRST_WORDS = 260  # the level a subclass feature says is in its first sentence or so
SAME_HEADING = 20.0  # title lines this close (points) with no text between are one heading


@dataclass(slots=True)
class _Block:
    """A heading and the lines under it, before it is known whether it is a feature."""

    name: str
    page: int
    klass: str
    subclass: str  # "" in the class's own section
    level: int | None
    lines: list[Line] = field(default_factory=list)


def _is_title(line: Line) -> bool:
    return line.first_font.startswith("GillSans")


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in EDGES_51)


def _is_table(line: Line) -> bool:
    return line.first_font.startswith(("Calibri", "HiraKaku"))


def _heading(lines: Sequence[Line], i: int) -> tuple[str, int]:
    """The words of the heading that starts at `lines[i]` and the index after it: a long heading
    that wraps is its title lines in a row."""
    text = clean(lines[i].text)
    j = i + 1
    while (
        j < len(lines)
        and _is_title(lines[j])
        and abs(lines[j].x - lines[i].x) <= EDGE_SLACK
        and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
    ):
        text += " " + clean(lines[j].text)
        j += 1
    return text, j


@dataclass(slots=True)
class _Row:
    """One row of a class table: its level, where it starts, and the text of its Features cell
    (with the other columns' numbers still in it until `items` is asked for)."""

    level: int
    page: int
    y: float
    text: list[str] = field(default_factory=list)
    lines: int = 0

    @property
    def items(self) -> list[list[str]]:
        """The names in the Features cell, each as its lower-case words: the cell's text joined,
        its brackets ("(1 die)") and numbers left out, cut at the commas."""
        joined = _BRACKETS.sub(" ", " ".join(self.text))
        joined = _NUMBERS.sub(" ", joined)
        return [
            [w.lower() for w in WORDS.findall(_apostrophes(item))] for item in joined.split(",")
        ]


def table_levels(lines: Sequence[Line]) -> list[_Row]:
    """The class table's rows, from the table's plain lines in reading order. A wide table (the
    Monk's) is printed in two pieces: the first columns, then the Features cell of each row at
    the same height further along; a line without a level of its own joins the row at its height
    when there is one, and otherwise the row above it (a cell that wraps, or runs on into the
    next column). The table after the class's own (the beast shapes, the spell lists) adds rows
    at the end, which `level_in` never reads."""
    rows: list[_Row] = []
    current: _Row | None = None
    for line in lines:
        if line.first_font != "Calibri":
            continue
        text = clean(line.text)
        match = ROW.match(text)
        if match:
            current = _Row(int(match["level"]), line.page, line.y)
            rows.append(current)
            text = text[match.end() :]
        else:
            aligned = [r for r in rows if r.page == line.page and abs(r.y - line.y) <= SAME_ROW]
            if len(aligned) == 1 and line.x > COLUMN_SPLIT_51:
                current = aligned[0]
        if current is not None and current.lines < MOST_LINES:
            current.text.append(text)
            current.lines += 1
    return rows


def level_in(name: str, rows: Sequence[_Row]) -> int | None:
    """The level of the first row of the class table (levels 1 to 20, in order) that has `name`
    among its features. An item may hold a word of another column between the words of the name
    ("Primal" "Unlimited" "Champion"), but must start with the name's first word, so "Protection"
    is not "Aura of Protection"."""
    words = [w.lower() for w in WORDS.findall(_apostrophes(name))]
    if not words:
        return None
    for row in rows[:LEVELS]:
        for item in row.items:
            if item[:1] == words[:1] and _in_order(words, item):
                return row.level
    return None


def _apostrophes(text: str) -> str:
    """The table spells "Thieves' Cant" with a modifier letter (U+02BC) where the heading has a
    curly apostrophe; both are read as the curly one."""
    return text.replace("'", "’").replace("ʼ", "’")


def _in_order(words: Sequence[str], item: Sequence[str]) -> bool:
    remaining = iter(item)
    return all(word in remaining for word in words)


def stated_level(lines: Sequence[Line]) -> int | None:
    """The level a subclass feature's first sentence names ("At 3rd level, ...")."""
    words = " ".join(clean(line.text) for line in lines if line.first_font.startswith("Cambria"))
    match = LEVEL_PHRASE.search(words[:FIRST_WORDS])
    return int(match["level"]) if match else None


def _blocks(segment: Sequence[Line], klass: str) -> list[_Block]:
    """The headings of one class's lines with the lines under them. Proficiency and equipment
    lines, and tables, are not kept."""
    rows = table_levels(segment)
    if not rows:
        raise SrdError(f"{klass}: no class table (rows that start with a level)")
    blocks: list[_Block] = []
    subclass = ""
    dropping = False  # inside the proficiencies or the equipment
    i = 0
    while i < len(segment):
        line = segment[i]
        text = clean(line.text)
        if _is_table(line):
            dropping = False  # the table follows the equipment; a table is never a feature's text
            i += 1
            continue
        if _is_title(line) and _at_an_edge(line):
            name, i = _heading(segment, i)
            if name in INTRO_HEADINGS:
                dropping = True
            elif name == klass:
                dropping = False
            elif name == SUBCLASSES[klass]:
                dropping, subclass = False, name
                blocks.append(_Block(name, line.page, klass, subclass, None))
                blocks[-1].name = ""  # the subclass's own heading: it holds no feature text
            else:
                dropping = False
                level = None if subclass else level_in(name, rows)
                blocks.append(_Block(name, line.page, klass, subclass, level))
            continue
        if line.first_font == "Cambria-Bold" and text.startswith(LABELS):
            dropping = True
        if dropping and line.first_font in ("Cambria", "Cambria-Bold", "Symbol"):
            i += 1
            continue
        if blocks:
            blocks[-1].lines.append(line)
        i += 1
    return blocks


def parse_features51(lines: Sequence[Line], vocab: Vocabulary) -> list[Feature]:
    """Every class feature in the lines of the Classes chapter, with its class, level and
    subclass."""
    starts = [
        i
        for i, line in enumerate(lines)
        if _is_title(line) and abs(line.x - LEFT_EDGE) <= EDGE_SLACK and clean(line.text) in CLASSES
    ]
    if [clean(lines[i].text) for i in starts] != list(CLASSES):
        raise SrdError(
            f"The 5.1 classes were not found in order: {[lines[i].text for i in starts]}"
        )
    ends = [*starts[1:], len(lines)]
    features: list[Feature] = []
    for start, end in zip(starts, ends, strict=True):
        features += parse_class(lines[start:end], clean(lines[start].text), vocab)
    return features


def parse_class(lines: Sequence[Line], klass: str, vocab: Vocabulary) -> list[Feature]:
    """The features of one class, from its lines (its title first)."""
    rows = table_levels(lines)
    check_table(rows, klass)
    return _class_features(_blocks(lines, klass), rows, klass, vocab)


def check_table(rows: Sequence[_Row], klass: str) -> None:
    """The class table has its 20 levels, in order, first (what comes after is another table)."""
    if [r.level for r in rows[:LEVELS]] != list(range(1, LEVELS + 1)):
        raise SrdError(f"{klass}: the class table does not start with levels 1 to {LEVELS}")


def unmatched_items(rows: Sequence[_Row], names: Sequence[str]) -> list[str]:
    """The names in the table's Features cells that no feature was found for (not counting the
    cells that are not features). A feature that is in the table but whose heading was not read
    would otherwise be lost without a word."""
    found = [[w.lower() for w in WORDS.findall(_apostrophes(n))] for n in names]
    missing: list[str] = []
    for row in rows[:LEVELS]:
        for item in row.items:
            text = " ".join(item)
            if not item or len(item) > MOST_WORDS or NOT_A_FEATURE.search(text):
                continue
            if not any(item[:1] == f[:1] and _in_order(f, item) for f in found if f):
                missing.append(f"{text} (level {row.level})")
    return missing


def _class_features(
    blocks: Sequence[_Block], rows: Sequence[_Row], klass: str, vocab: Vocabulary
) -> list[Feature]:
    """The features of one class: a heading with a level is one, any other heading is folded
    into the feature above it. What is left out is left out on purpose and by name (an option
    list, an introduction, flavour text); anything else that would be lost stops the tool."""
    kept: list[_Block] = []
    seen_subclass = False
    listing = False  # in a list of options set apart after the class's last feature
    for block in blocks:
        where = f"{klass}: {block.name!r} on page {block.page}"
        if block.name == "":  # the subclass's heading
            if seen_subclass:
                raise SrdError(f"{klass}: the subclass heading comes twice (page {block.page})")
            seen_subclass, listing = True, False
            continue
        if block.subclass and block.level is None:
            block.level = stated_level(block.lines)
        if block.level is not None and not block.subclass and listing:
            raise SrdError(f"{where} is a feature of the class, after a section left out")
        if listing:
            continue
        if block.level is not None and any(
            k.name == block.name and k.subclass == block.subclass for k in kept
        ):
            if block.name not in OPTION_LISTS:
                raise SrdError(f"{where} comes twice")
            listing = True  # the Warlock's invocations, headed again after the last feature
            continue
        if block.level is not None:
            kept.append(block)
            continue
        if block.name in TRAILING:
            listing = True
            continue
        if not kept:
            raise SrdError(f"{where} comes before any feature")
        parent = kept[-1]
        # Inside the subclass's section a heading with no level is its introduction or spell list
        # (named in SUBCLASS_INTROS) before its first feature, or a part of the feature above it.
        if block.subclass and parent.subclass != block.subclass:
            if block.name not in SUBCLASS_INTROS:
                raise SrdError(f"{where} is in the subclass but says no level")
            continue
        if parent.name in OPTION_LISTS:
            continue
        parent.lines.append(_title_line(block))
        parent.lines.extend(block.lines)
    if not seen_subclass:
        raise SrdError(f"{klass}: its subclass {SUBCLASSES[klass]!r} was not found")
    for subclass in (False, True):
        levels = [b.level or 0 for b in kept if bool(b.subclass) == subclass]
        if levels != sorted(levels):
            raise SrdError(f"{klass}: the levels of its features are not in order: {levels}")
    if missing := unmatched_items(rows, [b.name for b in kept if not b.subclass]):
        raise SrdError(f"{klass}: in the class table but no feature was read: {missing}")
    out = []
    for block in kept:
        words = _paragraphs(block.lines, vocab)
        if not words:
            raise SrdError(f"{klass}: feature {block.name!r} on page {block.page}: no description")
        out.append(Feature(block.name, klass, block.level or 0, block.subclass, words, block.page))
    return out


def _title_line(block: _Block) -> Line:
    """A folded heading as a line of its own, in a font the paragraph step reads as a heading."""
    return Line(
        x=0.0, y=0.0, page=block.page, pieces=[Piece(0.0, 0.0, "GillSans-SemiBold", block.name)]
    )
