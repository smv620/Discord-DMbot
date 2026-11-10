"""The words read from a document's pictures, handed to the lore read (#4003; CloudDev-lead's shape,
agreed on the issue 2026-11-10). Pure: no database, no AI, no image.

CloudDev-lead's half finds a PDF's picture pages, reads each picture with a vision model and gives
one `PictureText` per picture, in page order. This half reads those words through the same lore
reader as the document's text (an extra page, behind `DMBOT_PICTURE_NAMES`) and checks every name
it gives against the document's own names (`picture_names.check`, Supervisor's ruling):
- **Matched**: the document's spelling, or written word for word in its text: kept under that
  spelling, so it joins the text's own name instead of making a second one. Only the name: the
  picture read's other spellings, quote and line are dropped (they are unchecked words from a
  picture, and a quote must come from the document's text). A name the campaign already has never
  reaches here: the read skips it, as it does in the text.
- **Corrected**: a plain misreading of exactly one of the document's names: it IS that name, so
  nothing new is kept; counted for the upload's notice.
- **ToCheck**: anything else. Never added as it reads: held for the DM's Names to check, with a link
  to the picture when it was kept (`picture_id`). Showing them is the next slice: until it ships,
  DMBOT_PICTURE_NAMES stays off.

The pictures' words go only to the lore reader and are never logged: logs carry counts only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from dmbot.memory import background as bg
from dmbot.memory.picture_names import Corrected, Known, Matched, ToCheck, check, written_in

MAX_PICTURES = 200  # pictures read from one upload; past it, the first ones in page order
WHERE = ("map", "table", "handout")


@dataclass(frozen=True, slots=True)
class PictureText:
    """One picture's words as the vision read gave them. `page`: the PDF page (1-based);
    `picture_id`: the kept picture's opaque id for the DM's link ("" when not kept; never a path or
    an address); `text`: its words, a table row by row, a map's labels one per line; `where`: "map",
    "table", "handout", or "" when the read can't say."""

    page: int
    picture_id: str
    text: str
    where: str = ""


@dataclass(frozen=True, slots=True)
class Held:
    """A name read from a picture that the document does not plainly resolve, for the DM."""

    found: bg.Found
    check: ToCheck
    page: int
    picture_id: str


@dataclass(slots=True)
class Sorted:
    """What became of the names the pictures' read gave (see the module's note)."""

    kept: list[bg.Found] = field(default_factory=list)  # (matched: under the right spelling)
    corrected: Counter[str] = field(default_factory=Counter)  # (by test: slip, cut, initials)
    held: list[Held] = field(default_factory=list)


def as_pages(pictures: Sequence[PictureText]) -> str:
    """The pictures' words as the extra pages the lore reader reads, one block a picture, in page
    order. The heading says what each is, so the reader treats a map's labels as places and a
    table's rows as rows; it holds no id."""
    blocks = []
    for picture in sorted(pictures, key=lambda p: p.page)[:MAX_PICTURES]:
        words = picture.text.strip()
        if not words:
            continue
        what = picture.where if picture.where in WHERE else "picture"
        blocks.append(f"[A {what} on page {picture.page}]\n{words}")
    return "\n\n".join(blocks)


def sort(
    found: Sequence[bg.Found],
    document: Sequence[bg.Found],
    text: str,
    pictures: Sequence[PictureText],
) -> Sorted:
    """The names the pictures' read gave (`found`), checked against the document's own names
    (`document`, as its text read gave them: one reader, so their kinds agree) and its text. A
    held name carries the first picture whose words hold it, for the DM's link. Pure."""
    own = [Known(f.name, f.kind) for f in document] + [
        Known(a, f.kind) for f in document for a in f.also
    ]
    out = Sorted()
    for f in found:
        verdict = check(f.name, f.kind, own, text=text)
        if isinstance(verdict, Matched):
            # (the name only: nothing else a picture read gave is checked)
            out.kept.append(replace(f, name=verdict.name, also=(), secret=(), quote="", about=""))
        elif isinstance(verdict, Corrected):
            out.corrected[verdict.how] += 1
        else:
            page, picture_id = _where_from(f.name, pictures)
            out.held.append(Held(f, verdict, page, picture_id))
    return out


def _where_from(name: str, pictures: Sequence[PictureText]) -> tuple[int, str]:
    """The first picture whose words hold the name as whole words (case aside), or (0, "") when none
    does: a short name never links the picture of a longer one ("Kal" is not in "Kaltro")."""
    for picture in sorted(pictures, key=lambda p: p.page):
        if written_in(name, picture.text) is not None:
            return picture.page, picture.picture_id
    return 0, ""


__all__ = ["MAX_PICTURES", "Held", "PictureText", "Sorted", "as_pages", "sort"]
