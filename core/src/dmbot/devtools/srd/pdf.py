"""Reading the SRD's PDF in the order a person reads it (#866).

The SRD is set in two columns, and a PDF's text is stored in the order it was drawn, not
the order it is read. So this keeps where each piece of text sits (and in which font) and
puts the pieces back in reading order: the left column from the top down, then the right.
Pure apart from `read_pages`, which opens the file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# Where the columns split on the SRD's pages (594 points wide), and where the running
# header and footer ("System Reference Document 5.2.1", the page number) sit.
COLUMN_SPLIT = 300.0
FOOTER_Y = 40.0
SAME_LINE = 1.0  # pieces whose heights differ by less than this are on one line


@dataclass(frozen=True, slots=True)
class Piece:
    """A run of text in one font at one place on the page."""

    x: float
    y: float
    font: str
    text: str


@dataclass(slots=True)
class Line:
    """One printed line: its pieces left to right, with a place and the fonts used."""

    x: float
    y: float
    pieces: list[Piece] = field(default_factory=list)
    page: int = 0  # the page it is printed on (the SRD numbers its pages from 1)

    @property
    def text(self) -> str:
        """The line's words. Pieces drawn from one starting point share its x; where the
        next one starts further on than all of that text could reach, they are two columns
        of a table with no space between them in the PDF ("Temperature" ... "Wind"), and a
        space is put in."""
        out = ""
        origin: float | None = None
        drawn = ""  # the text drawn from `origin` so far
        previous: Piece | None = None
        for piece in self.pieces:
            if origin is not None and piece.x != origin:
                if _column_gap(drawn, previous, piece, piece.x - origin):
                    out += " "
                origin, drawn = piece.x, ""
            elif origin is None:
                origin = piece.x
            drawn += piece.text
            out += piece.text.replace("\n", "")
            previous = piece
        return out

    @property
    def first_text(self) -> str:
        """The words of the first piece that has any."""
        return next((p.text.strip() for p in self.pieces if p.text.strip()), "")

    @property
    def fonts(self) -> list[str]:
        """The fonts of its pieces with text, without the PDF's subset prefix."""
        return [p.font.split("+")[-1] for p in self.pieces if p.text.strip()]

    @property
    def first_font(self) -> str:
        fonts = self.fonts
        return fonts[0] if fonts else ""

    def starts_with_font(self, name: str) -> bool:
        return self.first_font == name


# An estimate of how far a character of the SRD's body or table fonts reaches, in points
# (an average, with room to spare: capitals are wider). Only the gap between two columns
# of a table has to clear it plus GAP_SLACK, and this PDF's byte-for-byte output is the test.
CHAR_WIDTH = 5.5
GAP_SLACK = 12.0


def _column_gap(drawn: str, previous: Piece | None, right: Piece, distance: float) -> bool:
    """`right` starts `distance` from where `drawn` began and neither side has a space at
    the join, yet they are two columns, not one word in two fonts: it starts further on
    than `drawn`'s words could reach, or it is in the same font as the piece before it
    (one word is not drawn twice in one font from two places)."""
    # A piece may end with the line break the PDF draws after it even when another column
    # follows on the same line, so only a real space counts.
    if drawn.rstrip("\n").endswith(" ") or right.text.startswith(" "):
        return False
    if previous is not None and previous.font == right.font:
        return True
    return distance > len(drawn.strip()) * CHAR_WIDTH + GAP_SLACK


def lines_of(pieces: list[Piece], page: int = 0) -> list[Line]:
    """Pieces in reading order, as lines: left column top to bottom, then the right."""
    columns: list[list[Piece]] = [[], []]
    for piece in pieces:
        if piece.y < FOOTER_Y or not piece.text.strip("\n"):
            continue  # the running header and footer
        columns[0 if piece.x < COLUMN_SPLIT else 1].append(piece)
    out: list[Line] = []
    for column in columns:
        column.sort(key=lambda p: -p.y)
        current: Line | None = None
        for piece in column:
            if current is None or abs(current.y - piece.y) >= SAME_LINE:
                current = Line(piece.x, piece.y, page=page)
                out.append(current)
            current.pieces.append(piece)
    for line in out:  # a piece can sit a hair higher than the rest of its line
        line.pieces.sort(key=lambda p: p.x)
        line.x = line.pieces[0].x
    return out


@dataclass(frozen=True, slots=True)
class Char:
    """One character as the PDF reader reports it: its font ("" for a space the reader made up
    between two pieces), where its baseline starts, and where its box begins and ends."""

    text: str
    font: str
    x: float
    y: float
    left: float
    right: float


# The text columns' left edges on the SRD's pages, and how far a paragraph's first line is
# indented from them (the PDF draws the indent as a gap, not as a character).
COLUMN_EDGES = (63.0, 313.5)
INDENT = 8.0
INDENT_SLACK = 1.0
SPACE_GAP = 1.5  # a gap this wide (points) between two pieces of one line is a space
DIGIT_GAP = 0.3  # ...and this wide between a letter and a number ("Con" "22" in the ability table)
LINE_END_HYPHEN = re.compile(r"([A-Za-z0-9])(-[\xad\u2010\u2011]*)$")
SENTENCE_END = tuple('.:!?)”"')
SOFT_HYPHEN = "\ufffe"  # what pdfium reports for the hyphen the typesetter added at a line's end


def pieces_of(chars: Iterable[Char]) -> list[Piece]:
    """The pieces of one page, from its characters in the order the PDF draws them. A piece is
    a run in one font on one line, as the parsers expect (they were written for it). Two things
    the PDF leaves out are put back: the space between pieces that sit apart, and the leading
    space of a paragraph's first line (its indent), which the parsers use to find paragraphs."""
    pieces: list[Piece] = []
    current: list[Any] | None = None  # x, y, font, text, x of its last character
    lead = ""  # an indent seen before the next piece's first character
    at_line_start = True
    in_lead_in = False  # the line at the column's edge began with a bold-italic lead-in
    previous_right = 0.0

    def close(ending: str = "") -> None:
        nonlocal current
        if current is not None:
            body = current[3]
            if ending:
                # A hyphen that ends a line is drawn apart from its word, and the parsers read
                # it as " -" (the 5.1 reader tells "long-dead" from "immedi-ately" by it).
                body = LINE_END_HYPHEN.sub(r"\1 \2", body)
            pieces.append(
                Piece(round(current[0], 1), round(current[1], 1), current[2], body + ending)
            )
        current = None

    for char in chars:
        text = "-" if char.text == SOFT_HYPHEN else char.text
        if text in ("\r", "\n"):
            close("\n")
            lead, at_line_start = "", True
            continue
        if not char.font:  # a space made up between two pieces
            if current is not None:
                current[3] += text
            elif text == " ":
                lead = " "
            continue
        if (
            current is None
            or char.font != current[2]
            or abs(char.y - current[1]) > SAME_LINE / 2
            or char.x < current[4] - 2
        ):
            close()
            before = pieces[-1] if pieces else None
            current = [char.x, char.y, char.font, lead, char.x]
            lead = ""
            if at_line_start:
                at_edge = any(abs(char.left - edge) <= 1.5 for edge in COLUMN_EDGES)
                if at_edge:
                    in_lead_in = "BoldItalic" in char.font
                elif (
                    not in_lead_in
                    and any(abs(char.left - edge - INDENT) <= INDENT_SLACK for edge in COLUMN_EDGES)
                    and before is not None
                    and (
                        before.text.rstrip("\n").rstrip().endswith(SENTENCE_END)
                        or "SemiBold" in before.font
                        or "GillSans" in before.font
                    )
                ):
                    current[3] = " "  # an indented first line starts a paragraph
                at_line_start = False
            if (
                before is not None
                and abs(before.y - char.y) <= SAME_LINE / 2
                and not text.isspace()
                and not before.text.endswith((" ", "\n"))
                and (
                    char.left - previous_right >= SPACE_GAP
                    or (
                        text.isdigit()
                        and before.text[-1:].isalpha()
                        and char.left - previous_right >= DIGIT_GAP
                    )
                )
            ):
                current[3] = " " + current[3]
        current[3] += text
        current[4] = char.x
        previous_right = char.right
    close()
    return pieces


def read_pages(path: str) -> list[list[Line]]:
    """Every page of the PDF at `path`, as lines in reading order. Read with pypdfium2 (the
    `srd` extra): pypdf, and pdfminer.six, each leave out parts of this PDF's text (the
    ability table's Dex, Con, Wis and Cha labels, some numbers, an equals sign), the same
    on every version tried (#1892)."""
    import ctypes

    import pypdfium2 as pdfium  # imported here: only this tool opens PDFs
    import pypdfium2.raw as raw

    document = pdfium.PdfDocument(path)
    name = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    origin_x, origin_y = ctypes.c_double(), ctypes.c_double()
    pages: list[list[Line]] = []
    for number in range(len(document)):
        textpage = document[number].get_textpage()
        chars: list[Char] = []
        for index, text in enumerate(textpage.get_text_range()):
            raw.FPDFText_GetFontInfo(textpage, index, name, len(name), ctypes.byref(flags))
            raw.FPDFText_GetCharOrigin(
                textpage, index, ctypes.byref(origin_x), ctypes.byref(origin_y)
            )
            left, _bottom, right, _top = textpage.get_charbox(index)
            font = name.value.decode("latin1") if text not in "\r\n" else ""
            chars.append(Char(text, font, origin_x.value, origin_y.value, left, right))
        pages.append(lines_of(pieces_of(chars), number + 1))
    return pages
