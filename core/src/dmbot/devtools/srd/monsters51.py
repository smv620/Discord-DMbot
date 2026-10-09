"""Turning the 2014 SRD 5.1's monster pages into monster entries (#900). Pure, like
`monsters`, and with the same repairs as `parse51` (the 5.1 PDF's text layer is rough).

A **stat block** starts with the creature's name in bold (the same font as its labels), an
italic line (`Small humanoid (goblinoid), neutral evil`) and then, each on a line of its
own with the label in bold: `Armor Class 15 (leather armor, shield)`, `Hit Points 7 (2d6)`,
`Speed 30 ft.`, a table of the six abilities (`STR DEX CON INT WIS CHA` over `8 (-1) 14
(+2) ...`), the optional `Saving Throws`, `Skills`, `Damage Vulnerabilities`, `Damage
Resistances`, `Damage Immunities` and `Condition Immunities`, then `Senses`, `Languages`
and `Challenge 1/4 (50 XP)`. The traits follow, then the headings Actions, Reactions and
Legendary Actions; each trait or action begins with a bold-italic name. A group's name
("Fungi") stands in front of its first creature and is not an entry.

The labels are read with their spaces ignored, since the text layer cuts words ("Armo r
Class").
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from dmbot.devtools.srd import monsters as shared
from dmbot.devtools.srd.monsters import Monster
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line

TYPE_LINE = re.compile(rf"^(?P<size>{shared.SIZE}) (?P<rest>.+)$")
SIZES = ("tiny", "small", "medium", "large", "huge", "gargantuan")
LABEL_FONT = "Calibri-Bold"
LEAD_IN_FONT = "Calibri-BoldItalic"
ITALIC_FONT = "Calibri-Italic"
BODY_FONT = "Calibri"
PROSE_FAMILY = "Cambria"
GROUP_FONTS = ("GillSans-SemiBold", "GillSans")
PARAGRAPH_GAP = 13.5  # lines of one paragraph are about 12 apart; paragraphs about 15
LABELS = {
    "armorclass": "ac",
    "hitpoints": "hp",
    "speed": "speed",
    "savingthrows": "saves",
    "skills": "skills",
    "damagevulnerabilities": "vulnerabilities",
    "damageresistances": "resistances",
    "damageimmunities": "immunities",
    "conditionimmunities": "condition_immunities",
    "senses": "senses",
    "languages": "languages",
    "challenge": "cr",
}
HEADINGS = ("Actions", "Reactions", "Legendary Actions")
SCORES = re.compile(r"(\d+) *\( *([+−-] *\d+) *\)")
CR_VALUE = re.compile(r"^(?P<cr>\S+) *\((?P<xp>[\d,]+) *XP\)$")
AC_VALUE = re.compile(r"^(?P<ac>\d+) *(?P<note>.*)$")  # "14 (natural armor), 11 while prone"
HP_VALUE = re.compile(r"^(?P<hp>\d+) *\((?P<dice>.+)\)$")
_SPLIT_NUMBER = re.compile(r"(?<=\d) (?=\d\b)")  # "17d10 + 8 5" is "17d10 + 85"
# a spell list's lines ("At will: ...", "1st level (4 slots): ...") each begin a row
SPELL_LINE = re.compile(r"^(atwill|cantrips|\d/day|\d(?:st|nd|rd|th)level)")
_THOUSANDS = re.compile(r"(?<=\d), (?=\d{3}\b)")  # "3, 900 XP" is "3,900 XP"


def _label_pattern(label: str) -> re.Pattern[str]:
    """The label with a space allowed between any two letters; the plural "s" at its end
    may be missing, as the PDF sometimes drops it ("Damage Resistance")."""
    letters = [re.escape(c) for c in label]
    if label.endswith("s"):
        letters[-1] += "?"
    return re.compile(r"^" + r" ?".join(letters) + r"\b ?(?P<value>.*)$", re.I)


LABEL_PATTERNS = {key: _label_pattern(label) for label, key in LABELS.items()}


def _compact(line: Line) -> str:
    return "".join(clean(line.text).lower().split())


def _label_of(line: Line) -> tuple[str, str] | None:
    """(`"senses"`, the rest of the line) if the line begins with one of the stat block's
    labels in bold, whatever stray spaces cut it."""
    if line.first_font != LABEL_FONT:
        return None
    text = clean(line.text)
    for key, pattern in LABEL_PATTERNS.items():
        if match := pattern.match(text):
            return key, match["value"].strip()
    # a label cut so that its end sits in the value ("Chall enge"): compare without spaces
    squeezed = _compact(line)
    for label, key in LABELS.items():
        if squeezed.startswith(label):
            kept = 0
            for index, char in enumerate(text):
                kept += char != " "
                if kept == len(label):
                    return key, text[index + 1 :].strip()
    return None


def _split_at_labels(line: Line) -> list[Line]:
    """A wrapped value can end on the same printed line as the next label ("silvered
    weapons Senses passive Perception 12"): the line is split where the label starts."""
    if line.first_font != BODY_FONT:
        return [line]
    for k, piece in enumerate(line.pieces[1:], 1):
        if piece.font.split("+")[-1] == LABEL_FONT and piece.text.strip():
            tail = Line(piece.x, line.y, line.pieces[k:], line.page)
            if _label_of(tail) is not None:
                return [Line(line.x, line.y, line.pieces[:k], line.page), tail]
    return [line]


def _type_lines(lines: Sequence[Line], i: int) -> int:
    """How many italic lines the type line is set over (it can wrap), from `lines[i]`."""
    count = 0
    while i + count < len(lines) and lines[i + count].first_font == ITALIC_FONT:
        count += 1
    return count


def is_stat_block_start(lines: Sequence[Line], i: int) -> bool:
    """A name, then the italic type line (over one or two lines), then the armor class
    line."""
    kind = _type_lines(lines, i + 1)
    if not 1 <= kind <= 2 or i + 1 + kind >= len(lines):
        return False
    label = _label_of(lines[i + 1 + kind])
    return (
        label is not None
        and label[0] == "ac"
        and _compact(lines[i + 1]).startswith(SIZES)  # ("L arge": the size word can be cut)
    )


def parse_monsters(
    lines: Sequence[Line], sections: Sequence[tuple[int, str]], vocab: Vocabulary
) -> list[Monster]:
    """Every creature in `lines` (pages in reading order). `sections` are (page, name):
    a creature belongs to the last section that starts on or before its page."""
    starts = [i for i in range(len(lines)) if is_stat_block_start(lines, i)]
    monsters = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = list(lines[start:end])
        for k, line in enumerate(block):  # prose in the book's own font ends the stat block
            if line.first_font.startswith(PROSE_FAMILY):
                del block[k:]
                break
        while len(block) > 1 and block[-1].first_font in (LABEL_FONT, *GROUP_FONTS):
            block.pop()  # the next group's name
        name = next(s for p, s in reversed(sections) if p <= block[0].page)
        monsters.append(
            _monster([x for line in block for x in _split_at_labels(line)], name, vocab)
        )
    return monsters


def _monster(block: Sequence[Line], section: str, vocab: Vocabulary) -> Monster:
    name = vocab.repair(clean(block[0].text))
    where = f"{name!r} on page {block[0].page}"
    wrapped = _type_lines(block, 1)
    type_text = vocab.tidy(" ".join(clean(line.text) for line in block[1 : 1 + wrapped]))
    kind = TYPE_LINE.match(type_text)
    if kind is None:
        raise SrdError(f"Monster {where}: can't read the type line {type_text!r}")
    kind_text, comma, alignment = kind["rest"].rpartition(", ")
    if not comma:
        raise SrdError(f"Monster {where}: no alignment in {kind['rest']!r}")
    values: dict[str, str] = {}
    scores: list[tuple[int, int]] = []
    key = ""
    i = 1 + wrapped
    while i < len(block) and key != "cr":
        line = block[i]
        text = clean(line.text)
        label = _label_of(line)
        if label is not None:
            key, value = label
            values[key] = value
        elif line.first_font == LABEL_FONT and _compact(line).startswith("strdex"):
            pass  # the table's heading
        elif line.first_font == BODY_FONT and len(found := SCORES.findall(text)) == 6:
            scores = [(int(s), int(m.replace(" ", "").replace("−", "-"))) for s, m in found]
        elif key and line.first_font == BODY_FONT:  # a value that wrapped
            values[key] = f"{values[key]} {text}"
        else:
            raise SrdError(f"Monster {where}: can't place the line {text!r}")
        i += 1
    ac = AC_VALUE.match(vocab.tidy(values.get("ac", "")))
    hp = HP_VALUE.match(vocab.tidy(values.get("hp", "")))
    cr = CR_VALUE.match(_THOUSANDS.sub(",", vocab.tidy(values.get("cr", ""))))
    if ac is None or hp is None or cr is None or len(scores) != 6 or "speed" not in values:
        raise SrdError(
            f"Monster {where}: can't read its stats ({sorted(values.items())}, {scores})"
        )
    body = _body(block[i:], vocab)
    if not body:
        raise SrdError(f"Monster {where}: no traits or actions")
    field = {k: vocab.tidy(v) for k, v in values.items()}
    field["cr"] = _THOUSANDS.sub(",", field["cr"])
    return Monster(
        name=name,
        section=section,
        size=kind["size"],
        type=vocab.tidy(kind_text),
        alignment=vocab.tidy(alignment),
        ac=int(ac["ac"]),
        ac_note=ac["note"],
        initiative="",
        hp=int(hp["hp"]),
        hit_dice=_SPLIT_NUMBER.sub("", hp["dice"]),
        speed=field["speed"],
        abilities=tuple((a, *scores[n]) for n, a in enumerate(shared.ABILITY_NAMES)),
        saves=field.get("saves", ""),
        skills=field.get("skills", ""),
        gear="",
        resistances=field.get("resistances", ""),
        vulnerabilities=field.get("vulnerabilities", ""),
        immunities=field.get("immunities", ""),
        condition_immunities=field.get("condition_immunities", ""),
        senses=field.get("senses", ""),
        languages=field.get("languages", ""),
        cr=cr["cr"],
        xp=int(cr["xp"].replace(",", "")),
        pb=0,
        challenge=field["cr"],
        text=body,
        page=block[0].page,
    )


def _body(lines: Sequence[Line], vocab: Vocabulary) -> str:
    """The traits and actions: each heading on a line of its own, then a paragraph for each
    trait or action. A bold-italic name begins one, and so does a line that sits a
    paragraph's gap below the one before it (a spell list's lines)."""
    rows: list[str] = []
    previous: Line | None = None
    for line in lines:
        text = clean(line.text)
        if not text:
            continue
        gap = previous.y - line.y if previous is not None else 0.0
        heading = line.first_font == LABEL_FONT and text in HEADINGS
        name_wraps = (
            line.first_font == LEAD_IN_FONT
            and previous is not None
            and previous.first_font == LEAD_IN_FONT
            and not rows[-1].endswith(".")
        )
        fresh = (
            not rows
            or heading
            or rows[-1] in HEADINGS
            or (line.first_font == LEAD_IN_FONT and not name_wraps)
            or gap > PARAGRAPH_GAP
            or SPELL_LINE.match("".join(text.lower().split())) is not None
        )
        if fresh:
            rows.append(text)
        else:
            rows[-1] = vocab.run_on(rows[-1], text)
        previous = line
    return "\n".join(vocab.tidy(r) for r in rows)
