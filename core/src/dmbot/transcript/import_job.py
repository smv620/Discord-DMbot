"""Cleaning an imported session (#2105): the Transcript Cleaner and the off-topic labelling, run
on its stored lines the way they run on a live session, in the background away from the website.

The website stores the lines (through its store door) and leaves the session `waiting`. This job
takes it (`working`), fixes misheard names with the campaign's own names (never a fix DMbot is
unsure of: nothing is shown with Undo for a file, so only the silent ones are made), asks the fast
AI which windows of lines are not game talk, and marks it `done`. What was heard never changes; only
the cleaned text and the topic do. Counts only in the log, never a word of the transcript.

A restart puts `working` back to `waiting` (`ImportStore.resume`), so nothing is lost. A name
lookup or cleaning that fails leaves the line as heard; an AI window that fails leaves its lines
as game talk. Only a session where the cleaner could not start fails (`reason`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.lookup import CampaignLookup
from dmbot.transcript.cleaner import clean
from dmbot.transcript.topic_ai import classify
from dmbot.transcript.topics import GAME, WINDOW_LINES

log = logging.getLogger(__name__)

PER_ROUND = 3  # sessions cleaned in one round for one server
TOPIC_CALL_TIMEOUT_S = 30.0
FAILED_NAMES = "names_failed"  # the campaign's names couldn't be read
FAILED_ERROR = "error"  # anything else that stopped it
CHUNK_LINES = 120  # lines cleaned between two yields to the bot's other work, and between saves
MAX_WINDOWS = 400  # AI windows (WINDOW_LINES lines each) for one session; the rest stays game talk
TOPIC_FAILURES_BEFORE_REST = 3  # windows failing one after another before the AI is left alone

Lookup = Callable[[int, str], Awaitable[CampaignLookup]]


@dataclass(frozen=True, slots=True)
class Row:
    id: int
    user_id: int
    heard: str
    agreed: bool = True  # still recorded now; a line of someone who stopped is left as heard


class ImportStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def waiting(self, guild_id: int, limit: int = PER_ROUND) -> list[tuple[str, str]]:
        """(campaign, session) of the imports waiting in this server, oldest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_id FROM session_imports WHERE state = 'waiting'"
                " ORDER BY imported_at, session_id LIMIT %s",
                (limit,),
            )
            return [(r["campaign_id"], r["session_id"]) for r in await cur.fetchall()]

    async def claim(self, guild_id: int, session_id: str) -> bool:
        """Take a waiting import. False when someone else has (or it is gone)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET state = 'working', reason = ''"
                " WHERE session_id = %s AND state = 'waiting'",
                (session_id,),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, session_id: str, *, ok: bool, reason: str = "") -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET state = %s, reason = %s WHERE session_id = %s",
                ("done" if ok else "failed", reason[:40], session_id),
            )

    async def resume(self, guild_id: int) -> int:
        """After a restart: an import left `working` goes back to `waiting`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET state = 'waiting' WHERE state = 'working'"
            )
            return cur.rowcount

    async def rows(self, guild_id: int, session_id: str) -> list[Row]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT l.id, l.user_id, l.heard, EXISTS (SELECT 1 FROM consent c"
                " WHERE c.guild_id = l.guild_id AND c.user_id = l.user_id) AS agreed"
                " FROM transcript_lines l WHERE l.session_id = %s AND l.sidebar IS NULL"
                " ORDER BY l.started_ms, l.id",
                (session_id,),
            )
            return [
                Row(int(r["id"]), int(r["user_id"]), r["heard"], bool(r["agreed"]))
                for r in await cur.fetchall()
            ]

    async def back_to_waiting(self, guild_id: int, session_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET state = 'waiting' WHERE session_id = %s"
                " AND state = 'working'",
                (session_id,),
            )

    async def apply(
        self, guild_id: int, session_id: str, cleaned: Sequence[tuple[int, str | None, str]]
    ) -> None:
        """Save (line id, cleaned text or None when unchanged, topic) for lines of this session."""
        if not cleaned:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_lines t SET text = c.text, topic = c.topic"
                " FROM unnest(%s::bigint[], %s::text[], %s::text[]) AS c(id, text, topic)"
                " WHERE t.id = c.id AND t.session_id = %s",
                (
                    [c[0] for c in cleaned],
                    [c[1] for c in cleaned],
                    [c[2] for c in cleaned],
                    session_id,
                ),
            )


class ImportJob:
    def __init__(self, store: ImportStore, lookup: Lookup, topic_ai: Any | None) -> None:
        self.store, self.lookup, self.topic_ai = store, lookup, topic_ai

    async def run_round(self, guild_id: int) -> int:
        """Clean the waiting imports of one server. How many were done."""
        done = 0
        for cid, sid in await self.store.waiting(guild_id):
            if await self.store.claim(guild_id, sid):
                await self.run(guild_id, cid, sid)
                done += 1
        return done

    async def run(self, guild_id: int, campaign_id: str, session_id: str) -> None:
        """Clean one claimed session. Whatever goes wrong, it never stays `working`: it fails with
        a reason, or (if the bot is stopping) goes back to waiting."""
        try:
            await self._run(guild_id, campaign_id, session_id)
        except asyncio.CancelledError:
            await asyncio.shield(self.store.back_to_waiting(guild_id, session_id))
            raise
        except Exception:
            log.exception("Import: a session could not be cleaned")
            await self.store.finish(guild_id, session_id, ok=False, reason=FAILED_ERROR)

    async def _run(self, guild_id: int, campaign_id: str, session_id: str) -> None:
        try:
            lookup = await self.lookup(guild_id, campaign_id)
        except Exception:
            log.exception("Import: couldn't read the campaign's names")
            await self.store.finish(guild_id, session_id, ok=False, reason=FAILED_NAMES)
            return
        rows = await self.store.rows(guild_id, session_id)
        windows_left = MAX_WINDOWS
        failures = 0
        for start in range(0, len(rows), CHUNK_LINES):
            chunk = rows[start : start + CHUNK_LINES]
            texts: list[str | None] = []
            for row in chunk:
                fixed = row.heard
                if row.agreed:  # someone who stopped being recorded is not worked on at all
                    try:
                        fixed = clean(lookup, row.heard, unsure=False).text
                    except Exception:
                        fixed = row.heard  # a line the cleaner can't read stays as heard
                texts.append(None if fixed == row.heard else fixed)
            topics = [GAME] * len(chunk)
            if self.topic_ai is not None:
                topics, windows_left, failures = await self._topics(
                    [
                        (t or r.heard) if r.agreed else None
                        for t, r in zip(texts, chunk, strict=True)
                    ],
                    windows_left,
                    failures,
                )
            await self.store.apply(
                guild_id,
                session_id,
                [(r.id, t, topic) for r, t, topic in zip(chunk, texts, topics, strict=True)],
            )
            await asyncio.sleep(0)  # the live table's work goes first between chunks
        await self.store.finish(guild_id, session_id, ok=True)
        log.info("Import: cleaned a session of %d lines", len(rows))  # counts only

    async def _topics(
        self, lines: Sequence[str | None], windows_left: int, failures: int
    ) -> tuple[list[str], int, int]:
        """The topics of these lines, a window at a time (a line that is None is not sent). A
        window that fails, a session out of windows, or an AI that failed several windows in a row
        leaves its lines as game talk: nothing is left out on a guess. Returns the topics and the
        windows and failures left over."""
        out = [GAME] * len(lines)
        for start in range(0, len(lines), WINDOW_LINES):
            if windows_left <= 0 or failures >= TOPIC_FAILURES_BEFORE_REST:
                break
            window = lines[start : start + WINDOW_LINES]
            sent = [(i, t) for i, t in enumerate(window) if t is not None]
            if not sent:
                continue
            windows_left -= 1
            try:
                topics, _reply = await asyncio.wait_for(
                    classify(self.topic_ai, [t for _, t in sent]), TOPIC_CALL_TIMEOUT_S
                )
            except Exception:
                failures += 1
                log.warning("Import: a window of lines wasn't labelled")
                continue
            failures = 0
            if len(topics) != len(sent):
                continue
            for (i, _), topic in zip(sent, topics, strict=True):
                out[start + i] = topic
        return out, windows_left, failures
