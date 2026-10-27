"""The examples a DM gives for how their recaps should sound (#2075).

A recap's rules (what it may say, the names it may use, the length) are fixed in `draft.py`. This
module only holds the *style*: up to 3 pasted recaps, each at most 600 words, that the writer reads
for tone, rhythm and word choice, never for what happens in them. With none of its own a campaign
gets the defaults shipped in `style_examples/`: four example recaps from the owner's table, with
invented names (owner decision, 2026-10-20), as plain text files read in file-name order. The DM's
examples replace the defaults, and removing them all brings the defaults back.

Per campaign, isolated, deleted with the campaign, and in its backup (CLAUDE.md: campaign
isolation). The examples go only to the writer's call: never to the beat sheet or the fact check,
and the code holds the finished recap to the rules whatever the examples say (`draft.check_prose`:
a name that is only in an example is unknown, and a run of 6 words taken from one is a copy).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.rules.house import require_dm

MAX_EXAMPLES = 3
MAX_NOTES = 10  # standing style notes a campaign keeps; the oldest goes when an 11th is saved
NOTE_CHARS = 300
EXAMPLE_MAX_WORDS = 600
MAX_CHARS = 8000  # the table's own limit, so a long-worded paste gets TOO_LONG, not an error
INT64_MAX = 2**63 - 1

DAMAGED = "This backup file is damaged (bad recap style)."
EMPTY = "That is empty. Paste a recap you like."
TOO_LONG = f"That is too long. Keep it to {EXAMPLE_MAX_WORDS} words or fewer."
FULL = f"You can keep {MAX_EXAMPLES} examples. Remove one to add another."
NOT_FOUND = "That example is already gone."
NOTE_GONE = "That note is already gone."
NOTE_BAD = "That note is empty or too long. Keep it to one short sentence."


@dataclass(frozen=True, slots=True)
class Example:
    id: int
    text: str


def defaults() -> list[str]:
    """The shipped examples, in file-name order."""
    folder = resources.files("dmbot.recaps").joinpath("style_examples")
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".txt")), key=lambda f: f.name)
    return [t for t in (f.read_text(encoding="utf-8").strip() for f in files) if t]


def clean(text: str) -> str:
    """The example as kept: trimmed, and refused when empty or over the word limit."""
    text = text.strip()
    if not text:
        raise CampaignError(EMPTY)
    if len(text.split()) > EXAMPLE_MAX_WORDS or len(text) > MAX_CHARS:
        raise CampaignError(TOO_LONG)
    return text


class StyleStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def own(self, guild_id: int, campaign_id: str) -> list[Example]:
        """The DM's own examples, oldest first (none means the defaults are in use)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, text FROM recap_style_examples WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY position, id",
                (guild_id, campaign_id),
            )
            return [Example(int(r["id"]), r["text"]) for r in await cur.fetchall()]

    async def examples_for(self, guild_id: int, campaign_id: str) -> list[str]:
        """What the writer reads: the DM's own examples, or the defaults when there are none."""
        own = await self.own(guild_id, campaign_id)
        return [example.text for example in own] if own else defaults()

    async def add(self, guild_id: int, campaign_id: str, user_id: int, text: str) -> Example:
        """Add an example. Only the campaign's DMs, at most `MAX_EXAMPLES`."""
        kept = clean(text)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS top, COUNT(*) AS n FROM recap_style_examples"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            if int(row["n"]) >= MAX_EXAMPLES:
                raise CampaignError(FULL)
            cur = await conn.execute(
                "INSERT INTO recap_style_examples (guild_id, campaign_id, position, text,"
                " created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (guild_id, campaign_id, int(row["top"]) + 1, kept, user_id, int(self._clock())),
            )
            made = await cur.fetchone()
            assert made is not None
        return Example(int(made["id"]), kept)

    async def remove(self, guild_id: int, campaign_id: str, user_id: int, example_id: int) -> None:
        """Remove one example. Only the campaign's DMs."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "DELETE FROM recap_style_examples WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (guild_id, campaign_id, example_id),
            )
            if cur.rowcount == 0:
                raise CampaignError(NOT_FOUND)

    async def use_defaults(self, guild_id: int, campaign_id: str, user_id: int) -> None:
        """Remove all of the DM's own examples, so the defaults are used again."""
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            await conn.execute(
                "DELETE FROM recap_style_examples WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    text: str


class NoteStore:
    """The standing style notes sorted out of a DM's feedback (#2076). Only a note the DM pressed
    Save on is ever kept here; accuracy and content feedback never is."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def notes(self, guild_id: int, campaign_id: str) -> list[Note]:
        """Oldest first: where two notes disagree the later one wins, so the writer reads them in
        this order."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, text FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY position, id",
                (guild_id, campaign_id),
            )
            return [Note(int(r["id"]), r["text"]) for r in await cur.fetchall()]

    async def add(
        self, guild_id: int, campaign_id: str, user_id: int, text: str
    ) -> tuple[Note, bool]:
        """Keep a note. Only the campaign's DMs. Returns it and whether the oldest note had to go
        to make room (the DM is told). The same words again are kept once, as the newest."""
        kept = " ".join(text.split())
        if not kept or len(kept) > NOTE_CHARS:
            raise CampaignError(NOTE_BAD)
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            await conn.execute(
                "DELETE FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s"
                " AND lower(text) = lower(%s)",
                (guild_id, campaign_id, kept),
            )
            cur = await conn.execute(
                "SELECT id FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY position, id",
                (guild_id, campaign_id),
            )
            ids = [int(r["id"]) for r in await cur.fetchall()]
            dropped = ids[: max(0, len(ids) - MAX_NOTES + 1)]
            if dropped:
                await conn.execute(
                    "DELETE FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = ANY(%s)",
                    (guild_id, campaign_id, dropped),
                )
            cur = await conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS top FROM recap_style_notes"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            top = await cur.fetchone()
            assert top is not None
            cur = await conn.execute(
                "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (guild_id, campaign_id, int(top["top"]) + 1, kept, user_id, int(self._clock())),
            )
            made = await cur.fetchone()
            assert made is not None
        return Note(int(made["id"]), kept), bool(dropped)

    async def remove(self, guild_id: int, campaign_id: str, user_id: int, note_id: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id, lock=True)
            cur = await conn.execute(
                "DELETE FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (guild_id, campaign_id, note_id),
            )
            if cur.rowcount == 0:
                raise CampaignError(NOTE_GONE)


class NotesSection:
    """A campaign's style notes in its backups (an `ExportSection`)."""

    name = "recap_style_notes"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT text, created_by, created_at FROM recap_style_notes"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY position, id",
            (guild_id, campaign_id),
        )
        return [
            {"text": r["text"], "by": str(r["created_by"]), "at": int(r["created_at"])}
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        if len(rows) > MAX_NOTES:
            raise CampaignError(DAMAGED)
        out: list[_Checked] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"text", "by", "at"}:
                raise CampaignError(DAMAGED)
            text, by, at = row["text"], row["by"], row["at"]
            digits = isinstance(by, str) and by.isascii() and by.isdigit()
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > NOTE_CHARS
                or not digits
                or not 0 < int(str(by)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            out.append(_Checked(" ".join(text.split()), int(str(by)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, _Checked) for r in rows)
            else self.check(rows)
        )
        for n, r in enumerate(checked, start=1):
            await conn.execute(
                "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, n, r.text, r.created_by, r.created_at),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_style_notes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


@dataclass(frozen=True, slots=True)
class _Checked:
    text: str
    created_by: int
    created_at: int


class StyleSection:
    """A campaign's own recap examples in its backups (an `ExportSection`)."""

    name = "recap_style_examples"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT text, created_by, created_at FROM recap_style_examples"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY position, id",
            (guild_id, campaign_id),
        )
        return [
            {"text": r["text"], "by": str(r["created_by"]), "at": int(r["created_at"])}
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        if len(rows) > MAX_EXAMPLES:
            raise CampaignError(DAMAGED)
        out: list[_Checked] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"text", "by", "at"}:
                raise CampaignError(DAMAGED)
            text, by, at = row["text"], row["by"], row["at"]
            digits = isinstance(by, str) and by.isascii() and by.isdigit()
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.split()) > EXAMPLE_MAX_WORDS
                or len(text) > MAX_CHARS
                or not digits
                or not 0 < int(str(by)) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            out.append(_Checked(text.strip(), int(str(by)), at))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, _Checked) for r in rows)
            else self.check(rows)
        )
        for n, r in enumerate(checked, start=1):
            await conn.execute(
                "INSERT INTO recap_style_examples (guild_id, campaign_id, position, text,"
                " created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (guild_id, campaign_id, n, r.text, r.created_by, r.created_at),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_style_examples WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
