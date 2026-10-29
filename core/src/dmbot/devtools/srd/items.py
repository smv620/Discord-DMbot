"""Turning the SRD 5.2.1's Magic Items A to Z chapter into items (#1912). Pure: no PDF, no files.

An item is a title in the semi-bold sans font at a column's edge, then one or two lines in the
italic serif font (`Wondrous Item, Rare (Requires Attunement)`), then its description in the serif
fonts. A title that wraps ("Amulet of Proof against Detection" / "and Location") is one heading.
A title with a table under it (`Apparatus of the Crab Levers`) is the table's caption; the
tables, and the stat blocks printed inside a description (set in the sans fonts), are left out as
the class tables were, because their cells do not read as sentences. When something doesn't look
as expected the tool stops and says where, instead of losing an item.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.devtools.srd.parse import (
    COLUMN_EDGES,
    EDGE_SLACK,
    Mender,
    SrdError,
    _clean,
    _paragraphs,
    is_title,
)
from dmbot.devtools.srd.pdf import Line

SAME_HEADING = 20.0  # title lines this close (points) with nothing between are one heading
TYPE_FONT = "Cambria-Italic"  # the category and rarity line under an item's name
# "(Requires Attunement)" or "(Requires Attunement by a Wizard)": the bracket may hold only this.
ATTUNEMENT = re.compile(r"\s*\(Requires Attunement(?: by (?P<by>[^)]+))?\)")
# The category and, if it has one, its bracket: "Armor (Any Light, Medium, or Heavy)", "Ring".
# The closed set of rarities the SRD prints, each with an optional note: "Uncommon (+1)", "Very
# Rare (Requires Attunement)" (taken out before this is matched). A type line that doesn't end
# in one of these is not complete, or is not a type line at all (#1912).
_RARITY = r"(?:Common|Uncommon|Rare|Very Rare|Legendary|Artifact)(?: \([^)]*\))?"
RARITIES = re.compile(rf"^(?:Rarity Varies|{_RARITY}(?:(?:, or |, | or ){_RARITY})*)$")
CATEGORY = re.compile(r"^(?P<category>[^(,]+?)(?: \((?P<subtype>[^)]*)\))?, (?P<rarity>.+)$")


@dataclass(frozen=True, slots=True)
class Item:
    name: str
    category: str  # "Armor", "Potion", "Ring", "Wondrous Item", ...
    subtype: str  # the category's bracket ("Any Ammunition"), or ""
    rarity: str  # as printed, with its notes: "Uncommon (+1), Rare (+2), or Very Rare (+3)"
    attunement: bool
    attunement_by: str  # "a Wizard" in "Requires Attunement by a Wizard", else ""
    text: str
    page: int


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


def _is_type_line(line: Line) -> bool:
    return line.first_font.endswith(TYPE_FONT) and _at_an_edge(line)


def _heading(lines: Sequence[Line], i: int) -> tuple[str, int]:
    """The words of the heading that starts at `lines[i]` and the index after it."""
    text = _clean(lines[i].text)
    j = i + 1
    while (
        j < len(lines)
        and is_title(lines[j])
        and _at_an_edge(lines[j])
        and abs(lines[j].x - lines[i].x) <= EDGE_SLACK
        and 0 < lines[j - 1].y - lines[j].y <= SAME_HEADING
    ):
        text += " " + _clean(lines[j].text)
        j += 1
    return text, j


def _complete(text: str, following: str) -> bool:
    """Does this read as a whole category and rarity line, with nothing of it on the next line?
    A line that ends open (an unclosed bracket, a comma, "or") or whose next line carries on
    ("(+2), or Very Rare (+3)") is not whole yet."""
    if text.count("(") != text.count(")") or text.endswith((",", " or")):
        return False
    if following.startswith(("(", ")", "or ")):
        return False
    match = CATEGORY.match(ATTUNEMENT.sub("", text).strip())
    return match is not None and RARITIES.match(match["rarity"]) is not None


def _type_lines(lines: Sequence[Line], start: int) -> tuple[str, int]:
    """The category and rarity line (it can wrap over two or three lines) and the index after
    it. It ends where it reads as complete: a description can begin in the same italic font."""
    j = start
    while j < len(lines) and _is_type_line(lines[j]):
        j += 1
        text = _clean(" ".join(line.text for line in lines[start:j]))
        following = _clean(lines[j].text) if j < len(lines) and _is_type_line(lines[j]) else ""
        if _complete(text, following):
            return text, j
    return _clean(" ".join(line.text for line in lines[start:j])), j


def parse_type(name: str, text: str, page: int) -> tuple[str, str, str, bool, str]:
    """(category, bracket, rarity, attunement, by whom) from a type line. Stops if the line is
    not in the shape the SRD uses."""
    attunement = ATTUNEMENT.search(text)
    plain = ATTUNEMENT.sub("", text).strip()
    match = CATEGORY.match(plain)
    if match is None or RARITIES.match(match["rarity"]) is None:
        raise SrdError(
            f"Item {name!r} on page {page}: can't read its category and rarity: {text!r}"
        )
    if re.search(r"requires attunement", plain, re.IGNORECASE):
        raise SrdError(f"Item {name!r} on page {page}: an attunement note it can't read: {text!r}")
    by = (attunement.group("by") or "") if attunement else ""
    return match["category"], match["subtype"] or "", match["rarity"], attunement is not None, by


def parse_items(lines: Sequence[Line]) -> list[Item]:
    """Every item in the lines of the Magic Items A to Z chapter, after its introduction."""
    mender = Mender(lines)
    items: list[Item] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (is_title(line) and _at_an_edge(line)):
            i += 1
            continue
        name, j = _heading(lines, i)
        if not (j < len(lines) and _is_type_line(lines[j])):
            # A table's caption or a stat block's name is followed by sans-font lines, and the
            # chapter's own heading comes before the first item. A title with serif words
            # under it and no category line is an item that would be lost.
            after = lines[j] if j < len(lines) else None
            if items and after is not None and after.first_font.startswith("Cambria"):
                raise SrdError(f"Item {name!r} on page {line.page} has no category and rarity line")
            i = j
            continue
        text, k = _type_lines(lines, j)
        category, subtype, rarity, attunement, by = parse_type(name, text, line.page)
        end = k
        while end < len(lines) and not _starts_an_item(lines, end):
            _check_not_an_item_without_a_line(lines, end)
            end += 1
        body = [ln for ln in lines[k:end] if ln.first_font.startswith("Cambria")]
        words = _paragraphs(body, mender)
        if not words:
            raise SrdError(f"Item {name!r} on page {line.page}: no description")
        if name in seen:
            raise SrdError(f"Item {name!r} comes twice (page {line.page})")
        seen.add(name)
        items.append(Item(name, category, subtype, rarity, attunement, by, words, line.page))
        i = end
    return items


def left_out(lines: Sequence[Line]) -> dict[str, int]:
    """Each item that has lines left out of its text (tables, stat blocks), with how many: the
    list ATTRIBUTION.md names, and a test pins, so a new drop can't pass unnoticed."""
    found: dict[str, int] = {}
    i = 0
    while i < len(lines):
        if not _starts_an_item(lines, i):
            i += 1
            continue
        name, j = _heading(lines, i)
        _, k = _type_lines(lines, j)
        end = k
        while end < len(lines) and not _starts_an_item(lines, end):
            end += 1
        dropped = sum(1 for ln in lines[k:end] if not ln.first_font.startswith("Cambria"))
        if dropped:
            found[name] = dropped
        i = end
    return found


def _starts_an_item(lines: Sequence[Line], i: int) -> bool:
    """True at a title that begins an item: its (possibly wrapped) heading, then a type line."""
    if not (is_title(lines[i]) and _at_an_edge(lines[i])):
        return False
    if i > 0 and is_title(lines[i - 1]) and _at_an_edge(lines[i - 1]):
        before = lines[i - 1]
        if abs(before.x - lines[i].x) <= EDGE_SLACK and 0 < before.y - lines[i].y <= SAME_HEADING:
            return False  # the second line of a heading that began above
    _, j = _heading(lines, i)
    return j < len(lines) and _is_type_line(lines[j])


def _check_not_an_item_without_a_line(lines: Sequence[Line], i: int) -> None:
    """Stops the tool at a title inside an item's description that has serif words under it and
    no category line: that is an item that would be lost. A table's caption and a stat block's
    name have sans-font lines under them instead."""
    if not (is_title(lines[i]) and _at_an_edge(lines[i])):
        return
    if i > 0 and is_title(lines[i - 1]) and 0 < lines[i - 1].y - lines[i].y <= SAME_HEADING:
        return  # the second line of a heading: the first one was checked
    name, j = _heading(lines, i)
    if j < len(lines) and lines[j].first_font.startswith("Cambria") and not _is_type_line(lines[j]):
        raise SrdError(f"Item {name!r} on page {lines[i].page} has no category and rarity line")
