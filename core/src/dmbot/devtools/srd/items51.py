"""Turning the 2014 SRD 5.1's Magic Items chapter into items (#1920). Pure: no PDF, no files.

An item is a title in the semi-bold sans font at a column's edge, then one or two lines in the
italic serif font in lower case (`Wondrous item, uncommon (requires attunement)`), then its
description in the serif fonts. The tables in an item (set in the Calibri fonts) and the stat
blocks are left out, as `items` does for the 5.2.1, because their cells do not read as
sentences. The entries have the shape `items` gives the 5.2.1's (category and rarity in the
5.2.1's capitals), so the index reads both alike. When something doesn't look as expected the
tool stops and says where, instead of losing an item.

What differs from `items`: the 5.1 column edges, lower-case type lines, three rarities the
5.1 prints in words ("rarity varies", "rarity by figurine", "varies"), paragraphs that start
with an indent (and bullets whose own lines are indented), and the words mended with the 5.1
reader's vocabulary because its text layer cuts words.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from dmbot.devtools.srd.items import Item
from dmbot.devtools.srd.parse import SrdError, is_title
from dmbot.devtools.srd.parse51 import Vocabulary, clean
from dmbot.devtools.srd.pdf import Line

# Where a column's text starts on the 5.1's two-column pages (measured from the PDF; the 5.2.1
# reader's edges are 63 and 314). A title or a type line sits there, a paragraph indent does not.
COLUMN_EDGES = (57.6, 328.6)
SAME_HEADING = 20.0  # title lines this close (points) with nothing between are one heading
EDGE_SLACK = 3.0
# An indented line only starts a paragraph when the line above ended a sentence: the Figurine of
# Wondrous Power's paragraphs are all set in, and a wrapped line there is indented too.
SENTENCE_END = (".", "!", "?", ":", ")", "”")
SHORT_BULLET = 30  # a bullet line shorter than this (in characters) did not fill its line
INDENT = 5.0  # a line set in by about this much starts a paragraph (a bullet's wrap sits there too)
TYPE_FONT = "Cambria-Italic"  # the category and rarity line under a name (also used in prose)
BODY_FAMILY = "Cambria"
BULLET_FONT = "Symbol"
LEAD_INS = ("Cambria-Bold", "Cambria-BoldItalic")
# "(requires attunement)", "(requires attunement by a wizard)": the bracket holds only this. The
# Ring of Shooting Stars adds when ("outdoors at night"): the description says it again.
ATTUNEMENT = re.compile(
    r"\s*\(requires attunement(?: by (?P<by>[^)]+)| outdoors at night)?\)", re.IGNORECASE
)
_RARITY = r"(?:common|uncommon|rare|very rare|legendary|artifact)(?: \([^)]*\))?"
# The closed set of rarities the 5.1 prints; a type line that doesn't end in one is not complete,
# or is not a type line at all. Three are words rather than a grade (Figurine of Wondrous Power,
# Potion of Healing, Spell Scroll).
RARITIES = re.compile(
    rf"^(?:rarity varies|rarity by figurine|varies|{_RARITY}(?:(?:, or |, | or ){_RARITY})*)$"
)
CATEGORY = re.compile(r"^(?P<category>[^(,]+?)(?: \((?P<subtype>[^)]*)\))?, (?P<rarity>.+)$")
# Rarities the 5.1 prints in words instead of a grade; all three mean "no one rarity" (the Spell
# Scroll's bare "varies" is "rarity varies"), and are written as the 5.2.1 writes its own.
_WORDS = ("rarity by figurine", "rarity varies", "varies")
_GRADE = re.compile(r"\b(common|uncommon|rare|very rare|legendary|artifact)\b")


def _at_an_edge(line: Line) -> bool:
    return any(abs(line.x - edge) <= EDGE_SLACK for edge in COLUMN_EDGES)


def _is_type_line(line: Line) -> bool:
    return line.first_font.endswith(TYPE_FONT) and _at_an_edge(line)


def _continues_heading(lines: Sequence[Line], i: int) -> bool:
    """Is `lines[i]` the second line of a wrapped heading ("Amulet of Proof against Detection
    and" / "Location")?"""
    return (
        i > 0
        and is_title(lines[i])
        and is_title(lines[i - 1])
        and abs(lines[i].x - lines[i - 1].x) <= EDGE_SLACK
        and 0 < lines[i - 1].y - lines[i].y <= SAME_HEADING
    )


def _heading(lines: Sequence[Line], i: int) -> tuple[str, int]:
    """The words of the heading that starts at `lines[i]` and the index after it."""
    text = clean(lines[i].text)
    j = i + 1
    while j < len(lines) and _continues_heading(lines, j):
        text += " " + clean(lines[j].text)
        j += 1
    return text, j


def _starts_an_item(lines: Sequence[Line], i: int) -> bool:
    """True at a title that begins an item: its (possibly wrapped) heading, then a type line."""
    if not (is_title(lines[i]) and _at_an_edge(lines[i])) or _continues_heading(lines, i):
        return False
    _, j = _heading(lines, i)
    return j < len(lines) and _is_type_line(lines[j])


def _complete(text: str, following: str) -> bool:
    """Does this read as a whole category and rarity line, with nothing of it on the next line?"""
    if text.count("(") != text.count(")") or text.endswith((",", " or")):
        return False
    if following.startswith(("(", ")", "or ", "rare", "by ")):
        return False
    match = CATEGORY.match(ATTUNEMENT.sub("", text).strip())
    return match is not None and RARITIES.match(match["rarity"]) is not None


def _type_lines(lines: Sequence[Line], start: int) -> tuple[str, int]:
    """The category and rarity line (it can wrap over two or three lines) and the index after
    it. It ends where it reads as complete: a description can begin in the same italic font."""
    j = start
    while j < len(lines) and _is_type_line(lines[j]):
        j += 1
        text = clean(" ".join(line.text for line in lines[start:j]))
        following = clean(lines[j].text) if j < len(lines) and _is_type_line(lines[j]) else ""
        if _complete(text, following):
            return text, j
    return clean(" ".join(line.text for line in lines[start:j])), j


def _capitalised(rarity: str) -> str:
    """The 5.1's lower-case rarity as the 5.2.1 prints it: "uncommon (+1), rare (+2), or very
    rare (+3)" is "Uncommon (+1), Rare (+2), or Very Rare (+3)". A bracket's words stay."""
    if rarity in _WORDS:  # ("varies" alone is the Spell Scroll's: the same as "rarity varies")
        return "Rarity by Figurine" if "figurine" in rarity else "Rarity Varies"
    out, depth = [], 0
    for part in re.split(r"(\(|\))", rarity):
        depth += part == "("
        out.append(part if depth else _GRADE.sub(lambda m: m.group().title(), part))
        depth -= part == ")"
    return "".join(out)


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
    subtype = match["subtype"] or ""
    return (
        match["category"].title(),
        subtype[:1].upper() + subtype[1:],
        _capitalised(match["rarity"]),
        attunement is not None,
        by,
    )


def _is_body(line: Line) -> bool:
    return line.first_font.startswith((BODY_FAMILY, BULLET_FONT))


def _paragraphs(lines: Sequence[Line], vocab: Vocabulary) -> str:
    """The description. A line starts a paragraph when it is set in (unless the paragraph is a
    bullet, whose wrapped lines are set in too), starts in bold, is a bullet, or comes after
    lines that were left out (a table or a stat block ended the paragraph above). A paragraph that
    follows a bullet list is set in just as a bullet's wrapped line is, so it is told apart by
    the line above it: a finished sentence, or a bullet too short to have wrapped."""
    rows: list[str] = []
    previous = ""
    bullet = False
    skipped = True  # lines were left out just before this one
    for line in lines:
        if not _is_body(line):
            skipped = True
            continue
        text = clean(line.text)
        if not text:
            continue
        edge = min(COLUMN_EDGES, key=lambda e: abs(e - line.x))
        indented = line.x - edge > INDENT
        # A stat line ("Armor Class: 20") hangs like a bullet; an indented bold lead-in
        # ("Absorption (Very Rare). ...") is an ordinary paragraph.
        starts_bullet = line.first_font == BULLET_FONT or text.startswith("•")
        stat_line = line.first_font == "Cambria-Bold" and not indented and ":" in text[:25]
        starts_bullet = starts_bullet or stat_line
        ends_bullet = (
            bullet
            and indented
            and text[:1].isupper()
            and (previous.endswith(SENTENCE_END) or len(previous) < SHORT_BULLET)
        )
        fresh = (
            not rows
            or skipped
            or starts_bullet
            or line.first_font in LEAD_INS
            or (indented and not bullet and previous.endswith(SENTENCE_END))
            or ends_bullet
            or (bullet and not indented)
        )
        if fresh:
            rows.append(text)
            bullet = starts_bullet
        else:
            rows[-1] = vocab.run_on(rows[-1], text)
        skipped = False
        previous = text
    return "\n".join(vocab.tidy(r) for r in rows)


def parse_items(lines: Sequence[Line], vocab: Vocabulary) -> list[Item]:
    """Every item in the lines of the Magic Items chapter, up to its section on sentient items."""
    items: list[Item] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _starts_an_item(lines, i):
            i += 1
            continue
        name, j = _heading(lines, i)
        name = vocab.repair(name)
        text, k = _type_lines(lines, j)
        category, subtype, rarity, attunement, by = parse_type(name, text, line.page)
        end = k
        while end < len(lines) and not _starts_an_item(lines, end):
            _check_not_an_item_without_a_line(lines, end)
            end += 1
        words = _paragraphs(lines[k:end], vocab)
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
        dropped = sum(1 for ln in lines[k:end] if not _is_body(ln))
        if dropped:
            found[name] = dropped
        i = end
    return found


def _check_not_an_item_without_a_line(lines: Sequence[Line], i: int) -> None:
    """Stops the tool at a title inside an item's description that has serif words under it and
    no type line: that is an item that would be lost. A table's caption has sans lines under it."""
    if not (is_title(lines[i]) and _at_an_edge(lines[i])) or _continues_heading(lines, i):
        return
    name, j = _heading(lines, i)
    if (
        j < len(lines)
        and lines[j].first_font.startswith(BODY_FAMILY)
        and not _is_type_line(lines[j])
    ):
        raise SrdError(f"Item {name!r} on page {lines[i].page} has no type line")
