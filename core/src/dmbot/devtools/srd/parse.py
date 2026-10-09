"""Turning the SRD's lines into spells and conditions (#866). Pure: no PDF, no files.

The SRD 5.2.1 is set so that its structure shows in its fonts, which is how this finds it:

- A **spell** starts with its name in the semi-bold sans font, then a line in italics:
  `Level 3 Evocation (Sorcerer, Wizard)`, or `Evocation Cantrip (Sorcerer, Wizard)`. Four
  labelled lines follow (Casting Time, Range, Components, Duration), then the description
  until the next spell. Long values wrap onto the next line.
- A **condition** is a Rules Glossary entry whose name ends in `[Condition]`, in the same
  semi-bold sans font; its description runs to the next glossary entry.

A line break in the PDF may cut a word ("Hit Point mini-" / "mum"): the PDF draws a real
hyphen and a cut one the same way. `Mender` puts words back together by asking the
document itself: a joined word is used if it appears whole elsewhere, a hyphenated one if
that appears elsewhere. Anything else is joined (cut hyphens are far more common).

When something doesn't look as expected the tool stops with a message saying where, rather
than guessing: a person reads the result before it is committed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.pdf import Line

TITLE_FONT = "GillSans-SemiBold"
SANS_FONT = "GillSans"
ITALIC_FONT = "Cambria-Italic"
BODY_FAMILY = "Cambria"
STAT_FAMILY = "Optima"  # stat blocks inside a spell; a line starting in plain Optima wraps on
STAT_STARTS = ("Optima-Bold", "Optima-BoldItalic")  # a stat block entry begins in bold
BOLD_LEAD_INS = ("Cambria-Bold", "Cambria-BoldItalic")

LEVEL_LINE = re.compile(r"^Level (?P<level>\d) (?P<school>[A-Z][a-z]+) \((?P<classes>.+)\)$")
CANTRIP_LINE = re.compile(r"^(?P<school>[A-Z][a-z]+) Cantrip \((?P<classes>.+)\)$")
# What a spell's own description never has, but a merged-in next spell would.
SPELL_HEADING_IN_TEXT = re.compile(
    r"^(Casting Time:|Level \d+ [A-Z][a-z]+ \(|[A-Z][a-z]+ Cantrip \()", re.MULTILINE
)
# The start of either, on the first of its lines (a long list of classes wraps).
KIND_START = re.compile(r"^(Level \d+ [A-Z][a-z]+|[A-Z][a-z]+ Cantrip) \(")
FIELDS = ("Casting Time", "Range", "Components", "Duration")
# The SRD's Barkskin says "Component:" (a typo in the document); both are read.
FIELD_LABEL = re.compile(r"^(?P<label>Casting Time|Range|Components?|Duration):\s*(?P<value>.*)$")
CONDITION_TAG = re.compile(r"^(?P<name>.+?) \[Condition\]$")
WORD = re.compile(r"[A-Za-z’']+")
# A stat block's ability scores are set in small capitals, which come out in mixed case
# ("dex 14", "WiS 3"): the name is written once, as "Dex".
ABILITY_NAME = re.compile(r"\b(str|dex|con|int|wis|cha)\b(?= \d)", re.IGNORECASE)
HYPHENATED = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)+")


def is_lead_in(line: Line) -> bool:
    """A paragraph's bold heading ("Audible Alarm."), which ends in a full stop; a bold
    name link at the start of a wrapped line ("Steed" of "Otherworldly Steed") doesn't."""
    return line.first_font in BOLD_LEAD_INS and line.first_text.endswith(".")


def is_title(line: Line) -> bool:
    """In the semi-bold sans font or its small-caps variant ("GillSans-SemiBold-SC700")."""
    return line.first_font.startswith(TITLE_FONT)


class SrdError(ValueError):
    """The SRD isn't laid out as this tool expects. The message says where."""


@dataclass(frozen=True, slots=True)
class Spell:
    name: str
    level: int  # 0 for a cantrip
    school: str
    classes: tuple[str, ...]
    casting_time: str
    range: str
    components: str
    duration: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    text: str
    page: int


class Mender:
    """Puts back together words that a line break cut (see the module's note)."""

    def __init__(self, lines: Iterable[Line]) -> None:
        self.whole: set[str] = set()
        self.hyphenated: set[str] = set()
        previous_cut = False
        for line in lines:
            text = " ".join(line.text.split())
            words = list(WORD.finditer(text))
            for i, match in enumerate(words):
                first, last = i == 0 and previous_cut, i == len(words) - 1 and text.endswith("-")
                if not (first or last):
                    self.whole.add(match.group().lower())
            self.hyphenated.update(m.group().lower() for m in HYPHENATED.finditer(text))
            previous_cut = text.endswith("-")

    def join(self, left: str, right: str) -> str:
        """`left` ends in a hyphen (perhaps after a space); `right` is the next line."""
        spaced = left.endswith(" -")
        head = left[:-1].rstrip()
        tail = WORD.findall(head)[-1:] or [""]
        lead = WORD.findall(right)[:1] or [""]
        whole = (tail[0] + lead[0]).lower()
        hyphenated = f"{tail[0]}-{lead[0]}".lower()
        if right[:1].isdigit():  # "10-foot-by-" / "10-foot": a number goes with the hyphen
            return f"{head}-{right}"
        if hyphenated in self.hyphenated and whole not in self.whole:
            return f"{head}-{right}"
        if whole in self.whole or spaced or hyphenated not in self.hyphenated:
            return f"{head}{right}"
        return f"{head}-{right}"


def _clean(text: str) -> str:
    return " ".join(text.split())


def _paragraphs(lines: Sequence[Line], mender: Mender) -> str:
    """The description: wrapped lines made into paragraphs. A new paragraph starts at an
    indented line, at a bold lead-in ("Audible Alarm."), and wherever the font changes
    (a table, a stat block); those lines each stay on a line of their own."""
    paragraphs: list[str] = []
    previous_body = previous_stat = False
    for line in lines:
        raw = line.text
        body = line.first_font.startswith(BODY_FAMILY)
        # A stat block line that doesn't start an entry (in bold) carries on the line
        # before it, whether it starts in plain or italic Optima ("Constitution Saving /
        # Throw:").
        in_stat = line.first_font.startswith(STAT_FAMILY)
        # ...and so does a bold entry name still inside its brackets: "Healing Touch
        # (Celestial Only; Recharges after a Long" / "Rest)."
        name = paragraphs[-1].split(". ", 1)[0] if paragraphs else ""  # before its first sentence
        open_name = name.count("(") > name.count(")")
        wrapped = in_stat and previous_stat and (line.first_font not in STAT_STARTS or open_name)
        fresh = not wrapped and (
            not paragraphs
            or not body
            or not previous_body
            or raw.startswith(" ")
            or is_lead_in(line)
        )
        cut = bool(paragraphs) and paragraphs[-1].endswith("-")  # a word cut at the line's end
        text = raw.strip() if fresh else raw.lstrip()
        if cut and not raw.startswith(" ") and not is_lead_in(line):
            paragraphs[-1] = mender.join(paragraphs[-1], text)  # a table row or stat block too
        elif fresh:
            paragraphs.append(text)
        elif paragraphs[-1].endswith("-"):
            paragraphs[-1] = mender.join(paragraphs[-1], text)
        else:
            paragraphs[-1] += " " + text
        previous_body = body
        previous_stat = in_stat
    return "\n".join(ABILITY_NAME.sub(_titled, _clean(p)) for p in paragraphs if p.strip())


def _titled(match: re.Match[str]) -> str:
    return match.group().capitalize()


def _fix_title(line: Line) -> str:
    """The name as printed. One title is set in a small-caps variant of the font, which
    comes out as "Acid SplASh"; those pieces are put into ordinary capitals."""
    parts = []
    for piece in line.pieces:
        text = piece.text.replace("\n", "")
        if piece.font.endswith("SC700"):
            text = " ".join(w[:1] + w[1:].lower() for w in text.split(" "))
        parts.append(text)
    return _clean("".join(parts))


def parse_spells(lines: Sequence[Line]) -> list[Spell]:
    """Every spell in `lines` (the Spell Descriptions section, in reading order)."""
    mender = Mender(lines)
    starts = [
        i
        for i in range(len(lines) - 1)
        if is_title(lines[i])
        and lines[i + 1].starts_with_font(ITALIC_FONT)
        and KIND_START.match(_clean(lines[i + 1].text))
    ]
    spells = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        spells.append(_spell(lines[start:end], mender))
    for spell in spells:  # a spell whose heading wasn't recognised ran into the one before
        if SPELL_HEADING_IN_TEXT.search(spell.text):
            raise SrdError(f"Spell {spell.name!r} on page {spell.page}: another spell is inside it")
    return spells


def _spell(block: Sequence[Line], mender: Mender) -> Spell:
    name, page = _fix_title(block[0]), block[0].page
    where = f"{name!r} on page {page}"
    i = 1
    level_text = []
    while i < len(block) and block[i].starts_with_font(ITALIC_FONT):
        level_text.append(block[i].text)
        i += 1
    kind = _clean(" ".join(level_text))
    if match := LEVEL_LINE.match(kind):
        level, school = int(match["level"]), match["school"]
    elif match := CANTRIP_LINE.match(kind):
        level, school = 0, match["school"]
    else:
        raise SrdError(f"Spell {where}: can't read the level line {kind!r}")
    classes = tuple(c.strip() for c in match["classes"].split(","))
    values: dict[str, str] = {}
    field_name = ""
    while i < len(block):
        line = block[i]
        labelled = FIELD_LABEL.match(line.text)  # in the sans font, or (some) in the body's
        if labelled is not None:
            field_name = "Components" if labelled["label"] == "Component" else labelled["label"]
            values[field_name] = _clean(labelled["value"])
        elif field_name and (field_name != "Duration" or line.starts_with_font(SANS_FONT)):
            # A value that wrapped: any line before the Duration, and after it only one in
            # the sans font (the description starts in the body's).
            values[field_name] = _clean(f"{values[field_name]} {line.text}")
        else:
            break
        i += 1
    missing = [f for f in FIELDS if f not in values]
    if missing:
        raise SrdError(f"Spell {where}: no {', '.join(missing)}")
    text = _paragraphs(block[i:], mender)
    if not text:
        raise SrdError(f"Spell {where}: no description")
    return Spell(
        name,
        level,
        school,
        classes,
        values["Casting Time"],
        values["Range"],
        values["Components"],
        values["Duration"],
        text,
        page,
    )


def parse_conditions(lines: Sequence[Line]) -> list[Condition]:
    """Every glossary entry tagged `[Condition]` in `lines` (the Rules Glossary)."""
    mender = Mender(lines)
    conditions = []
    for i, line in enumerate(lines):
        match = CONDITION_TAG.match(_clean(line.text)) if is_title(line) else None
        if match is None:
            continue
        end = i + 1
        while end < len(lines) and not is_title(lines[end]):
            end += 1
        text = _paragraphs(lines[i + 1 : end], mender)
        if not text:
            raise SrdError(f"Condition {match['name']!r} on page {line.page}: no description")
        conditions.append(Condition(match["name"], text, line.page))
    return conditions
