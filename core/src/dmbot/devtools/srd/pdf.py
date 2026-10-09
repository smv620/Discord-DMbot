"""Reading the SRD's PDF in the order a person reads it (#866).

The SRD is set in two columns, and a PDF's text is stored in the order it was drawn, not
the order it is read. So this keeps where each piece of text sits (and in which font) and
puts the pieces back in reading order: the left column from the top down, then the right.
Pure apart from `read_pages`, which opens the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


CHAR_WIDTH = 5.5  # more than any letter of the SRD's body or table fonts is wide, in points
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


def read_pages(path: str) -> list[list[Line]]:
    """Every page of the PDF at `path`, as lines in reading order."""
    from pypdf import PdfReader  # imported here: only this tool opens PDFs

    reader = PdfReader(path)
    pages: list[list[Line]] = []
    for number, page in enumerate(reader.pages, 1):
        pieces: list[Piece] = []

        def visit(
            text: str,
            cm: list[float],
            tm: list[float],
            font: dict[str, object] | None,
            _size: float,
            sink: list[Piece] = pieces,
        ) -> None:
            if not text.strip("\n"):
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            name = str((font or {}).get("/BaseFont", ""))
            sink.append(Piece(round(x, 1), round(y, 1), name, text))

        page.extract_text(visitor_text=visit)
        pages.append(lines_of(pieces, number))
    return pages
