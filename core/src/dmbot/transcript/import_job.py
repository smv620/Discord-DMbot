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
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from dmbot import ai_feed
from dmbot.db import Database
from dmbot.memory.lookup import CampaignLookup
from dmbot.transcript.cleaner import clean
from dmbot.transcript.store import Settled, TranscriptStore
from dmbot.transcript.topic_ai import Completes, classify
from dmbot.transcript.topics import GAME, WINDOW_LINES

log = logging.getLogger(__name__)

PER_ROUND = 3  # sessions cleaned in one round for one server
TOPIC_CALL_TIMEOUT_S = 30.0
FAILED_NAMES = "names_failed"  # the campaign's names couldn't be read
FAILED_ERROR = "error"  # anything else that stopped it
PARTLY = "partly_labelled"  # done, but the AI was not asked about every line
# (a scan that is scanned and not finished: a session whose story read is not complete)
# The story read of an import (#3301) applies to scans finished on or after this time (Unix,
# 2026-11-03): an import scanned before it is left as it was, so a deploy never spends on old
# imports all at once.
READ_IMPORTS_FROM = 1_793_664_000
_UNREAD = (
    f"i.scan_finished AND NOT i.scan_gave_up AND i.scan_to >= {READ_IMPORTS_FROM}"
    " AND t.extracted_through_ms < ("
    "SELECT coalesce(max(l.started_ms), 0) FROM transcript_lines l"
    " WHERE l.guild_id = t.guild_id AND l.session_id = t.id)"
)
STALE_SCAN_S = 15 * 60  # a name scan claimed this long ago and never finished is taken again
RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped; it is taken again
CHUNK_LINES = 120  # lines cleaned between two yields to the bot's other work, and between saves
# The step the AI feed shows while a session is cleaned (#3710): the import page's own word for it
# (web/src/content/import-words.ts, `stage.cleaning`).
TIDYING = "Tidying the transcript"
MAX_WINDOWS = 1_200  # AI windows (WINDOW_LINES lines each) for one session, about 7,200 lines
TOPIC_FAILURES_BEFORE_REST = 3  # windows failing one after another before the AI is left alone

Lookup = Callable[[int, str], Awaitable[CampaignLookup]]
Consenting = Callable[[int], Awaitable[frozenset[int]]]  # who is recorded now, in a server
Recorded = Callable[[int, str], Awaitable[frozenset[int]]]  # ...and in one campaign (#3305)


@dataclass(frozen=True, slots=True)
class Ready:
    """A cleaned import whose name scan is next (#2106)."""

    campaign_id: str
    session_id: str
    started_at: int
    write_recap: bool
    import_id: str = ""  # the batch the session came in


@dataclass(frozen=True, slots=True)
class WaitingPlay:
    """A "plays which character" choice from the import page that waits for the bot."""

    campaign_id: str
    user_id: int
    character_id: str
    created_at: int


@dataclass(frozen=True, slots=True)
class Row:
    id: int
    user_id: int
    heard: str


class ImportStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def waiting_plays(self, guild_id: int, limit: int = 50) -> list[WaitingPlay]:
        """The choices the DM made on the import page, oldest first, that the bot has not made
        links of yet (#2135)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, user_id, character_id, created_at FROM import_plays"
                " ORDER BY created_at, user_id LIMIT %s",
                (limit,),
            )
            return [
                WaitingPlay(
                    r["campaign_id"], int(r["user_id"]), r["character_id"], int(r["created_at"])
                )
                for r in await cur.fetchall()
            ]

    async def done_plays(self, guild_id: int, play: WaitingPlay) -> None:
        """A choice has been made a link, or can no longer be: it stops waiting. Only this very
        choice: one the DM saved again since it was read stays."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM import_plays WHERE campaign_id = %s AND user_id = %s"
                " AND character_id = %s AND created_at = %s",
                (play.campaign_id, play.user_id, play.character_id, play.created_at),
            )

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
                "UPDATE session_imports SET state = 'working', reason = '', claimed_at = %s"
                " WHERE session_id = %s AND state = 'waiting'",
                (int(time.time()), session_id),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, session_id: str, *, ok: bool, reason: str = "") -> None:
        """The end of the work: `done` (with a reason when it was only partly done) or `failed`."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET state = %s, reason = %s WHERE session_id = %s",
                ("done" if ok else "failed", reason[:40], session_id),
            )

    async def resume(self, guild_id: int, *, older_than_s: int = RETAKE_AFTER_S) -> int:
        """An import left `working` by a bot that stopped goes back to `waiting`: one whose claim
        is older than `older_than_s` (0: every one, for the start of the bot)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET state = 'waiting' WHERE state = 'working'"
                " AND claimed_at <= %s",
                (int(time.time()) - older_than_s,),
            )
            return int(cur.rowcount)

    async def rows(self, guild_id: int, session_id: str) -> list[Row]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, heard FROM transcript_lines"
                " WHERE session_id = %s AND sidebar IS NULL ORDER BY started_ms, id",
                (session_id,),
            )
            return [Row(int(r["id"]), int(r["user_id"]), r["heard"]) for r in await cur.fetchall()]

    async def settle(self, guild_id: int, campaign_id: str, session_id: str) -> Settled:
        """The filter leans to keeping here too (#2567): once the lines are labelled, what the AI
        was not sure about is kept, as at the end of a live session (`settle_topics`), with the
        campaign's DMs, before the name scan reads the lines."""
        # The Discord IDs that can act as a DM, not the account numbers the table holds: lines are
        # labelled with the speaker's Discord ID (#2533).
        from dmbot.campaigns.store import CampaignStore

        campaign = await CampaignStore(self._db).get(guild_id, campaign_id)
        dms = set(campaign.dm_user_ids) if campaign is not None else set()
        return await TranscriptStore(self._db).settle_topics(guild_id, session_id, dms)

    async def ready_for_scan(self, guild_id: int, limit: int = PER_ROUND) -> list[Ready]:
        """For each campaign, its earliest cleaned import not yet scanned (or whose claim never
        finished, `STALE_SCAN_S` ago), when no earlier import of that campaign is still waiting,
        being cleaned or unscanned: the scans and recaps of a batch go in date order (#2106)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT i.campaign_id, i.session_id, i.write_recap, t.started_at, i.import_id"
                " FROM session_imports i JOIN transcript_sessions t"
                " ON t.id = i.session_id AND t.guild_id = i.guild_id"
                " WHERE i.state = 'done' AND NOT i.scan_finished"
                " AND (NOT i.scanned OR i.scan_to < %s) AND NOT EXISTS ("
                " SELECT 1 FROM session_imports j JOIN transcript_sessions u"
                " ON u.id = j.session_id AND u.guild_id = j.guild_id"
                " WHERE j.campaign_id = i.campaign_id"
                " AND (u.started_at, j.session_id) < (t.started_at, i.session_id)"
                " AND (j.state IN ('waiting', 'working')"
                " OR (j.state = 'done' AND NOT j.scan_finished)))"
                " ORDER BY t.started_at, i.session_id LIMIT %s",
                (int(time.time()) - STALE_SCAN_S, limit),
            )
            return [
                Ready(
                    r["campaign_id"],
                    r["session_id"],
                    int(r["started_at"]),
                    bool(r["write_recap"]),
                    r["import_id"],
                )
                for r in await cur.fetchall()
            ]

    async def pending_scans(self, guild_id: int, campaign_id: str) -> int:
        """How many imports of the campaign still wait to be cleaned or scanned: the batch is
        over when none do."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM session_imports WHERE campaign_id = %s"
                " AND (state IN ('waiting', 'working') OR (state = 'done' AND NOT scan_finished))",
                (campaign_id,),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def claim_scan(self, guild_id: int, session_id: str, began: int) -> bool:
        """Take the name scan of this import before doing it, so two bots never both propose its
        names; `began` starts its window. A claim that never finished (the bot stopped) can be
        taken again after `STALE_SCAN_S`. False when it is taken."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET scanned = TRUE, scan_from = %s, scan_to = %s"
                " WHERE session_id = %s AND NOT scan_finished"
                " AND (NOT scanned OR scan_to < %s)",
                (began, began, session_id, began - STALE_SCAN_S),
            )
            return bool(cur.rowcount)

    async def renew_scan(self, guild_id: int, session_id: str, now: int) -> bool:
        """Keep a running scan's claim fresh while it does slow work (the story extractor's AI
        calls), so no other round retakes it after `STALE_SCAN_S`. A running claim's `scan_to` is
        its last sign of life (the window's start, `scan_from`, stays where the scan began).
        False when the claim is not running (it finished, or was given up)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET scan_to = %s"
                " WHERE session_id = %s AND scanned AND NOT scan_finished",
                (now, session_id),
            )
            return bool(cur.rowcount)

    async def unextracted(self, guild_id: int, limit: int = 20) -> list[Ready]:
        """For each campaign, its earliest scanned import that the story extractor has not
        finished reading (a call limit stopped it, or it failed part way). One for each campaign,
        oldest first, so a campaign that cannot be read never crowds out another and a campaign's
        later sessions wait for its earlier ones. The caller checks which lines are readable."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (i.campaign_id) i.campaign_id, i.session_id, i.write_recap,"
                " t.started_at, i.import_id"
                " FROM session_imports i JOIN transcript_sessions t"
                " ON t.id = i.session_id AND t.guild_id = i.guild_id"
                f" WHERE {_UNREAD}"
                " ORDER BY i.campaign_id, t.started_at, i.session_id",
            )
            first = [
                Ready(
                    r["campaign_id"],
                    r["session_id"],
                    int(r["started_at"]),
                    bool(r["write_recap"]),
                    r["import_id"],
                )
                for r in await cur.fetchall()
            ]
        return sorted(first, key=lambda r: (r.started_at, r.session_id))[:limit]

    async def finished_imports(self, guild_id: int) -> list[tuple[str, str, int]]:
        """(campaign, import id, sessions the story read has not finished) for each import whose
        every session has finished its name scan (or failed to clean), scanned on or after
        `READ_IMPORTS_FROM` (#3314). The caller tells the DM once for each."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT i.campaign_id, i.import_id,"
                f" count(*) FILTER (WHERE {_UNREAD}) AS unread"
                " FROM session_imports i JOIN transcript_sessions t"
                " ON t.id = i.session_id AND t.guild_id = i.guild_id"
                " WHERE i.guild_id = %s GROUP BY i.campaign_id, i.import_id"
                " HAVING bool_and(i.state = 'failed' OR (i.state = 'done' AND i.scan_finished))"
                f" AND max(i.scan_to) >= {READ_IMPORTS_FROM}"
                " ORDER BY min(t.started_at), i.import_id",
                (guild_id,),
            )
            return [
                (str(r["campaign_id"]), str(r["import_id"]), int(r["unread"]))
                for r in await cur.fetchall()
            ]

    async def count_unextracted(self, guild_id: int) -> dict[str, int]:
        """How many scanned imports of each campaign the story extractor has not finished."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT i.campaign_id, count(*) AS n FROM session_imports i"
                " JOIN transcript_sessions t ON t.id = i.session_id AND t.guild_id = i.guild_id"
                f" WHERE {_UNREAD} GROUP BY i.campaign_id",
            )
            return {r["campaign_id"]: int(r["n"]) for r in await cur.fetchall()}

    async def set_story(
        self,
        guild_id: int,
        session_id: str,
        state: str,
        now: int,
        *,
        part: int | None = None,
        parts: int | None = None,
    ) -> None:
        """Where the story read of this import is (#3307): 'reading' (batch `part` of `parts`),
        'waiting' (the limit, or a failed batch, stopped it; it carries on by itself) or 'read'.
        Counts and times only. It started when it first went to 'reading' (or went back to an
        earlier batch: a new try after a restart); every call is a move."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET story_moved_at = %s,"
                " story_started_at = CASE WHEN %s = 'reading' AND (story_state <> 'reading'"
                " OR coalesce(%s, 0) < story_part) THEN %s ELSE story_started_at END,"
                " story_part = coalesce(%s, story_part), story_parts = coalesce(%s, story_parts),"
                " story_state = %s WHERE guild_id = %s AND session_id = %s",
                (now, state, part, now, part, parts, state, guild_id, session_id),
            )

    async def finish_scan(self, guild_id: int, session_id: str, ended: int) -> None:
        """The scan ran to `ended` (seconds): its window is closed."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET scan_to = %s, scan_finished = TRUE"
                " WHERE session_id = %s",
                (ended, session_id),
            )

    async def release_scan(self, guild_id: int, session_id: str) -> int:
        """The scan failed: give it back for the next round and count the try. Returns the tries."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET scanned = FALSE, scan_tries = scan_tries + 1"
                " WHERE session_id = %s RETURNING scan_tries",
                (session_id,),
            )
            row = await cur.fetchone()
        return int(row["scan_tries"]) if row else 0

    async def give_up_scan(self, guild_id: int, session_id: str) -> None:
        """A scan that keeps failing is let go with an empty window, so it blocks no later session
        and the recap is not held for questions it never raised."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET scanned = TRUE, scan_finished = TRUE,"
                " scan_from = 0, scan_to = 0, scan_gave_up = TRUE WHERE session_id = %s",
                (session_id,),
            )

    async def ready_for_recap(self, guild_id: int, limit: int = PER_ROUND) -> list[Ready]:
        """Scanned imports whose recap the DM asked for and that has not been dealt with, in date
        order. Separate from the scan, so a recap that failed to start is tried again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT i.campaign_id, i.session_id, i.write_recap, t.started_at, i.import_id"
                " FROM session_imports i JOIN transcript_sessions t"
                " ON t.id = i.session_id AND t.guild_id = i.guild_id"
                " WHERE i.scan_finished AND i.write_recap AND NOT i.recap_started"
                " ORDER BY t.started_at, i.session_id LIMIT %s",
                (limit,),
            )
            return [
                Ready(
                    r["campaign_id"],
                    r["session_id"],
                    int(r["started_at"]),
                    bool(r["write_recap"]),
                    r["import_id"],
                )
                for r in await cur.fetchall()
            ]

    async def mark_recap_started(self, guild_id: int, session_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET recap_started = TRUE WHERE session_id = %s",
                (session_id,),
            )

    async def note_recap_failure(self, guild_id: int, session_id: str) -> int:
        """A recap could not be started: count the try. Returns the tries so far."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_imports SET recap_tries = recap_tries + 1"
                " WHERE session_id = %s RETURNING recap_tries",
                (session_id,),
            )
            row = await cur.fetchone()
        return int(row["recap_tries"]) if row else 0

    async def window(self, guild_id: int, session_id: str) -> tuple[int, int] | None:
        """When this import's name scan ran, or None for a session that was not imported (or
        whose scan has not run)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT scan_from, scan_to FROM session_imports WHERE session_id = %s AND scanned",
                (session_id,),
            )
            r = await cur.fetchone()
        return (int(r["scan_from"]), int(r["scan_to"])) if r else None

    async def scan_lines(self, guild_id: int, session_id: str) -> list[tuple[int, str]]:
        """(speaker, words) of the session's game talk, cleaned where the cleaner changed them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT user_id, COALESCE(text, heard) AS words FROM transcript_lines"
                " WHERE session_id = %s AND sidebar IS NULL AND topic = %s ORDER BY started_ms, id",
                (session_id, GAME),
            )
            return [(int(r["user_id"]), r["words"]) for r in await cur.fetchall()]

    async def back_to_waiting(self, guild_id: int, session_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET state = 'waiting' WHERE session_id = %s"
                " AND state = 'working'",
                (session_id,),
            )

    async def set_total(self, guild_id: int, session_id: str, total: int) -> None:
        """How many lines this session's cleaning has to get through, for the page's "n of m"
        (#2564). Counts only; starting again from the top starts every count again, the words and
        AI calls too (#3534), since the run goes over every line again with its windows renewed."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_imports SET step_total = %s, step_done = 0, words_done = 0,"
                " calls_made = 0 WHERE session_id = %s",
                (total, session_id),
            )

    async def apply(
        self,
        guild_id: int,
        session_id: str,
        cleaned: Sequence[tuple[int, str | None, str]],
        *,
        words: int = 0,
        calls: int = 0,
    ) -> None:
        """Save (line id, cleaned text or None when unchanged, topic) for lines of this session.
        `words` and `calls`: the words this chunk handled and the AI calls it made, added to the
        session's running counts for the page (#3534); counts only, in the same statement as the
        lines done."""
        if not cleaned:
            return
        async with self._db.guild(guild_id, by_dmbot=True) as conn:
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
            # (the row's own trigger stamps `updated_at`: this chunk is a step forward)
            await conn.execute(
                "UPDATE session_imports SET step_done = step_done + %s,"
                " words_done = words_done + %s, calls_made = calls_made + %s"
                " WHERE session_id = %s",
                (len(cleaned), max(words, 0), max(calls, 0), session_id),
            )


class ImportJob:
    def __init__(
        self,
        store: ImportStore,
        lookup: Lookup,
        topic_ai: Completes | None,
        consenting: Consenting,
        recorded: Recorded | None = None,
    ) -> None:
        """`consenting`: who is recorded now in a server (`ConsentStore.consenting`: the current
        terms, the outside engine and the stops held back). Asked before every chunk and every AI
        call, because a person can stop at any moment during a long job. `recorded`: the same for
        one campaign's stored sessions, which also counts the people the uploader confirmed
        (`ConsentStore.recorded_in`, #3305); without it, `consenting` alone counts."""
        self.store, self.lookup, self.topic_ai = store, lookup, topic_ai
        self.consenting = consenting
        self.recorded = recorded

    async def _agreed(self, guild_id: int, campaign_id: str) -> frozenset[int]:
        if self.recorded is not None:
            return await self.recorded(guild_id, campaign_id)
        return await self.consenting(guild_id)

    async def run_round(self, guild_id: int) -> int:
        """Clean the waiting imports of one server (first taking back any a stopped bot left
        half done). How many were done."""
        await self.store.resume(guild_id)
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
            # Every AI call of this cleaning belongs to this session's import, for the DM's live
            # window on the import page (#3710); the import page knows its sessions by this id.
            with ai_feed.job(guild_id, campaign_id, "import", session_id, step=TIDYING):
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
        await self.store.set_total(guild_id, session_id, len(rows))
        windows_left = MAX_WINDOWS
        failures = 0
        partly = False
        parts = max(1, -(-len(rows) // CHUNK_LINES))
        for start in range(0, len(rows), CHUNK_LINES):
            chunk = rows[start : start + CHUNK_LINES]
            ai_feed.step(f"{TIDYING} (part {start // CHUNK_LINES + 1} of {parts})")
            agreed = await self._agreed(guild_id, campaign_id)  # asked again for every chunk
            texts: list[str | None] = []
            for row in chunk:
                fixed = row.heard
                if row.user_id in agreed:  # someone who stopped being recorded is not worked on
                    try:
                        fixed = clean(lookup, row.heard, unsure=False).text
                    except Exception:
                        fixed = row.heard  # a line the cleaner can't read stays as heard
                texts.append(None if fixed == row.heard else fixed)
            topics = [GAME] * len(chunk)
            windows_before = windows_left
            if self.topic_ai is not None:
                topics, windows_left, failures, skipped = await self._topics(
                    guild_id,
                    campaign_id,
                    [(r.user_id, t or r.heard) for t, r in zip(texts, chunk, strict=True)],
                    windows_left,
                    failures,
                )
                partly = partly or skipped
            await self.store.apply(
                guild_id,
                session_id,
                [(r.id, t, topic) for r, t, topic in zip(chunk, texts, topics, strict=True)],
                # (only lines of people still recorded were worked on)
                words=sum(len(r.heard.split()) for r in chunk if r.user_id in agreed),
                calls=windows_before - windows_left,  # (a window is one AI call)
            )
            await asyncio.sleep(0)  # the live table's work goes first between chunks
        try:
            done = await self.store.settle(guild_id, campaign_id, session_id)
            log.info(  # counts only
                "Import off-topic filter: kept %d, turned back %d, left out %d",
                done.kept,
                done.turned_back,
                done.left_out,
            )
        except Exception:  # never costs the import its cleaning
            log.exception("Import: couldn't settle the off-topic labels")
        await self.store.finish(guild_id, session_id, ok=True, reason=PARTLY if partly else "")
        log.info("Import: cleaned a session of %d lines", len(rows))  # counts only

    async def _topics(
        self,
        guild_id: int,
        campaign_id: str,
        lines: Sequence[tuple[int, str]],
        windows_left: int,
        failures: int,
    ) -> tuple[list[str], int, int, bool]:
        """The topics of these (speaker, words) lines, a window at a time. Who is recorded is asked
        again right before every AI call, and a line of someone who stopped is not sent. A window
        that fails, a session out of windows, or an AI that failed several windows in a row leaves
        its lines as game talk: nothing is left out on a guess. Returns the topics, the windows and
        failures left over, and whether the AI was not asked about some line it could have been."""
        out = [GAME] * len(lines)
        ai = self.topic_ai
        if ai is None:
            return out, windows_left, failures, False
        skipped = False
        for start in range(0, len(lines), WINDOW_LINES):
            if windows_left <= 0 or failures >= TOPIC_FAILURES_BEFORE_REST:
                skipped = True
                break
            agreed = await self._agreed(guild_id, campaign_id)
            window = lines[start : start + WINDOW_LINES]
            sent = [(i, text) for i, (user, text) in enumerate(window) if user in agreed]
            if not sent:
                continue
            windows_left -= 1
            try:
                topics, _reply = await asyncio.wait_for(
                    classify(ai, [t for _, t in sent]), TOPIC_CALL_TIMEOUT_S
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
        return out, windows_left, failures, skipped
