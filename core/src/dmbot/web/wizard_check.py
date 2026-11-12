"""The start-up wizard's "check only" read (#3955; docs/WIZARD_DESIGN.md, 4.3): can a document be
opened and read as text, and how many words and pages is it? No AI, no names.

The website's step 4 and 5 (the lore everyone knows, the secrets) only check what the DM gives;
nothing is read for names until Finish. The file is the DM's own and untrusted: it is opened by the
same safe reader the house-rules upload uses (`rules.house_reader.read_file`: a size cap before any
parsing, PDF and Word in a child process with a time limit, no network, no program run). This module
imports no AI and no story-memory code (a test keeps it so). It never raises for a file it cannot
read: it says why, in a short code the page words.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

from dmbot.memory.document_reader import READING
from dmbot.memory.name_documents import (
    MAX_DOCUMENT_BYTES,
    NO_TEXT,
    DocumentError,
)
from dmbot.rules import house_reader

# About how many words a page holds, for the page count of a format that has no pages of its own
# here (the page shows "about"). One number, so the count is the same every time.
WORDS_PER_PAGE = 500
# The website takes a PDF up to this size, less than Discord's 100 MB (#4001): the file travels as
# base64 (a third bigger) in one request through the tunnel, whose own request limit is 100 MB on
# the usual plans, and is held whole in the API container while it is read. A bigger PDF is added
# in Discord. Every other type keeps 10 MB.
MAX_WEB_PDF_BYTES = 50 * 1024 * 1024
MAX_WEB_BYTES = MAX_WEB_PDF_BYTES  # the largest file of any type that this door takes
# The base64 text of the largest file: 4 characters for each 3 bytes, a little over.
MAX_BASE64_CHARS = MAX_WEB_BYTES * 4 // 3 + 8


def web_limit(filename: str) -> int:
    """The most bytes the website takes for a file of this name."""
    return MAX_WEB_PDF_BYTES if filename.casefold().endswith(".pdf") else MAX_DOCUMENT_BYTES


# The whole JSON request that adds a file: one file as base64 plus a little for the other fields.
MAX_BODY_BYTES = MAX_BASE64_CHARS + 4_096
# Every other request of the wizard (answers, pasted text) carries no file: its text is at most
# 262,144 characters, up to 6 bytes each as JSON escapes, so this is plenty (#4001).
MAX_SMALL_BODY_BYTES = 4 * 1024 * 1024


def base64_limit(filename: str) -> int:
    """The most base64 characters this file's name allows, checked before anything is decoded."""
    return web_limit(filename) * 4 // 3 + 8


def too_big_file() -> Checked:
    """The answer for a file over its limit, given without decoding or reading it."""
    return _unreadable("too_big")


@dataclass(frozen=True, slots=True)
class Checked:
    """What the read found. `text` is kept for Finish and never given back to the page."""

    text: str
    pages: int
    words: int
    status: str  # "ok" or "unreadable"
    reason: str  # "" when ok; else one of the door's reason codes
    picture_pages: int = 0  # PDF pages with only pictures, counted not skipped in silence (#4000)


def _unreadable(reason: str) -> Checked:
    return Checked("", 0, 0, "unreadable", reason)


def words_of(text: str) -> int:
    return len(text.split())


def pages_of(words: int) -> int:
    return 0 if words == 0 else -(-words // WORDS_PER_PAGE)


def _reason(error: DocumentError) -> str:
    """The fixed code for a reader's plain words: the page words it, the DM never sees ours."""
    said = str(error).casefold()
    if "scanned" in said or "pictures of pages" in said:
        return "scanned"
    if str(error) == NO_TEXT or "no text" in said:
        return "empty"
    if "too big" in said or "too long" in said or "more than" in said:
        return "too_big"
    if "password" in said or "locked" in said or "encrypted" in said:
        return "locked"
    return "not_text"


def decode(data: object) -> bytes | None:
    """The file's bytes from the page's base64, or None when it is not base64 or is too long.
    The length is checked before decoding, so a huge string costs nothing."""
    if not isinstance(data, str) or len(data) > MAX_BASE64_CHARS:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


async def check_file(filename: str, raw: bytes) -> Checked:
    """Open one uploaded file and count it. Never raises for a bad file."""
    if len(raw) > web_limit(filename):
        return _unreadable("too_big")
    try:
        async with READING:  # as every other way in: two big reads at a time, not more
            text = await house_reader.read_file(filename, raw)
    except DocumentError as exc:
        return _unreadable(_reason(exc))
    except Exception:  # a damaged or hostile file must never escape as another error
        return _unreadable("not_text")
    pdf_pages = int(
        getattr(text, "pages", 0)
    )  # (kept before the cleaning below, which gives a str)
    pictures = int(getattr(text, "picture_pages", 0))
    text = text.replace("\x00", "")
    count = words_of(text)
    if count == 0:
        return _unreadable("empty")
    return Checked(text, pdf_pages or pages_of(count), count, "ok", "", pictures)


def check_text(text: str) -> Checked:
    """Pasted text, counted the same way."""
    text = text.replace("\x00", "")
    count = words_of(text)
    if count == 0:
        return _unreadable("empty")
    return Checked(text, pages_of(count), count, "ok", "")


LINK_MAX = 2_000  # one address, as long as the finish stage reads
_LINK = re.compile(r"https://\S+")


def check_link(address: str) -> Checked | None:
    """A link document (#4206): kept as the address itself, to be fetched once at Finish and never
    here (no request leaves the server from this door). None when it is not a plain https address
    of sensible length, so the route refuses it before anything is stored."""
    if len(address) > LINK_MAX or not _LINK.fullmatch(address):
        return None
    return Checked(address, 0, 0, "ok", "")
