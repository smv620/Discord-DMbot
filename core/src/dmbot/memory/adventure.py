"""The DM's adventure text (docs/STORY_MEMORY.md, section 6, "Where the story plan comes from";
#1698; CLAUDE.md, IP rule): a file the DM gives DMbot for one campaign, kept in chunks of about
2,000 characters so a later slice (the story-plan reader) can use it. Nothing reads it yet and no
AI is called here.

The text stays in that one campaign: row-level security is forced, it is never in a transcript,
a shared ruleset, a pack, another campaign or another server, and never shown to players. It is
NOT in the campaign's backup (Supervisor, 2026-10-17: anyone in the server may download a paid
campaign's backup); the backup keeps only the document list, and a restored document has no
chunks until the DM gives the same file again. The text goes when the campaign is deleted. A DM
can remove a document, which removes its chunks.

The story-plan reader that comes next must check the switch and a non-restored confirmation
again before it uses any of this text: a restored campaign has no text and not the right.

Giving a file needs the Shared story switch on and the DM's confirmation of the right to use the
material on record (#1692): checked here, not only by the button.

This module has the pure part (reading a file's text and cutting it into chunks) and the store.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import SHARED_STORY, STORY_OFF, CampaignError
from dmbot.campaigns.store import DAMAGED
from dmbot.db import Conn, Database
from dmbot.memory.name_documents import MAX_DOCUMENT_BYTES, DocumentError, text_of

CHUNK_CHARS = 2000  # about; a chunk ends at a heading or a blank line, never mid-word
CHUNK_HARD = 4000  # the database's limit on one chunk
MIN_CHUNK = 400  # a heading starts a new chunk only after this much, so a list of short lines
# can't make a row for every line (the rows are bounded by the characters / MIN_CHUNK)
HEADING_CHARS = 80  # the longest line taken for a heading
HEADING_KEPT = 200
CAMPAIGN_MAX_CHARS = 2_000_000
DOCUMENT_MAX_CHARS = CAMPAIGN_MAX_CHARS  # one file may be all of it
DOCUMENTS_MAX = 20  # per campaign: each has a Remove button on the card (four rows of five)
FILE_NAME_MAX = 200
# The longest answer the reading process sends back: every character may be up to 4 bytes.
READ_REPLY_BYTES = DOCUMENT_MAX_CHARS * 4 + 1024
READ_TIME_S = 120.0  # a 300-page book, not a names list
FILE_MAX_BYTES = MAX_DOCUMENT_BYTES  # the same limit as the names-from-documents upload

ALREADY = "You already gave DMbot that file."
FULL = (
    "This campaign's adventure text is full (about 1,000 pages). Remove a document, then "
    "add this one."
)
TOO_MANY = f"This campaign already has {DOCUMENTS_MAX} documents. Remove one, then add this one."
NOT_DM = "Only this campaign's DMs can do that."
NO_CAMPAIGN = "That campaign isn't here any more. Open ⚙️ Manage DMbot again."
NOT_ON = "Turn on Shared story and say you have the right to use the material first."
GONE_DOC = "That document is already gone."


class AdventureError(ValueError):
    """Plain words for the DM."""


def adventure_text(filename: str, raw: bytes) -> str:
    """The text of the file, as the names screen reads one but allowed to be a whole adventure.
    Importable by name: the reading process starts it afresh."""
    return text_of(filename, raw, max_chars=DOCUMENT_MAX_CHARS)


# ---- cutting the text into chunks ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    heading: str  # the last heading before it, "" before the first
    text: str


_MD_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_BREAK = re.compile(r"\n\s*\n")
_SPACE = re.compile(r"[ \t]+")


def _is_heading(block: str) -> str | None:
    """The block as a heading, or None: a Markdown `# Heading`, or one short line with no
    full stop at the end, which is how a PDF or Word title comes out as text."""
    if "\n" in block.strip():
        return None
    line = block.strip()
    markdown = _MD_HEADING.match(line)
    if markdown:
        return markdown.group(1)[:HEADING_KEPT]
    if 0 < len(line) <= HEADING_CHARS and line[-1] not in ".,;:!?" and not line.isdigit():
        return line[:HEADING_KEPT]
    return None


def _pieces(block: str, limit: int) -> list[str]:
    """One block cut to pieces of at most `limit` characters, at the end of a sentence if it
    can, else at a space; only a "word" longer than the limit is cut in the middle."""
    out: list[str] = []
    rest = block
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        cut = cut + 1 if cut > limit // 3 else window.rfind(" ")
        if cut <= 0:
            cut = limit  # (no space at all: a hostile or broken line)
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return [p for p in out if p]


def chunks_of(text: str, target: int = CHUNK_CHARS) -> list[Chunk]:
    """The text in order, cut into chunks of about `target` characters at headings and blank
    lines, never mid-word. A heading starts a new chunk and is remembered for the chunks after
    it until the next one. No text is lost or reordered: the chunks, joined, hold every word."""
    blocks = [b.strip() for b in _BREAK.split(text.replace("\r\n", "\n").replace("\r", "\n"))]
    out: list[Chunk] = []
    heading = ""
    current: list[str] = []
    size = 0
    started_under = ""

    def flush() -> None:
        nonlocal current, size
        if current:
            out.append(Chunk(started_under, "\n\n".join(current)))
        current, size = [], 0

    for block in blocks:
        block = _SPACE.sub(" ", block)
        if not block:
            continue
        title = _is_heading(block)
        if title is not None:
            if size >= MIN_CHUNK:
                flush()
            heading = title
        for piece in _pieces(block, min(target, CHUNK_HARD)):
            if current and size + len(piece) + 2 > target:
                flush()
            if not current:
                started_under = heading
            current.append(piece)
            size += len(piece) + 2
    flush()
    return out


# ---- the store ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    file_name: str
    characters: int
    chunks: int
    added_by: int
    added_at: int

    @property
    def has_text(self) -> bool:
        """False for a document restored from a backup: its name is kept, its text is not."""
        return self.chunks > 0


def fingerprint_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def clean_file_name(name: str) -> str:
    """A name safe to show: no path, no control characters, at most FILE_NAME_MAX."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    return (cleaned or "adventure")[:FILE_NAME_MAX]


class AdventureStore:
    """The documents and chunks of every campaign, each read and written one campaign at a time
    (the server and the campaign are in every statement)."""

    def __init__(self, db: Database, *, clock: Callable[[], float] | None = None) -> None:
        self._db = db
        self._clock = clock or time.time

    async def add(
        self, guild_id: int, campaign_id: str, user_id: int, file_name: str, text: str
    ) -> Document:
        """Keep a file's text for this campaign, in chunks, in one transaction. Refused with plain
        words when the person isn't one of its DMs, the switch is Off or has no confirmation, the
        same text is already here, or the campaign would pass its limit."""
        chunks = await asyncio.to_thread(chunks_of, text)  # CPU: off the event loop
        if not chunks:
            raise AdventureError("DMbot found no text in that file. Check it's the right file.")
        name = clean_file_name(file_name)
        digest = fingerprint_of(text)
        now = int(self._clock())
        doc_id = uuid.uuid4().hex
        async with self._db.guild(guild_id) as conn:
            await self.require_dm(conn, guild_id, campaign_id, user_id)
            await self.require_on(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "SELECT count(*) AS n, COALESCE(sum(characters), 0) AS total,"
                " bool_or(fingerprint = %s) AS same FROM adventure_documents"
                " WHERE guild_id = %s AND campaign_id = %s",
                (digest, guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            if row["same"]:
                # A document that came back from a backup has its name but no text (#1698): the
                # same file given again fills it in. A document that has its text is a repeat.
                cur = await conn.execute(
                    "SELECT id, added_by, added_at FROM adventure_documents WHERE guild_id = %s"
                    " AND campaign_id = %s AND fingerprint = %s AND chunks = 0 FOR UPDATE",
                    (guild_id, campaign_id, digest),
                )
                empty = await cur.fetchone()
                if empty is None:
                    raise AdventureError(ALREADY)
                doc_id = empty["id"]
                await conn.execute(
                    "UPDATE adventure_documents SET chunks = %s WHERE guild_id = %s"
                    " AND campaign_id = %s AND id = %s",
                    (len(chunks), guild_id, campaign_id, doc_id),
                )
                await self._insert_chunks(conn, guild_id, campaign_id, doc_id, chunks, now, user_id)
                return Document(
                    doc_id,
                    name,
                    len(text),
                    len(chunks),
                    int(empty["added_by"]),
                    int(empty["added_at"]),
                )
            if int(row["n"]) >= DOCUMENTS_MAX:
                raise AdventureError(TOO_MANY)
            if int(row["total"]) + len(text) > CAMPAIGN_MAX_CHARS:
                raise AdventureError(FULL)
            await conn.execute(
                "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name,"
                " fingerprint, characters, chunks, added_by, added_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, doc_id, name, digest, len(text), len(chunks), user_id, now),
            )
            await self._insert_chunks(conn, guild_id, campaign_id, doc_id, chunks, now, user_id)
        return Document(doc_id, name, len(text), len(chunks), user_id, now)

    @staticmethod
    async def _insert_chunks(
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        doc_id: str,
        chunks: list[Chunk],
        now: int,
        user_id: int,
    ) -> None:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO adventure_chunks (guild_id, campaign_id, id, document_id,"
                " position, heading, text, created_at, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        guild_id,
                        campaign_id,
                        uuid.uuid4().hex,
                        doc_id,
                        n,
                        c.heading,
                        c.text,
                        now,
                        user_id,
                    )
                    for n, c in enumerate(chunks)
                ],
            )

    async def documents(self, guild_id: int, campaign_id: str) -> list[Document]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, file_name, characters, chunks, added_by, added_at"
                " FROM adventure_documents WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY added_at, id",
                (guild_id, campaign_id),
            )
            return [
                Document(
                    r["id"],
                    r["file_name"],
                    int(r["characters"]),
                    int(r["chunks"]),
                    int(r["added_by"]),
                    int(r["added_at"]),
                )
                for r in await cur.fetchall()
            ]

    async def chunks(self, guild_id: int, campaign_id: str, document_id: str) -> list[Chunk]:
        """A document's chunks in order (for the story-plan reader that comes next, and tests)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT heading, text FROM adventure_chunks WHERE guild_id = %s"
                " AND campaign_id = %s AND document_id = %s ORDER BY position",
                (guild_id, campaign_id, document_id),
            )
            return [Chunk(r["heading"], r["text"]) for r in await cur.fetchall()]

    async def remove(self, guild_id: int, campaign_id: str, user_id: int, document_id: str) -> None:
        """Remove a document; its chunks go with it. Only the campaign's DMs."""
        async with self._db.guild(guild_id) as conn:
            await self.require_dm(conn, guild_id, campaign_id, user_id)
            cur = await conn.execute(
                "DELETE FROM adventure_documents WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (guild_id, campaign_id, document_id),
            )
            if cur.rowcount == 0:
                raise AdventureError(GONE_DOC)

    @staticmethod
    async def require_dm(conn: Conn, guild_id: int, campaign_id: str, user_id: int) -> None:
        cur = await conn.execute(
            "SELECT 1 AS ok FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
            (guild_id, campaign_id),
        )
        if await cur.fetchone() is None:
            raise AdventureError(NO_CAMPAIGN)
        cur = await conn.execute(
            "SELECT 1 AS ok FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
            " AND user_id = %s",
            (guild_id, campaign_id, user_id),
        )
        if await cur.fetchone() is None:
            raise AdventureError(NOT_DM)

    @staticmethod
    async def require_on(conn: Conn, guild_id: int, campaign_id: str) -> None:
        """The switch is on and a DM said here that they have the right (a confirmation that came
        from a backup file is not one: CLAUDE.md, IP rule)."""
        cur = await conn.execute(
            "SELECT c.story_mode AS mode, EXISTS (SELECT 1 FROM shared_confirmations s"
            " WHERE s.guild_id = c.guild_id AND s.campaign_id = c.id AND s.purpose = %s"
            " AND NOT s.restored) AS said FROM campaigns c WHERE c.guild_id = %s AND c.id = %s",
            (SHARED_STORY, guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None or row["mode"] == STORY_OFF or not row["said"]:
            raise AdventureError(NOT_ON)


# ---- backups --------------------------------------------------------------------------------

_DOC_KEYS = frozenset({"file_name", "fingerprint", "characters", "added_by", "added_at"})


def _storable(text: str) -> bool:
    """Text the database will keep: no NUL byte (Postgres refuses it) and no lone surrogate
    (it can't be written as UTF-8)."""
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


_HEX64 = re.compile(r"[0-9a-f]{64}")
_INT64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class _Checked:
    file_name: str
    fingerprint: str
    characters: int
    added_by: int
    added_at: int


class AdventureSection:
    """The adventure's document list in campaign backups (an `ExportSection`), **never its text**
    (Supervisor, 2026-10-17): anyone in the server may download a paid campaign's backup, and that
    would hand a commercial adventure's full text to all of them, past what the DM confirmed the
    right to use (CLAUDE.md, IP rule). A restored document keeps its name, size, fingerprint and
    who added it, with no chunks; giving DMbot the same file again fills it in. The ids mean
    nothing in another campaign, so they are made again. Restoring adds no confirmation (#1692)."""

    name = "adventure"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT file_name, fingerprint, characters, added_by, added_at"
            " FROM adventure_documents WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY added_at, id",
            (guild_id, campaign_id),
        )
        return [
            {
                "file_name": d["file_name"],
                "fingerprint": d["fingerprint"],
                "characters": int(d["characters"]),
                "added_by": str(d["added_by"]),
                "added_at": int(d["added_at"]),
            }
            for d in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        """Every field from the untrusted file, checked in a worker thread. Nothing is written
        until all of it is right."""
        if len(rows) > DOCUMENTS_MAX:
            raise CampaignError(DAMAGED)
        out: list[_Checked] = []
        seen: set[str] = set()
        total = 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != _DOC_KEYS:
                raise CampaignError(DAMAGED)
            name, digest = row["file_name"], row["fingerprint"]
            characters, by, at = row["characters"], row["added_by"], row["added_at"]
            if not (
                isinstance(name, str)
                and 1 <= len(name) <= FILE_NAME_MAX
                and clean_file_name(name) == name  # no control characters, no path
                and _storable(name)
                and isinstance(digest, str)
                and _HEX64.fullmatch(digest)
                and digest not in seen
                and type(characters) is int
                and characters > 0
                and isinstance(by, str)
                and by.isascii()
                and by.isdigit()
                and 0 < int(by) <= _INT64_MAX
                and type(at) is int
                and 0 <= at <= _INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            seen.add(digest)
            total += characters
            if total > CAMPAIGN_MAX_CHARS:
                raise CampaignError(DAMAGED)
            out.append(_Checked(name, digest, characters, int(by), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        loaded = rows if self._is_checked(rows) else self.check(rows)
        for doc in loaded:
            doc_id = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name,"
                " fingerprint, characters, chunks, added_by, added_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    guild_id,
                    campaign_id,
                    doc_id,
                    doc.file_name,
                    doc.fingerprint,
                    doc.characters,
                    0,  # no text comes back from a backup
                    doc.added_by,
                    doc.added_at,
                ),
            )

    @staticmethod
    def _is_checked(rows: Any) -> bool:
        return isinstance(rows, list) and all(isinstance(r, _Checked) for r in rows)

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM adventure_documents WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = [
    "AdventureError",
    "AdventureSection",
    "AdventureStore",
    "Chunk",
    "Document",
    "DocumentError",
    "adventure_text",
    "chunks_of",
]
