"""Turning the SRD 5.2.1's feats, species and backgrounds into entries (#1913). Pure: no PDF, no
files.

All three are headed by a title in the semi-bold sans font at a column's edge, and what comes under
the title says which kind it is:
- a feat: one or two italic serif lines, `Origin Feat` or `Epic Boon Feat (Prerequisite: ...)`;
- a species: sans lines `Creature Type: Humanoid`, `Size: ...`, `Speed: 30 feet`, then the traits;
- a background: sans lines `Ability Scores: ...`, `Feat: ...`, `Skill Proficiencies: ...`,
  `Tool Proficiency: ...` and `Equipment: ...`, which are the whole description.
The tables inside a species (the Draconic Ancestors, the Elven Lineages, the Fiendish Legacies,
set in the sans fonts) are left out as the class tables were: their cells do not read as
sentences. When something doesn't look as expected the tool stops and says where, instead of
losing an entry.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.items import SAME_HEADING, _at_an_edge, _heading, _is_type_line
from dmbot.devtools.srd.parse import Mender, SrdError, _clean, _paragraphs, is_title
from dmbot.devtools.srd.pdf import Line

FEAT_LINE = re.compile(
    r"^(?P<category>Origin|General|Fighting Style|Epic Boon) Feat"
    r"(?: \(Prerequisite: (?P<prerequisite>.+)\))?$"
)
SPECIES_LABELS = ("Creature Type", "Size", "Speed")
BACKGROUND_LABELS = (
    "Ability Scores",
    "Feat",
    "Skill Proficiencies",
    "Tool Proficiency",
    "Equipment",
)
SEE_FEATS = re.compile(r"\s*\(see “Feats”\)$")  # the pointer after a background's feat


@dataclass(frozen=True, slots=True)
class Feat:
    name: str
    category: str  # "Origin", "General", "Fighting Style", "Epic Boon"
    prerequisite: str  # "" when there is none
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Species:
    name: str
    creature_type: str
    size: str
    speed: str
    text: str  # the traits, one paragraph each
    page: int


@dataclass(frozen=True, slots=True)
class Background:
    name: str
    ability_scores: str
    feat: str  # the Origin feat it gives, as named ("Magic Initiate (Cleric)")
    skills: str
    tool: str
    equipment: str
    text: str  # its lines as printed, one for each part
    page: int


def _label_of(text: str, labels: Sequence[str]) -> str | None:
    """The label a line starts with ("Size"), if it is one of `labels`."""
    head, colon, _ = text.partition(":")
    return head if colon and head in labels else None


def _is_label(line: Line, labels: Sequence[str]) -> bool:
    return is_title(line) and _at_an_edge(line) and _label_of(_clean(line.text), labels) is not None


def _name_of(lines: Sequence[Line], i: int, labels: Sequence[str]) -> tuple[str, int]:
    """The heading that starts at `lines[i]`, not taking in the label lines under it (they are
    set in the same font as the title)."""
    text = _clean(lines[i].text)
    j = i + 1
    while (
        j < len(lines)
        and is_title(lines[j])
        and _at_an_edge(lines[j])
        and not _is_label(lines[j], labels)
        and abs(lines[j].x - lines[i].x) <= 3.0
        and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
    ):
        text += " " + _clean(lines[j].text)
        j += 1
    return text, j


def _starts(lines: Sequence[Line], i: int, labels: Sequence[str]) -> bool:
    """A title at an edge whose next heading line is one of `labels`: a species or a background."""
    if not (is_title(lines[i]) and _at_an_edge(lines[i]) and not _is_label(lines[i], labels)):
        return False
    if i > 0 and is_title(lines[i - 1]) and 0 < lines[i - 1].y - lines[i].y <= 20:
        return False  # the second line of a heading that wrapped
    _, j = _name_of(lines, i, labels)
    return j < len(lines) and _is_label(lines[j], labels)


SECTION_HEADINGS = ("Background Descriptions", "Species Descriptions")  # no text of their own


def without_section_headings(lines: Sequence[Line]) -> list[Line]:
    """The lines without the two headings that only introduce the entries below them (they sit
    close enough above the first entry's name to be read as part of it)."""
    return [
        ln
        for ln in lines
        if not (is_title(ln) and _at_an_edge(ln) and _clean(ln.text) in SECTION_HEADINGS)
    ]


def _labelled(
    lines: Sequence[Line], start: int, labels: Sequence[str], what: str, mender: Mender
) -> tuple[dict[str, str], int]:
    """The labelled lines from `start` (a label line wraps onto sans lines set a little in) and
    the index after them."""
    found: dict[str, str] = {}
    label = ""
    j = start
    while j < len(lines) and not lines[j].first_font.startswith("Cambria"):
        line = lines[j]
        if is_title(line) and not _is_label(line, labels):
            break
        text = _clean(line.text)
        if _is_label(line, labels):
            label = _label_of(text, labels) or ""
            if label in found:
                raise SrdError(f"{what}: the line {label!r} comes twice (page {line.page})")
            found[label] = text.partition(":")[2].strip()
        elif label:
            if found[label].endswith("-"):  # a word cut at the line's end
                found[label] = mender.join(found[label], text)
            else:
                found[label] += " " + text  # a wrapped line
        j += 1
    return found, j


def _block_end(lines: Sequence[Line], start: int, labels: Sequence[str]) -> int:
    end = start
    while end < len(lines) and not _starts(lines, end, labels):
        end += 1
    return end


def parse_species(lines: Sequence[Line]) -> list[Species]:
    """Every species in the lines between the Character Origins heading and the Feats chapter."""
    lines = without_section_headings(lines)
    mender = Mender(lines)
    out: list[Species] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines) and not _starts(lines, i, SPECIES_LABELS):
        i += 1  # the chapter's introduction, and the backgrounds
    while i < len(lines):
        name, j = _name_of(lines, i, SPECIES_LABELS)
        parts, k = _labelled(lines, j, SPECIES_LABELS, f"Species {name!r}", mender)
        for label in ("Size", "Speed"):
            if not parts.get(label):
                raise SrdError(f"Species {name!r} on page {lines[i].page} has no {label}")
        end = _block_end(lines, k, SPECIES_LABELS)
        _no_lost_headings(lines[k:end], f"Species {name!r}")
        body = [ln for ln in lines[k:end] if ln.first_font.startswith("Cambria")]
        text = _paragraphs(body, mender)
        if not text:
            raise SrdError(f"Species {name!r} on page {lines[i].page}: no traits")
        if name in seen:
            raise SrdError(f"Species {name!r} comes twice (page {lines[i].page})")
        seen.add(name)
        out.append(
            Species(
                name, parts.get("Creature Type", ""), parts["Size"], parts["Speed"], text,
                lines[i].page,
            )
        )  # fmt: skip
        i = end
    return out


def _no_lost_headings(
    block: Sequence[Line], what: str, within: Sequence[Line] | None = None, at: int = 0
) -> None:
    """Inside a species or a feat, a title with serif words under it would be a lost heading; a
    table's caption has sans lines under it. With `within`, only the line at `within[at]` is
    checked (a feat's body is scanned line by line to find its end)."""
    scan = within if within is not None else block
    indexes = [at] if within is not None else range(len(scan))
    for i in indexes:
        line = scan[i]
        if not (is_title(line) and _at_an_edge(line)):
            continue
        if i > 0 and is_title(scan[i - 1]) and 0 < scan[i - 1].y - line.y <= SAME_HEADING:
            continue
        _, j = _heading(scan, i)
        if j < len(scan) and scan[j].first_font.startswith("Cambria"):
            raise SrdError(f"{what}: the heading {_clean(line.text)!r} has text under it")


def parse_backgrounds(lines: Sequence[Line]) -> list[Background]:
    """Every background in the lines between the Character Origins heading and the Feats chapter."""
    lines = without_section_headings(lines)
    mender = Mender(lines)
    out: list[Background] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        if not _starts(lines, i, BACKGROUND_LABELS):
            i += 1
            continue
        name, j = _name_of(lines, i, BACKGROUND_LABELS)
        parts, k = _labelled(lines, j, BACKGROUND_LABELS, f"Background {name!r}", mender)
        missing = [label for label in BACKGROUND_LABELS if not parts.get(label)]
        if missing:
            raise SrdError(f"Background {name!r} on page {lines[i].page} has no {missing[0]}")
        if name in seen:
            raise SrdError(f"Background {name!r} comes twice (page {lines[i].page})")
        seen.add(name)
        text = "\n".join(f"{label}: {parts[label]}" for label in BACKGROUND_LABELS)
        feat = SEE_FEATS.sub("", parts["Feat"])
        out.append(
            Background(
                name, parts["Ability Scores"], feat, parts["Skill Proficiencies"],
                parts["Tool Proficiency"], parts["Equipment"], text, lines[i].page,
            )
        )  # fmt: skip
        i = k
    return out


FEAT_CATEGORY_HEADINGS = (
    "Origin Feats",
    "General Feats",
    "Fighting Style Feats",
    "Epic Boon Feats",
)


def parse_feats(lines: Sequence[Line]) -> list[Feat]:
    """Every feat in the lines of the Feats chapter, after its introduction. The four category
    headings ("Origin Feats", ...) are not feats; each feat names its own category."""
    lines = [
        ln
        for ln in lines
        if not (is_title(ln) and _at_an_edge(ln) and _clean(ln.text) in FEAT_CATEGORY_HEADINGS)
    ]
    mender = Mender(lines)
    out: list[Feat] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (is_title(line) and _at_an_edge(line)):
            i += 1
            continue
        name, j = _heading(lines, i)
        if not (j < len(lines) and _is_type_line(lines[j])):
            after = lines[j] if j < len(lines) else None
            if out and after is not None and after.first_font.startswith("Cambria"):
                raise SrdError(f"Feat {name!r} on page {line.page} has no category line")
            i = j  # the chapter's introduction, or a category's name ("Origin Feats")
            continue
        text, k = _type_lines_of_a_feat(lines, j)
        match = FEAT_LINE.match(text)
        if match is None:
            raise SrdError(f"Feat {name!r} on page {line.page}: can't read its category: {text!r}")
        end = k
        while end < len(lines) and not _starts_a_feat(lines, end):
            _no_lost_headings(lines[end : end + 1], f"Feat {name!r}", lines, end)
            end += 1
        body = [ln for ln in lines[k:end] if ln.first_font.startswith("Cambria")]
        words = _paragraphs(body, mender)
        if not words:
            raise SrdError(f"Feat {name!r} on page {line.page}: no description")
        if name in seen:
            raise SrdError(f"Feat {name!r} comes twice (page {line.page})")
        seen.add(name)
        out.append(Feat(name, match["category"], match["prerequisite"] or "", words, line.page))
        i = end
    return out


def _type_lines_of_a_feat(lines: Sequence[Line], start: int) -> tuple[str, int]:
    """The category line (it can wrap) and the index after it: it ends where it reads whole,
    because the description can open in the same italic font."""
    j = start
    while j < len(lines) and _is_type_line(lines[j]):
        j += 1
        text = _clean(" ".join(line.text for line in lines[start:j]))
        if FEAT_LINE.match(text):
            return text, j
    return _clean(" ".join(line.text for line in lines[start:j])), j


def _starts_a_feat(lines: Sequence[Line], i: int) -> bool:
    if not (is_title(lines[i]) and _at_an_edge(lines[i])):
        return False
    before = lines[i - 1] if i > 0 else None
    if (
        before is not None
        and is_title(before)
        and _at_an_edge(before)
        and 0 < before.y - lines[i].y <= SAME_HEADING
    ):
        return False  # the second line of a heading that wrapped
    _, j = _heading(lines, i)
    return j < len(lines) and _is_type_line(lines[j])


__all__ = ["Background", "Feat", "Species", "parse_backgrounds", "parse_feats", "parse_species"]
