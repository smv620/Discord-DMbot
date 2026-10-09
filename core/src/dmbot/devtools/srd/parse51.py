"""Turning the 2014 SRD 5.1's lines into spells and conditions (#873). Pure, like `parse`.

The 5.1 PDF is laid out differently from 5.2.1's, and its text layer is rougher:

- Its characters are surrounded by tabs, no-break spaces and soft hyphens
  ("2nd-\\xad\\u2010\\u2011level"), which are cleaned away.
- A hyphen has spaces around it ("15 - foot- radius"), which are closed up.
- Words are cut by stray spaces in places ("hig her", "adva ntage", "t o"). A cut word is
  put back together when the whole word is one the SRD itself uses somewhere: the words of
  the 5.2.1 data and the words this PDF uses often. A space is never taken out of two
  words that are both known.
- In a few places the text layer has lost words altogether (a spell's "At Higher Levels"
  line reads "…one additional beast t level above 1st."). Those cannot be put back from
  the PDF; `strays()` counts the lone letters that mark them, and ATTRIBUTION.md says so.

A **spell** starts with its name in the semi-bold sans font and an italic line: `2nd-level
evocation`, `3rd-level necromancy (ritual)` or `Conjuration cantrip`. Four labelled lines
follow (in bold), then the description. The 5.1 spell line has no class list (the classes
are in other chapters), so these entries have none. A **condition** is a semi-bold heading
in the appendix "Conditions", followed by bullets.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.parse import SrdError, is_title
from dmbot.devtools.srd.pdf import Line

ITALIC_FONT = "Cambria-Italic"
LABEL_FONT = "Cambria-Bold"
LEAD_IN_FONT = "Cambria-BoldItalic"
BODY_FAMILY = "Cambria"
FIELDS = ("Casting Time", "Range", "Components", "Duration")
# The SRD's Contagion says "Component:" (as 5.2.1's Barkskin does): both are read.
LABELS = {"castingtime": "Casting Time", "range": "Range", "components": "Components",
          "component": "Components", "duration": "Duration"}  # fmt: skip
# The level line with every space taken out of it ("7 th - level illusion" and "7th-level
# illusion" are one thing), so stray spaces can't hide it.
LEVEL_LINE = re.compile(
    r"^(?P<level>\d)(?:st|nd|rd|th)-?level(?P<school>[a-z]+?)(?P<ritual>\(ritual\))?$"
)
CANTRIP_LINE = re.compile(r"^(?P<school>[a-z]+?)cantrip$")
INDENT = (4.0, 20.0)  # a wrapped value is set in from its label by about this much
WORD = re.compile(r"[A-Za-z’']+")
COMMON = 8  # words this PDF uses at least this often count as words
STRAY_WORDS = frozenset(["a", "i"])
SCHOOLS = (
    "abjuration conjuration divination enchantment evocation illusion necromancy transmutation"
)

_NOISE = re.compile(r"[\t\r\xa0]+")
_SOFT = re.compile(r"[\xad‐‑]")
_ORDINAL = re.compile(r"(?<=\d)(?:s t|n d|r d|t h)\b")  # "3r d" is "3rd"
_BEFORE_PUNCTUATION = re.compile(r"(?<=[A-Za-z0-9)”’]) ([.,;:])(?=\s|$)")  # "turn ," is "turn,"
_HYPHEN = re.compile(r"(?<=[A-Za-z0-9]) - ?(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])- (?=[a-z0-9])")


@dataclass(frozen=True, slots=True)
class Spell51:
    name: str
    level: int  # 0 for a cantrip
    school: str
    ritual: bool
    casting_time: str
    range: str
    components: str
    duration: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Condition51:
    name: str
    text: str
    page: int


def clean(text: str) -> str:
    """One line's words with the PDF's noise taken out and its hyphens closed up."""
    text = _SOFT.sub("", _NOISE.sub(" ", text))
    text = " ".join(text.split())
    return _HYPHEN.sub("-", text)


class Vocabulary:
    """The words the SRD uses, for putting back words that stray spaces have cut."""

    def __init__(self, known: Iterable[str], lines: Iterable[Line]) -> None:
        seen: Counter[str] = Counter()
        for line in lines:
            seen.update(w.lower() for w in WORD.findall(clean(line.text)))
        # A lone letter is not a word (but "a" and "I" are): "a s much" is "as much".
        self.words: set[str] = {w.lower() for w in known if len(w) > 1} | set(SCHOOLS.split())
        self.words |= STRAY_WORDS
        self.words |= {w for w, n in seen.items() if n >= COMMON and len(w) >= 3}

    def join(self, left: str, right: str) -> str | None:
        """`leftright` as one word, if that is a word and the two aren't both words."""
        whole = (left + right).lower()
        if whole in self.words and not (left.lower() in self.words and right.lower() in self.words):
            return left + right
        return None

    def tidy(self, text: str) -> str:
        """Text made of lines run together: hyphens closed up across the line breaks,
        ordinals whole ("3rd"), cut words joined again."""
        text = _ORDINAL.sub(lambda m: m.group().replace(" ", ""), _HYPHEN.sub("-", text))
        text = _BEFORE_PUNCTUATION.sub(r"\1", text)
        return " ".join(self._strays_joined(self.repair(text).split(" ")))

    def _strays_joined(self, tokens: list[str]) -> list[str]:
        """A lone letter next to a piece that isn't a word is the rest of that piece ("d
        ropping", "knocke d", "(includin g"). Between two words it is a gap in the text, and
        is left. Punctuation around a token stays where it is."""
        out: list[str] = []
        for i, token in enumerate(tokens):
            lead, core, trail = _split(token)
            after = tokens[i + 1] if i + 1 < len(tokens) else ""
            if len(core) == 1 and core.lower() not in STRAY_WORDS and not trail:
                next_lead, next_core, _ = _split(after)
                if self._piece(next_core) and not next_lead:
                    tokens[i + 1] = lead + core + after
                    continue
            if out and len(core) == 1 and core.lower() not in STRAY_WORDS and not lead:
                _, before_core, before_trail = _split(out[-1])
                if self._piece(before_core) and not before_trail:
                    out[-1] = out[-1] + token
                    continue
            out.append(token)
        return out

    def _piece(self, core: str) -> bool:
        """A bare run of letters that is not a word: the part of a cut one."""
        return core.isalpha() and core.lower() not in self.words

    def repair(self, text: str) -> str:
        """The text with cut words joined again."""
        tokens = text.split(" ")
        changed = True
        while changed:
            changed = False
            for i in range(len(tokens) - 1):
                left, right = tokens[i], tokens[i + 1]
                if not (left.isalpha() or left.replace("’", "").isalpha()):
                    continue  # only a bare word can be the front of a cut one
                match = WORD.match(right)
                if match is None or match.start() != 0:
                    continue
                core = match.group()
                joined = self.join(left, core)
                if joined is not None:
                    tokens[i : i + 2] = [joined + right[len(core) :]]
                    changed = True
                    break
        return " ".join(tokens)


def _split(token: str) -> tuple[str, str, str]:
    """(punctuation before, the letters, punctuation after) of a token; the letters are
    empty if it is anything but one run of letters with punctuation around it."""
    match = re.fullmatch(r"([^A-Za-z]*)([A-Za-z]*)([^A-Za-z]*)", token)
    if match is None:
        return "", "", token
    return match[1], match[2], match[3]


def strays(texts: Iterable[str]) -> Counter[str]:
    """Lone letters other than a and I, which mark words the PDF's text layer has lost."""
    found: Counter[str] = Counter()
    for text in texts:
        for token in re.findall(r"(?<![\w’'-])[A-Za-z](?![\w’'-])", text):
            if token.lower() not in STRAY_WORDS:
                found[token] += 1
    return found


def _paragraphs(lines: Sequence[Line], vocab: Vocabulary) -> str:
    """The description: lines run on into paragraphs. A new line starts at a bold-italic
    lead-in ("At Higher Levels."), at a bullet, and at each line of a table (a font that
    isn't the body's)."""
    rows: list[str] = []
    joinable = False  # the last row takes the next line as its continuation
    for line in lines:
        text = clean(line.text)
        if not text:
            continue
        font = line.first_font
        table = not (font.startswith(BODY_FAMILY) or font == "Symbol")
        fresh = not rows or table or not joinable or font == LEAD_IN_FONT or text.startswith("•")
        if fresh:
            rows.append(text)
        else:
            rows[-1] += " " + text
        joinable = not table
    return "\n".join(vocab.tidy(r) for r in rows)


def _label(line: Line) -> tuple[str, str] | None:
    """`("Components", "V, S")` from a labelled line, whatever stray spaces cut the label
    ("Component s:"); None if it isn't one."""
    head, colon, value = clean(line.text).partition(":")
    name = LABELS.get(head.replace(" ", "").lower()) if colon else None
    return (name, value.strip()) if name else None


def _kind(line: Line) -> tuple[int, str, bool] | None:
    """(level, school, ritual) from a spell's italic line; None if it isn't one."""
    compact = "".join(clean(line.text).lower().split())
    if match := LEVEL_LINE.match(compact):
        level, school, ritual = int(match["level"]), match["school"], bool(match["ritual"])
    elif match := CANTRIP_LINE.match(compact):
        level, school, ritual = 0, match["school"], False
    else:
        return None
    return (level, school.capitalize(), ritual) if school in SCHOOLS.split() else None


def _is_spell_start(lines: Sequence[Line], i: int) -> bool:
    return (
        is_title(lines[i])
        and lines[i + 1].starts_with_font(ITALIC_FONT)
        and _kind(lines[i + 1]) is not None
    )


def parse_spells(lines: Sequence[Line], vocab: Vocabulary) -> list[Spell51]:
    """Every spell in `lines` (the Spell Descriptions, in reading order)."""
    starts = [i for i in range(len(lines) - 1) if _is_spell_start(lines, i)]
    spells = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        spells.append(_spell(lines[start:end], vocab))
    return spells


def _spell(block: Sequence[Line], vocab: Vocabulary) -> Spell51:
    name, page = vocab.repair(clean(block[0].text)), block[0].page
    where = f"{name!r} on page {page}"
    kind = _kind(block[1])
    if kind is None:
        raise SrdError(f"Spell {where}: can't read the level line {clean(block[1].text)!r}")
    level, school, ritual = kind
    values: dict[str, str] = {}
    field_name, label_x = "", block[2].x if len(block) > 2 else 0.0
    i = 2
    while i < len(block):
        line = block[i]
        labelled = _label(line) if line.first_font == LABEL_FONT else None
        if labelled is not None:
            field_name, label_x = labelled[0], line.x
            values[field_name] = labelled[1]
        elif field_name and (
            field_name != "Duration" or INDENT[0] < line.x - label_x < INDENT[1]
        ):  # a value that wrapped: any line before the Duration, an indented one after it
            values[field_name] = f"{values[field_name]} {clean(line.text)}"
        else:
            break
        i += 1
    missing = [f for f in FIELDS if f not in values]
    if missing:
        raise SrdError(f"Spell {where}: no {', '.join(missing)}")
    text = _paragraphs(block[i:], vocab)
    if not text:
        raise SrdError(f"Spell {where}: no description")
    return Spell51(
        name,
        level,
        school,
        ritual,
        vocab.tidy(values["Casting Time"]),
        vocab.tidy(values["Range"]),
        vocab.tidy(values["Components"]),
        vocab.tidy(values["Duration"]),
        text,
        page,
    )


def parse_conditions(
    lines: Sequence[Line], vocab: Vocabulary, names: Sequence[str]
) -> list[Condition51]:
    """The appendix's conditions, in `lines`: each `names` heading and what follows it up
    to the next heading. A heading that isn't there stops the tool."""
    wanted = set(names)
    found: dict[str, Condition51] = {}
    i = 0
    while i < len(lines):
        title = clean(lines[i].text)
        if is_title(lines[i]) and title in wanted:
            end = i + 1
            while end < len(lines) and not is_title(lines[end]):
                end += 1
            text = _paragraphs(lines[i + 1 : end], vocab)
            if not text:
                raise SrdError(f"Condition {title!r} on page {lines[i].page}: no description")
            found[title] = Condition51(title, text, lines[i].page)
            i = end
        else:
            i += 1
    missing = [n for n in names if n not in found]
    if missing:
        raise SrdError(f"Can't find the condition(s): {', '.join(missing)}")
    return [found[n] for n in names]
