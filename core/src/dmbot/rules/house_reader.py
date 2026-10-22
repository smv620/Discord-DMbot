"""Reading a house-rules file of any common shape into text (#2377). No Discord, no AI.

An upload or a linked file can be plain text or Markdown, a PDF (its text layer only), a Word
(.docx) or Excel (.xlsx) file, a CSV, or a web page; a Google Sheet arrives as CSV. Every format
is read by code into plain text, with the limits the names reader already has (10 MB, 200,000
characters), by libraries that need no network and no program on the server: `pypdf`, and the
standard library's `zipfile` and `csv` (a Word or Excel file is read without an XML parser, so
nothing inside is run or fetched). PDF, Word and Excel files are read in a child process with a
time limit (`document_reader`), so a hostile file cannot hold up the bot.

A file with no text (a scanned PDF) or an empty one raises a `DocumentError` in plain words.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import PurePath

from dmbot.memory.document_reader import read_document
from dmbot.memory.name_documents import (
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_CHARS,
    NO_TEXT,
    SCANNED_PDF,
    TYPES_HELP,
    DocumentError,
    decode_text,
    text_of,
)

CELL_JOIN = " | "  # between a row's filled cells, as in an Excel file's text
MAX_CSV_ROWS = 20_000


def csv_text(raw: bytes, max_chars: int = MAX_DOCUMENT_CHARS) -> str:
    """A CSV file's rows, one a line, each row's filled cells joined with `|`."""
    reader = csv.reader(io.StringIO(decode_text(raw), newline=""))
    lines: list[str] = []
    size = 0
    try:
        for number, row in enumerate(reader):
            cells = [" ".join(c.split()) for c in row]
            line = CELL_JOIN.join(c for c in cells if c)
            if line:
                lines.append(line)
                size += len(line)
            if number >= MAX_CSV_ROWS or size > max_chars:
                break
    except csv.Error as exc:
        raise DocumentError(
            "DMbot couldn't read that CSV file. Save it again as a CSV or a text file."
        ) from exc
    return "\n".join(lines)


SCANNED = (
    "That PDF is pictures of pages (scanned), so I can't read its words. Save or export it as a "
    "PDF with real text, or paste the rules into a Word or text file, and try again."
)
TYPES = (
    "I can read PDF, Word (.docx), Excel (.xlsx), CSV, text (.txt) and web page files. Save yours "
    "as one of those and try again."
)


def house_text_of(filename: str, raw: bytes) -> str:
    """The text of a house-rules file: `text_of`'s formats, plus a CSV read by rows, with the
    words of this flow (the names reader's point the DM to a names template). Importable by name,
    so a child process can run it."""
    try:
        return _text_of(filename, raw)
    except DocumentError as exc:
        words = str(exc)
        raise DocumentError(
            SCANNED if words == SCANNED_PDF else TYPES if words == TYPES_HELP else words
        ) from exc


def _text_of(filename: str, raw: bytes) -> str:
    if PurePath(filename).suffix.casefold() == ".csv":
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise DocumentError("That file is too big (up to 10 MB). Split it into smaller files.")
        text = csv_text(raw).replace("\x00", "")
        if not text.strip():
            raise DocumentError(NO_TEXT)
        if len(text) > MAX_DOCUMENT_CHARS:
            raise DocumentError(
                "That file is too long for one go. Split it into parts and add them one at a time."
            )
        return text
    return text_of(filename, raw)


async def read_file(filename: str, raw: bytes) -> str:
    """The file's text, read the safe way for its type. Raises DocumentError in plain words."""
    return await read_document(filename, raw, parse=house_text_of)


def fingerprint_of(text: str) -> str:
    """What a linked file's text was when it was last read: a hash of its words, spacing and
    case ignored. The text itself is never kept."""
    plain = " ".join(text.casefold().split())
    return hashlib.sha256(plain.encode("utf-8", errors="replace")).hexdigest()
