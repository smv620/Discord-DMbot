"""Turning the SRD 5.2.1's Monsters A-Z and Animals sections into monster entries (#900).
Pure, like `parse`: it reads lines, not the PDF.

A **stat block** starts with the creature's name in the semi-bold sans font, followed by an
italic line (`Small Fey (Goblinoid), Chaotic Neutral`). Then come, each in bold on a line
of its own: `AC 15  Initiative +2 (12)`, `HP 10 (3d6)`, `Speed 30 ft.`, a table of the six
abilities (`Str 8 -1 -1 Dex 15 +2 +2 ...`: score, modifier and saving throw), the optional
`Skills`, `Gear`, `Resistances`, `Vulnerabilities` and `Immunities`, then `Senses`,
`Languages` and `CR 1/4 (XP 50; PB +2)`. The traits and actions follow under the headings
Traits, Actions, Bonus Actions, Reactions and Legendary Actions; each begins with a
bold-italic name, and a spell list begins with a bold line ("At Will:", "1/Day Each:").
A group's name ("Goblins") stands in front of the first creature of the group and is not
an entry.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.parse import Mender, SrdError, is_title
from dmbot.devtools.srd.pdf import Line

SIZE = r"(?:Tiny|Small|Medium|Large|Huge|Gargantuan)"
TYPE_LINE = re.compile(rf"^(?P<size>{SIZE}(?: or {SIZE})?) (?P<rest>.+)$")
AC_LINE = re.compile(r"^AC (?P<ac>\d+) +Initiative (?P<initiative>.+)$")
HP_LINE = re.compile(r"^HP (?P<hp>\d+) \((?P<dice>.+)\)$")
ABILITY = re.compile(r"(Str|Dex|Con|Int|Wis|Cha) (\d+) ([+−]?\d+) ([+−]?\d+)", re.IGNORECASE)
CR_LINE = re.compile(r"^CR (?P<cr>\S+) \((?P<xp>.+?); PB \+(?P<pb>\d+)\)$")
ABILITY_NAMES = ("str", "dex", "con", "int", "wis", "cha")
FIELD_LABELS = (
    "Skills", "Gear", "Resistances", "Vulnerabilities", "Immunities", "Senses", "Languages",
)  # fmt: skip
HEADINGS = ("Traits", "Actions", "Bonus Actions", "Reactions", "Legendary Actions")
HEADING_FONT = "GillSans"
BOLD_STARTS = ("Optima-Bold", "Optima-BoldItalic")
ANIMALS_HEADING = "Animals"
SECTION = "Monsters A–Z"
SECTION_ANIMALS = "Animals"

_COMMA_GAP = re.compile(r"(?<=\d) ,")  # "+7 , reach" is "+7, reach"; "7 ,200" is "7,200"
_MINUS = "−"


@dataclass(frozen=True, slots=True)
class Monster:
    """One stat block. The text fields are as printed; the numbers are read out of them."""

    name: str
    section: str
    size: str
    type: str
    alignment: str
    ac: int
    ac_note: str  # what follows the armor class number, as printed (5.1)
    initiative: str
    hp: int
    hit_dice: str
    speed: str
    abilities: tuple[tuple[str, int, int], ...]  # (name, score, modifier)
    # The saving throws that differ from the modifier, as the 5.1 prints them. The 5.2.1
    # prints all six in its table; this line is made from it, not printed.
    saves: str
    skills: str
    gear: str
    resistances: str
    vulnerabilities: str
    immunities: str
    condition_immunities: str  # 5.1 keeps them on a line of their own
    senses: str
    languages: str
    cr: str
    xp: int
    pb: int
    challenge: str  # the CR line as printed, after "CR "
    text: str
    page: int


def _clean(text: str) -> str:
    return _COMMA_GAP.sub(",", " ".join(text.split()))


def _number(text: str) -> int:
    return int(text.replace(_MINUS, "-").replace("+", ""))


def is_stat_block_start(lines: Sequence[Line], i: int) -> bool:
    """Whether `lines[i]` is a creature's name: a title followed by the italic type line
    and then the AC line."""
    return (
        i + 2 < len(lines)
        and is_title(lines[i])
        and lines[i + 1].first_font.endswith("Italic")
        and TYPE_LINE.match(_clean(lines[i + 1].text)) is not None
        and AC_LINE.match(_clean(lines[i + 2].text)) is not None
    )


def parse_monsters(lines: Sequence[Line]) -> list[Monster]:
    """Every creature in `lines` (Monsters A-Z, then Animals, in reading order)."""
    mender = Mender(lines)
    starts = [i for i in range(len(lines)) if is_stat_block_start(lines, i)]
    animals_at = next(
        (
            i
            for i, line in enumerate(lines)
            if is_title(line) and _clean(line.text) == ANIMALS_HEADING
        ),
        len(lines),
    )
    monsters = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = list(lines[start:end])
        while len(block) > 1 and is_title(block[-1]):  # the next group's name
            block.pop()
        section = SECTION_ANIMALS if start > animals_at else SECTION
        monsters.append(_monster(block, section, mender))
    return monsters


def _monster(block: Sequence[Line], section: str, mender: Mender) -> Monster:
    name = _clean(block[0].text)
    where = f"{name!r} on page {block[0].page}"
    kind = TYPE_LINE.match(_clean(block[1].text))
    if kind is None:
        raise SrdError(f"Monster {where}: can't read the type line {_clean(block[1].text)!r}")
    kind_text, comma, alignment = kind["rest"].rpartition(", ")
    if not comma:
        raise SrdError(f"Monster {where}: no alignment in {kind['rest']!r}")
    ac = AC_LINE.match(_clean(block[2].text))
    hp = HP_LINE.match(_clean(block[3].text)) if len(block) > 3 else None
    if ac is None or hp is None:
        raise SrdError(f"Monster {where}: can't read the AC and HP lines")
    values: dict[str, str] = {}
    abilities: dict[str, tuple[int, int, int]] = {}
    i = 4
    label = ""
    cr_line = ""
    while i < len(block):
        line = block[i]
        text = _clean(line.text)
        word = text.split(" ", 1)[0]
        if line.first_font == HEADING_FONT and text.startswith("MOD"):
            i += 1  # the table's heading ("MOD SAVE MOD SAVE MOD SAVE")
            continue
        if line.first_font == HEADING_FONT and text in HEADINGS:
            break
        if word == "Speed" and "speed" not in values:
            values["speed"], label = text[len("Speed") :].strip(), "speed"
        elif found := ABILITY.findall(text):
            for ability, score, mod, save in found:
                abilities[ability.lower()] = (int(score), _number(mod), _number(save))
        elif line.first_font in BOLD_STARTS and word in FIELD_LABELS:
            values[word.lower()], label = text[len(word) :].strip(), word.lower()
        elif line.first_font in BOLD_STARTS and word == "CR":
            cr_line, label = text, "cr"
        elif label and label != "cr":  # a value that wrapped
            values[label] = f"{values[label]} {text}"
        else:
            raise SrdError(f"Monster {where}: can't place the line {text!r}")
        i += 1
    cr = CR_LINE.match(cr_line)
    if cr is None:
        raise SrdError(f"Monster {where}: can't read the CR line {cr_line!r}")
    xp = re.search(r"\d[\d,]*", cr["xp"])
    if xp is None or set(abilities) != set(ABILITY_NAMES) or "speed" not in values:
        raise SrdError(f"Monster {where}: no XP, abilities or speed")
    body = _body(block[i:], mender)
    if not body:
        raise SrdError(f"Monster {where}: no traits or actions")
    return Monster(
        name=name,
        section=section,
        size=kind["size"],
        type=kind_text,
        alignment=alignment,
        ac=int(ac["ac"]),
        ac_note="",
        initiative=ac["initiative"],
        hp=int(hp["hp"]),
        hit_dice=hp["dice"],
        speed=values["speed"],
        abilities=tuple((a, *abilities[a][:2]) for a in ABILITY_NAMES),
        saves=_saves(abilities),
        skills=values.get("skills", ""),
        gear=values.get("gear", ""),
        resistances=values.get("resistances", ""),
        vulnerabilities=values.get("vulnerabilities", ""),
        immunities=values.get("immunities", ""),
        condition_immunities="",
        senses=values.get("senses", ""),
        languages=values.get("languages", ""),
        cr=cr["cr"],
        xp=int(xp.group().replace(",", "")),
        pb=int(cr["pb"]),
        challenge=cr_line[len("CR ") :],
        text=body,
        page=block[0].page,
    )


def _saves(abilities: dict[str, tuple[int, int, int]]) -> str:
    """ "Dex +5, Wis +3": the saving throws that are not just the modifier (the ones the
    creature is proficient in), the way the 5.1 prints them."""
    found = [
        f"{name.capitalize()} {_signed(save)}"
        for name in ABILITY_NAMES
        for _, mod, save in [abilities[name]]
        if save != mod
    ]
    return ", ".join(found)


def _signed(number: int) -> str:
    return f"+{number}" if number >= 0 else f"{_MINUS}{-number}"


def _body(lines: Sequence[Line], mender: Mender) -> str:
    """The traits and actions: each heading on a line of its own, then a paragraph for each
    trait or action. A bold line begins one (but a bold name still inside its brackets
    carries on); a plain or italic line carries on the one before it."""
    rows: list[str] = []
    for line in lines:
        text = _clean(line.text)
        if not text:
            continue
        if line.first_font == HEADING_FONT:
            rows.append(text)
            continue
        name = rows[-1].split(". ", 1)[0] if rows else ""
        open_name = name.count("(") > name.count(")")
        fresh = (
            not rows or rows[-1] in HEADINGS or (line.first_font in BOLD_STARTS and not open_name)
        )
        if fresh:
            rows.append(text)
        elif rows[-1].endswith("-"):  # a word cut at the end of the line
            rows[-1] = mender.join(rows[-1], text)
        else:
            rows[-1] += " " + text
    return "\n".join(rows)
