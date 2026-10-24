"""Stored session transcripts (#41, #125).

Every session gets a row, and every piece of speech DMbot wrote down from someone who
agreed is a line. Lines arrive in batches (`TranscriptBuffer`, saved every few seconds),
so a session never waits on the database. Each save also updates the session's line
count and speakers, so listing sessions never reads their lines. The data types and the
buffer are in `dmbot.transcript.models`, which needs no database. Like every store,
reads and writes go through `Database.guild`, so Postgres only shows that server's rows.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database, row_int
from dmbot.transcript.export import UNKNOWN_SPEAKER, clean_name
from dmbot.transcript.models import Line, Lineage, TranscriptSession, line_ref
from dmbot.transcript.respell import put_back, respell
from dmbot.transcript.topics import GAME, OFF_TOPIC, Spoken, lean_to_keeping

RECENT_SESSIONS = 25  # Discord's limit for a menu
MAX_RESPELLED = 5000  # lines one run of a fixed spelling rewrites; Finish does the rest
PAGE = 2000  # candidate lines read at a time
MAX_SCANNED = 100_000  # candidate lines looked at in one run, rewritten or not


# The lines a respelling may reach (the filter shared by the pages and the count): this campaign's
# ended sessions, speech only, saying the name's longest word, not already done by this fix.
def _candidates(one_session: bool = False) -> str:
    # `one_session` (the name pass, #2628): only the session whose id is the next parameter after
    # the campaign's, whatever its end (it is being finished now), instead of every ended session.
    return (
        " FROM transcript_lines l JOIN transcript_sessions s ON s.id = l.session_id"
        " WHERE s.guild_id = %s AND s.campaign_id = %s"
        + (" AND s.id = %s" if one_session else " AND s.ended_at IS NOT NULL")
        + " AND l.sidebar IS NULL AND NOT l.typed"
        " AND strpos(lower(coalesce(l.text, l.heard)), lower(%s)) > 0"
        " AND NOT EXISTS (SELECT 1 FROM transcript_respells r"
        "   WHERE r.line_id = l.id AND r.campaign_id = %s AND r.batch = %s)"
    )


@dataclass(frozen=True, slots=True)
class PastRespell:
    """What one run of a fixed spelling did to the past transcripts (#2170)."""

    lines: int
    sessions: int
    remaining: int  # more lines that would change: the next run does them
    run: int  # this run's number within the fix, 0 when it wrote nothing
    # The spellings it replaced and how many times each (the name pass's report, #2628): words
    # from a transcript, for the campaign's DM only; never logged.
    forms: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Settled:
    """How the off-topic filter's end-of-session pass treated a session's spoken lines (#2567):
    counts only, for the logs, so the filter can be tuned on real sessions."""

    kept: int  # lines the filter kept in the first place (game or table talk)
    turned_back: int  # lines the filter had left out and the pass kept after all
    left_out: int  # lines still left out
    put_back: int  # lines a DM had put back (never changed by the pass)


@dataclass(frozen=True, slots=True)
class Deleted:
    """What "Delete my past transcripts" removed in one server (#1532)."""

    lines: int
    sessions: int  # sessions that had at least one of the person's lines


@dataclass(frozen=True, slots=True)
class Around:
    """A line and its neighbours, each (speaker's Discord ID, words)."""

    before: tuple[int, str] | None
    line: tuple[int, str]
    after: tuple[int, str] | None


# In an imported session only game talk counts (the name scan reads it so); a live session's
# lines all count. Spelled out because the import's mark lives in its own table.
GAME_IN_IMPORTS = (
    "(topic = 'game' OR NOT EXISTS (SELECT 1 FROM session_imports i"
    " WHERE i.guild_id = transcript_lines.guild_id AND i.session_id = transcript_lines.session_id))"
)


class TranscriptStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """The database, for the other stores that work on a session's lines (imports, #2105)."""
        return self._db

    async def open_session(
        self, guild_id: int, campaign_id: str, started_at: int, engine: str = ""
    ) -> str:
        """The session's ID, creating its row. A session resumed after a restart (same
        campaign and start time) gets its own row back. `engine`: which speech-to-text
        writes it down ("engine model host"), added if it's new to the session."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, engines)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (campaign_id, guild_id, started_at)"
                " DO UPDATE SET ended_at = NULL, engines = CASE"
                "   WHEN EXCLUDED.engines = '{}' OR transcript_sessions.engines @> EXCLUDED.engines"
                "   THEN transcript_sessions.engines"
                "   ELSE transcript_sessions.engines || EXCLUDED.engines END"
                " RETURNING id",
                (uuid.uuid4().hex, guild_id, campaign_id, started_at, [engine] if engine else []),
            )
            row = await cur.fetchone()
            assert row is not None
            return str(row["id"])

    async def extraction(self, guild_id: int, session_id: str) -> tuple[int, int]:
        """How far the story extractor got in this session (#1367): (when the last line it read
        began, in Unix milliseconds; AI calls used). (0, 0) for a session never read, or one that
        isn't here."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT extracted_through_ms, extractor_calls FROM transcript_sessions"
                " WHERE id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        return (int(row["extracted_through_ms"]), int(row["extractor_calls"])) if row else (0, 0)

    async def record_extraction(
        self, guild_id: int, session_id: str, *, through_ms: int, calls: int, now: int
    ) -> None:
        """The extractor ran: it has read every line that began up to `through_ms` (never
        backwards) and made `calls` more AI calls, at `now`."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET"
                " extracted_through_ms = greatest(extracted_through_ms, %s),"
                " extractor_calls = extractor_calls + %s,"
                " extracted_at = CASE WHEN %s > 0 THEN %s ELSE extracted_at END WHERE id = %s",
                (through_ms, calls, calls, now, session_id),
            )

    async def extractor_calls_since(self, guild_id: int, campaign_id: str, since: int) -> int:
        """AI calls the extractor made for this campaign in sessions it last ran in since
        `since` (a Unix time): the daily ceiling's count (#1367). A session counts in full at
        the time it last ran, which is close enough for a ceiling and needs no extra table."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT coalesce(sum(extractor_calls), 0) AS n FROM transcript_sessions"
                " WHERE campaign_id = %s AND extracted_at >= %s",
                (campaign_id, since),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def record_names(
        self, guild_id: int, session_id: str, names: Mapping[int, str | None]
    ) -> None:
        """Keep these speakers' display names for the website's transcripts (#2337). `None`: they
        are not in the server any more, kept as "" so DMbot does not look again. A name already
        kept is not replaced, and only people with lines in the session get one, so a name never
        outlives its lines (`delete_person` and `remove_lines` take it out). It is the name when
        DMbot first saved the person's words, or for an older session the one it found at the
        start-up fill-in."""
        if not names:
            return
        # (a name that is nothing once cleaned is kept as not found, never as "Someone")
        kept = {
            str(u): ""
            if not n or (clean_name(n) == UNKNOWN_SPEAKER and n.strip() != UNKNOWN_SPEAKER)
            else clean_name(n)
            for u, n in names.items()
        }
        async with self._db.guild(guild_id) as conn:
            # The session's row is locked first, as `delete_person` does, so a person's lines can't
            # be deleted between the check below and the write (the statement's own snapshot
            # would not see it).
            await conn.execute(
                "SELECT 1 FROM transcript_sessions WHERE id = %s FOR UPDATE", (session_id,)
            )
            await conn.execute(
                "UPDATE transcript_sessions s SET speaker_names = ("
                "   SELECT COALESCE(jsonb_object_agg(j.key, j.value), '{}'::jsonb)"
                "   FROM jsonb_each(%s::jsonb) AS j"
                "   WHERE EXISTS (SELECT 1 FROM transcript_lines l"
                "     WHERE l.session_id = s.id AND l.user_id = j.key::bigint)"
                " ) || s.speaker_names"
                " WHERE s.id = %s AND s.transcripts_deleted_at IS NULL",
                (json.dumps(kept), session_id),
            )

    async def unnamed(
        self, guild_id: int, *, limit: int = 200
    ) -> list[tuple[str, tuple[int, ...]]]:
        """Sessions with speakers whose name was never kept (sessions from before #2337, or one a
        restart cut short): (session ID, those speakers' IDs), at most `limit` sessions, oldest
        first. The bot's one-off fill-in at start-up reads this; it finds nothing once done."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.id, ARRAY(SELECT u FROM unnest(s.speakers) AS u"
                "   WHERE NOT s.speaker_names ? u::text ORDER BY u) AS missing"
                " FROM transcript_sessions s WHERE s.guild_id = %s AND s.line_count > 0"
                " AND s.transcripts_deleted_at IS NULL"
                " AND EXISTS (SELECT 1 FROM unnest(s.speakers) AS u"
                "   WHERE NOT s.speaker_names ? u::text)"
                " ORDER BY s.started_at, s.id LIMIT %s",
                (guild_id, limit),
            )
            return [
                (str(r["id"]), tuple(int(u) for u in r["missing"])) for r in await cur.fetchall()
            ]

    async def end_session(self, guild_id: int, session_id: str, ended_at: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET ended_at = %s WHERE id = %s",
                (ended_at, session_id),
            )

    async def add_lines(self, guild_id: int, session_id: str, lines: Sequence[Line]) -> list[int]:
        """Save lines, and the session's counts, in one transaction; the new rows' IDs."""
        if not lines:
            return []
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO transcript_lines"
                " (guild_id, session_id, started_ms, user_id, heard, text, duration_ms, topic,"
                " entity, sidebar, sidebar_ref, sidebar_reply_to, sidebar_via, sidebar_stt,"
                " sidebar_model, sidebar_prompt, sidebar_sources, typed, image)"
                " SELECT %s, %s, l.started_ms, l.user_id, l.heard, l.text, l.duration_ms,"
                " l.topic, l.entity, l.sidebar, l.ref, l.reply_to, l.via, l.stt, l.model, l.prompt,"
                " l.sources::jsonb, l.typed, l.image"
                " FROM unnest(%s::bigint[], %s::bigint[], %s::text[], %s::text[], %s::int[],"
                " %s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],"
                " %s::text[], %s::text[], %s::text[], %s::text[], %s::boolean[], %s::boolean[])"
                " AS l(started_ms, user_id, heard, text, duration_ms, topic, entity, sidebar, ref,"
                " reply_to, via, stt, model, prompt, sources, typed, image)"
                " RETURNING id",
                (
                    guild_id,
                    session_id,
                    [line.started_ms for line in lines],
                    [line.user_id for line in lines],
                    [line.heard for line in lines],
                    # NULL while the cleaned text is the same as heard (no names fixed)
                    [None if line.text == line.heard else line.text for line in lines],
                    [line.duration_ms for line in lines],
                    [line.topic for line in lines],
                    [line.entity or None for line in lines],
                    [line.sidebar or None for line in lines],
                    *_lineage_columns(lines),
                    [line.typed for line in lines],
                    [line.image for line in lines],
                ),
            )
            ids = [int(r["id"]) for r in await cur.fetchall()]
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = line_count + %s,"
                " speakers = ARRAY(SELECT DISTINCT u FROM unnest(speakers || %s::bigint[]) AS u"
                "   ORDER BY u)"
                " WHERE id = %s",
                # Sidebar lines (#935) are not speech: they don't make a session show up in
                # /transcript, or list the DM as a speaker. Typed table chat (#1118) is the
                # table's talk, so it counts like speech.
                (
                    sum(not line.sidebar for line in lines),
                    sorted({line.user_id for line in lines if not line.sidebar}),
                    session_id,
                ),
            )
        return ids

    async def relabel_line(
        self,
        guild_id: int,
        session_id: str,
        user_id: int,
        started_ms: int,
        text: str,
        *,
        image: bool = False,
    ) -> int:
        """A saved line's cleaned words changed (an Undo, #296). What was heard never
        changes; `text` goes back to NULL when it's the same as heard. How many lines
        changed (0: it wasn't saved). `image`: the line is a described picture (#2450), which is
        a typed line, so it is kept apart from the person's speech."""
        # (two fixed statements, so no part of a query is ever built from input)
        if image:
            query = (
                "UPDATE transcript_lines SET text = NULLIF(%s, heard)"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND typed AND image"
            )
        else:
            query = (
                "UPDATE transcript_lines SET text = NULLIF(%s, heard)"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND NOT typed"
            )
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(query, (text, session_id, user_id, started_ms))
            return cur.rowcount

    async def image_line(
        self, guild_id: int, session_id: str, user_id: int, started_ms: int
    ) -> tuple[str, str] | None:
        """(what the AI first read, the line as it reads now) for a described picture of this
        person's, or None (not theirs, not a picture, or not saved yet)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT heard, coalesce(text, heard) AS text FROM transcript_lines"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND typed AND image ORDER BY id LIMIT 1",
                (session_id, user_id, started_ms),
            )
            r = await cur.fetchone()
        return None if r is None else (str(r["heard"]), str(r["text"]))

    async def set_topic(
        self,
        guild_id: int,
        session_id: str,
        user_id: int,
        started_ms: int,
        topic: str,
        *,
        typed: bool = False,
    ) -> int:
        """A saved line's topic, from the off-topic filter (#52). What was heard never
        changes. How many lines changed (0: it wasn't saved). `typed`: typed chat and speech
        of one person can share a millisecond, so the kind is part of the match (#2447)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines SET topic = %s"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND typed = %s",
                (topic, session_id, user_id, started_ms, typed),
            )
            return cur.rowcount

    async def set_entity(
        self, guild_id: int, session_id: str, user_id: int, started_ms: int, entity: str
    ) -> int:
        """A saved line's entity: who it was said as (#53). What was heard never changes.
        How many lines changed (0: it wasn't saved)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines SET entity = %s"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL",
                (entity or None, session_id, user_id, started_ms),
            )
            return cur.rowcount

    async def set_topics(
        self,
        guild_id: int,
        session_id: str,
        user_id: int,
        started: Collection[int],
        topic: str,
        *,
        typed: bool = False,
    ) -> int:
        """Several of one person's saved lines get a topic, in one statement (#677: Put
        it back). What was heard never changes. How many lines changed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines SET topic = %s"
                " WHERE session_id = %s AND user_id = %s AND started_ms = ANY(%s)"
                " AND sidebar IS NULL AND typed = %s",
                (topic, session_id, user_id, sorted(started), typed),
            )
            return cur.rowcount

    async def settle_topics(
        self, guild_id: int, session_id: str, dm_ids: Collection[int]
    ) -> Settled:
        """The filter leans to keeping (#2567): once the session is over, a short stretch the AI
        called off-topic between game talk, and the DM's lines in any stretch that is not long,
        become game talk again (`topics.lean_to_keeping`). Marked `leaned`, so a count and an Undo
        can tell them apart, and never a line a DM decided. Speech only; what was heard never
        changes. Safe to run twice."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, started_ms, duration_ms, topic, leaned,"
                " topic_by IS NOT NULL AS decided"
                " FROM transcript_lines WHERE session_id = %s AND sidebar IS NULL AND NOT typed"
                " ORDER BY started_ms, id",
                (session_id,),
            )
            rows = await cur.fetchall()
            # A line this pass already turned back counts as the filter's off-topic call again, so
            # the same lines give the same answer however many times it runs.
            spoken = [
                Spoken(
                    int(r["user_id"]),
                    int(r["started_ms"]),
                    int(r["duration_ms"]) / 1000,
                    "",
                    OFF_TOPIC if r["leaned"] and not r["decided"] else r["topic"],
                    decided=bool(r["decided"]),
                )
                for r in rows
            ]
            leaned = lean_to_keeping(spoken, dm_ids)
            back = [
                int(r["id"])
                for r, was, new in zip(rows, spoken, leaned, strict=True)
                if was.topic == OFF_TOPIC and new.topic == GAME and r["topic"] == OFF_TOPIC
            ]
            if back:
                await conn.execute(
                    "UPDATE transcript_lines SET topic = 'game', leaned = TRUE"
                    " WHERE session_id = %s AND id = ANY(%s) AND topic_by IS NULL",
                    (session_id, back),
                )
        left = sum(1 for x in leaned if x.topic == OFF_TOPIC)
        put = sum(1 for r in rows if r["decided"] and r["topic"] != OFF_TOPIC)
        turned = sum(
            1
            for was, new in zip(spoken, leaned, strict=True)
            if was.topic == OFF_TOPIC != new.topic
        )
        return Settled(len(rows) - left - put - turned, turned, left, put)

    async def unread_put_back(
        self, guild_id: int, campaign_id: str, limit: int = 500
    ) -> list[Line]:
        """Lines a DM put back after their session that the names and story-memory readers have not
        read yet (#2567), oldest first: the queue a late line waits in. Only speech of a speaker
        who is still recorded is the caller's check to make (consent is asked again when the
        words are sent). Read them, then `mark_read`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT l.id, l.user_id, l.started_ms, l.heard, l.text, l.duration_ms"
                " FROM transcript_lines l JOIN transcript_sessions s ON s.id = l.session_id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND l.reread"
                " ORDER BY l.started_ms, l.id LIMIT %s",
                (guild_id, campaign_id, limit),
            )
            return [
                Line(
                    int(r["started_ms"]),
                    int(r["user_id"]),
                    r["heard"],
                    r["text"] or r["heard"],
                    int(r["duration_ms"]),
                )
                for r in await cur.fetchall()
            ]

    async def mark_read(
        self, guild_id: int, campaign_id: str, user_id: int, started: Collection[int]
    ) -> int:
        """The readers have read these put-back lines of one person (#2567). How many."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines l SET reread = FALSE FROM transcript_sessions s"
                " WHERE s.id = l.session_id AND s.guild_id = %s AND s.campaign_id = %s"
                " AND l.user_id = %s AND l.started_ms = ANY(%s) AND l.reread",
                (guild_id, campaign_id, user_id, sorted(started)),
            )
            return cur.rowcount

    async def remove_lines(self, guild_id: int, session_id: str, ids: Collection[int]) -> None:
        """Take saved lines back out (someone pressed Stop while they were being saved),
        and count the session again."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM transcript_lines WHERE session_id = %s AND id = ANY(%s)",
                (session_id, list(ids)),
            )
            await conn.execute(
                "UPDATE transcript_sessions SET"
                " line_count = (SELECT count(*) FROM transcript_lines"
                "   WHERE session_id = %s AND sidebar IS NULL),"
                " speakers = ARRAY(SELECT DISTINCT user_id FROM transcript_lines"
                "   WHERE session_id = %s AND sidebar IS NULL ORDER BY user_id),"
                # a name never outlives its person's lines (#2337)
                " speaker_names = (SELECT COALESCE(jsonb_object_agg(j.key, j.value), '{}'::jsonb)"
                "   FROM jsonb_each(speaker_names) AS j WHERE EXISTS (SELECT 1"
                "   FROM transcript_lines l WHERE l.session_id = %s AND l.user_id = j.key::bigint))"
                " WHERE id = %s",
                (session_id, session_id, session_id, session_id),
            )

    async def sessions(
        self, guild_id: int, campaign_id: str, *, limit: int = RECENT_SESSIONS
    ) -> list[TranscriptSession]:
        """The campaign's sessions that have lines, newest first, numbered from the
        first (Session 1). Reads sessions only, never their lines. A session whose transcript
        was deleted (#1533) is not listed but still has its number, so the others never shift."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM (SELECT *,"
                " row_number() OVER (ORDER BY started_at, id) AS number"
                " FROM transcript_sessions WHERE campaign_id = %s"
                " AND (line_count > 0 OR transcripts_deleted_at IS NOT NULL)) AS s"
                " WHERE line_count > 0 ORDER BY started_at DESC, id DESC LIMIT %s",
                (campaign_id, limit),
            )
            return [_session(r) for r in await cur.fetchall()]

    async def latest_before(
        self, guild_id: int, campaign_id: str, before: int
    ) -> tuple[int, bool] | None:
        """The campaign's latest session that began before `before`: when it began, and whether
        its transcript is gone (deleted by the keep-days limit, #1533). Counts a session whose lines
        were deleted, which `sessions` does not list; a session that never had lines is no session
        (#2253: the previous session, for finding a retelling of it)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_at, line_count = 0 AS gone FROM transcript_sessions"
                " WHERE campaign_id = %s AND started_at < %s"
                " AND (line_count > 0 OR transcripts_deleted_at IS NOT NULL)"
                " ORDER BY started_at DESC, id DESC LIMIT 1",
                (campaign_id, before),
            )
            row = await cur.fetchone()
        return None if row is None else (int(row["started_at"]), bool(row["gone"]))

    async def next_session_start(self, guild_id: int, campaign_id: str, after: int) -> int | None:
        """When the campaign's first session with lines that started after `after` began, or None.
        A session row is written before anyone speaks (and the one being played now has few
        lines), so only one with lines ends the window (#2169)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT min(started_at) AS at FROM transcript_sessions"
                " WHERE campaign_id = %s AND started_at > %s AND line_count > 0",
                (campaign_id, after),
            )
            row = await cur.fetchone()
        return None if row is None or row["at"] is None else int(row["at"])

    async def session_counts(self, guild_id: int) -> dict[str, int]:
        """Saved sessions (with at least one line) per campaign id in this server, in
        one query. Campaigns with none are left out."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, count(*) AS n FROM transcript_sessions"
                " WHERE line_count > 0 GROUP BY campaign_id"
            )
            return {r["campaign_id"]: r["n"] for r in await cur.fetchall()}

    async def session(self, guild_id: int, session_id: str) -> TranscriptSession | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.*, (SELECT count(*) FROM transcript_sessions e"
                "   WHERE e.campaign_id = s.campaign_id"
                "   AND (e.line_count > 0 OR e.transcripts_deleted_at IS NOT NULL)"
                "   AND (e.started_at, e.id) <= (s.started_at, s.id)) AS number"
                " FROM transcript_sessions s WHERE s.id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            return None if row is None else _session(row)

    async def around(
        self,
        guild_id: int,
        session_id: str,
        started_ms: int,
        user_id: int,
        speakers: Collection[int],
    ) -> Around | None:
        """One line of table speech and the one before and after it (#2329): what the review of a
        suggested name shows. Only `speakers` (the people who agree now) are read, the line
        itself and its neighbours: someone who stopped being recorded is not quoted even though
        their past lines stay. None when the line is not there any more (the transcript was
        deleted, or the person withdrew). The DM sidebar is skipped, so neighbours are what the
        table said. In an imported session only game talk is read, as the name scan reads it, so
        an off-topic line is never quoted next to a place (#2329)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, started_ms, user_id, coalesce(text, heard) AS text"
                " FROM transcript_lines WHERE session_id = %s AND sidebar IS NULL"
                f" AND {GAME_IN_IMPORTS} AND started_ms = %s AND user_id = %s"
                " AND user_id = ANY(%s) ORDER BY id LIMIT 1",
                (session_id, started_ms, user_id, list(speakers)),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            here = (int(row["started_ms"]), int(row["id"]))
            before = await conn.execute(
                "SELECT user_id, coalesce(text, heard) AS text FROM transcript_lines"
                f" WHERE session_id = %s AND sidebar IS NULL AND {GAME_IN_IMPORTS}"
                " AND (started_ms, id) < (%s, %s)"
                " AND user_id = ANY(%s) ORDER BY started_ms DESC, id DESC LIMIT 1",
                (session_id, *here, list(speakers)),
            )
            after = await conn.execute(
                "SELECT user_id, coalesce(text, heard) AS text FROM transcript_lines"
                f" WHERE session_id = %s AND sidebar IS NULL AND {GAME_IN_IMPORTS}"
                " AND (started_ms, id) > (%s, %s)"
                " AND user_id = ANY(%s) ORDER BY started_ms, id LIMIT 1",
                (session_id, *here, list(speakers)),
            )
            prev, nxt = await before.fetchone(), await after.fetchone()
        return Around(
            (int(prev["user_id"]), prev["text"]) if prev else None,
            (int(row["user_id"]), row["text"]),
            (int(nxt["user_id"]), nxt["text"]) if nxt else None,
        )

    async def lines(self, guild_id: int, session_id: str) -> list[Line]:
        """The session's lines in the order the speech started."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_ms, user_id, heard, coalesce(text, heard) AS text, duration_ms,"
                " topic, coalesce(entity, '') AS entity, coalesce(sidebar, '') AS sidebar,"
                " sidebar_ref, sidebar_reply_to,"
                " sidebar_via, sidebar_stt, sidebar_model, sidebar_prompt, sidebar_sources, typed,"
                " image"
                " FROM transcript_lines"
                " WHERE session_id = %s ORDER BY started_ms, id",
                (session_id,),
            )
            return [
                Line(
                    int(r["started_ms"]),
                    int(r["user_id"]),
                    r["heard"],
                    r["text"],
                    int(r["duration_ms"]),
                    r["topic"],
                    r["sidebar"],
                    _lineage(r),
                    r["entity"],
                    bool(r["typed"]),
                    bool(r["image"]),
                )
                for r in await cur.fetchall()
            ]

    # ---- A fixed spelling follows the cleaned transcripts (#2170) ----------------------------

    async def respell_names(
        self,
        guild_id: int,
        campaign_id: str,
        batch: int,
        old: str,
        new: str,
        longer: Collection[str],
        hidden: Collection[str] = (),
        *,
        now: int,
        forget_before: int,
        session_id: str | None = None,
    ) -> PastRespell:
        """Write `new` where this campaign's past cleaned lines say `old` as a whole name, in
        ONE transaction (so a failure changes nothing), at most `MAX_RESPELLED` lines in session
        order; `remaining` says how many more would change (run it again for the next run, which
        has its own Undo). Only the cleaned text: what was heard is never touched. Only this
        campaign's sessions that have ended (never the one being played, whose lines the Cleaner is
        still writing) and only lines of speech (not the DM sidebar, not typed lines). A match
        inside a longer name in `longer` is left alone, and so is a line the change would make say
        a name in `hidden`. The places are kept under `batch` and the run for `undo_respell`;
        places older than `forget_before` are dropped. `session_id`: only that session, ended or
        not (the name pass of a session being finished, #2628)."""
        # The longest run of letters and digits: an apostrophe or dash in the name may be written
        # with a look-alike (a curly one), so a prefilter on a whole word would miss it.
        longest = max(re.findall(r"\w+", old), key=len, default="")
        if not longest or old == new:
            return PastRespell(0, 0, 0, 0)
        updates: list[tuple[int, str]] = []
        rows: list[tuple[Any, ...]] = []
        sessions: set[str] = set()
        forms: Counter[str] = Counter()
        remaining = 0
        scope: tuple[Any, ...] = (
            (guild_id, campaign_id) if session_id is None else (guild_id, campaign_id, session_id)
        )
        async with self._db.guild(guild_id) as conn:
            # Two presses of Finish at once take turns, so no two runs share a number.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{campaign_id}:{batch}",)
            )
            await conn.execute(
                "DELETE FROM transcript_respells WHERE guild_id = %s AND campaign_id = %s"
                " AND made_at < %s",
                (guild_id, campaign_id, forget_before),
            )
            cur = await conn.execute(
                "SELECT coalesce(max(run), 0) + 1 AS run FROM transcript_respells"
                " WHERE campaign_id = %s AND batch = %s",
                (campaign_id, batch),
            )
            run = int((await cur.fetchone() or {"run": 1})["run"])
            after = (0, 0, 0)  # (session start, line time, line id) of the last line looked at
            scanned = 0
            while len(updates) < MAX_RESPELLED and scanned < MAX_SCANNED:
                # Pages of lines that may say the name, in session order, not yet done by this fix:
                # lines that never change (inside a longer name) cannot starve the window.
                cur = await conn.execute(
                    "SELECT l.id, l.session_id, l.user_id, l.started_ms, s.started_at,"
                    " coalesce(l.text, l.heard) AS cleaned"
                    + _candidates(session_id is not None)
                    + " AND (s.started_at, l.started_ms, l.id) > (%s, %s, %s)"
                    " ORDER BY s.started_at, l.started_ms, l.id LIMIT %s FOR UPDATE OF l",
                    (*scope, longest, campaign_id, batch, *after, PAGE),
                )
                page = await cur.fetchall()
                if not page:
                    break
                scanned += len(page)
                for row in page:
                    found = respell(str(row["cleaned"]), old, new, longer, hidden)
                    if found is not None and len(updates) >= MAX_RESPELLED:
                        break  # this line is the first of what is left
                    after = (int(row["started_at"]), int(row["started_ms"]), int(row["id"]))
                    if found is None:
                        continue
                    updates.append((int(row["id"]), found.text))
                    forms.update(found.was)
                    rows.append(
                        (
                            guild_id,
                            campaign_id,
                            batch,
                            run,
                            row["session_id"],
                            row["id"],
                            row["user_id"],
                            old,
                            new,
                            list(found.offsets),
                            list(found.was),
                            now,
                        )
                    )
                    sessions.add(str(row["session_id"]))
            # What is left, counted without locking: lines further on that say the name. Some may
            # never change (inside a longer name), so it is "up to" this many.
            cur = await conn.execute(
                "SELECT count(*) AS n"
                + _candidates(session_id is not None)
                + " AND (s.started_at, l.started_ms, l.id) > (%s, %s, %s)",
                (*scope, longest, campaign_id, batch, *after),
            )
            remaining = int((await cur.fetchone() or {"n": 0})["n"])
            if updates:
                await conn.execute(
                    "UPDATE transcript_lines t SET text = NULLIF(v.text, t.heard)"
                    " FROM unnest(%s::bigint[], %s::text[]) AS v(id, text) WHERE t.id = v.id",
                    ([u[0] for u in updates], [u[1] for u in updates]),
                )
                async with conn.cursor() as ins:
                    await ins.executemany(
                        "INSERT INTO transcript_respells (guild_id, campaign_id, batch, run,"
                        " session_id, line_id, user_id, old_text, new_text, offsets, was, made_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        rows,
                    )
        return PastRespell(
            len(updates),
            len(sessions),
            remaining,
            run if updates else 0,
            tuple(forms.most_common()),
        )

    # ---- PlotBot's name pass on a finished session (#2628) ------------------------------------

    async def mark_names_repaired(self, guild_id: int, session_id: str, at: int) -> None:
        """The session's name pass is done (or was not needed), so its recap may start."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET names_repaired_at = %s WHERE id = %s",
                (max(at, 1), session_id),
            )

    async def names_repaired(self, guild_id: int, campaign_id: str, started_at: int) -> bool:
        """Whether the name pass of the campaign's session that began at `started_at` is done.
        A session with no transcript (nothing to repair) counts as done."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT min(names_repaired_at) AS at FROM transcript_sessions"
                " WHERE guild_id = %s AND campaign_id = %s AND started_at = %s",
                (guild_id, campaign_id, started_at),
            )
            row = await cur.fetchone()
        return row is None or row["at"] is None or int(row["at"]) > 0

    async def respell_spellings(
        self, guild_id: int, campaign_id: str, batch: int
    ) -> tuple[str, str] | None:
        """The old and new spelling a fix wrote into past transcripts (to carry on with the rest),
        or None when it wrote nothing that can still be undone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT old_text, new_text FROM transcript_respells WHERE guild_id = %s"
                " AND campaign_id = %s AND batch = %s LIMIT 1",
                (guild_id, campaign_id, batch),
            )
            row = await cur.fetchone()
        return None if row is None else (str(row["old_text"]), str(row["new_text"]))

    async def respelled_olds(self, guild_id: int, campaign_id: str, batch: int) -> list[str]:
        """The spellings a batch replaced, each once (for the keep-as-heard rule an Undo saves)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT old_text FROM transcript_respells WHERE guild_id = %s"
                " AND campaign_id = %s AND batch = %s ORDER BY old_text",
                (guild_id, campaign_id, batch),
            )
            return [str(r["old_text"]) for r in await cur.fetchall()]

    async def undo_respell(
        self, guild_id: int, campaign_id: str, batch: int, run: int | None = None
    ) -> tuple[int, int]:
        """Put the old spelling back where `respell_names` wrote the new one, for that batch (all
        its runs, or just `run`). A line that was changed again since is left as it is. Returns
        (lines, sessions) put back."""
        changed = 0
        sessions: set[str] = set()
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM transcript_respells WHERE guild_id = %s AND campaign_id = %s"
                " AND batch = %s AND (%s::int IS NULL OR run = %s)"
                " RETURNING session_id, line_id, new_text, offsets, was",
                (guild_id, campaign_id, batch, run, run),
            )
            for row in await cur.fetchall():
                found = await conn.execute(
                    "SELECT id, heard, coalesce(text, heard) AS cleaned FROM transcript_lines"
                    " WHERE id = %s AND sidebar IS NULL AND NOT typed FOR UPDATE",
                    (row["line_id"],),
                )
                line = await found.fetchone()
                if line is None:
                    continue
                back = put_back(
                    str(line["cleaned"]), list(row["offsets"]), list(row["was"]), row["new_text"]
                )
                if back is None:
                    continue
                await conn.execute(
                    "UPDATE transcript_lines SET text = NULLIF(%s, heard) WHERE id = %s",
                    (back, line["id"]),
                )
                changed += 1
                sessions.add(str(row["session_id"]))
        return changed, len(sessions)

    # ---- Delete my past transcripts (#1532) ---------------------------------------------

    async def person_counts(self, guild_id: int, user_id: int) -> Deleted:
        """How many lines this person has in this server's transcripts, and in how many
        sessions: the numbers the warning shows. Both versions are one line; their DM-sidebar
        and table-question lines count (they are theirs too)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS lines, count(DISTINCT session_id) AS sessions"
                " FROM transcript_lines WHERE guild_id = %s AND user_id = %s",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
        return Deleted(int(row["lines"]), int(row["sessions"])) if row else Deleted(0, 0)

    async def delete_person(self, guild_id: int, user_id: int) -> Deleted:
        """Remove one person's words from every transcript in one server, in ONE transaction:
        if anything fails nothing is deleted.

        Goes: their lines (both versions, the sidebar questions and DMbot's replies to them,
        which carry their id), the maps they posted, kept in words (#2450), their fix suggestions in
        any state, the story-memory mentions
        that point at those lines (and the undo-log rows of those mentions), the name counts
        kept per speaker, and the story claims that cite them and were never confirmed. Each
        affected session's line count and speaker list are worked out again.

        Stays: other people's lines, the DM's confirmed facts and the claims they came from,
        recaps, and the session rows. Only this server and this person: every statement filters
        on both, and `Database.guild` hides every other server's rows as well."""
        async with self._db.guild(guild_id) as conn:
            # The sessions' rows are locked first, so a save adding lines at the same moment
            # can't make the recount below come out one batch short.
            await conn.execute(
                "SELECT 1 FROM transcript_sessions WHERE guild_id = %s AND id IN"
                " (SELECT session_id FROM transcript_lines WHERE guild_id = %s AND user_id = %s)"
                " ORDER BY id FOR UPDATE",
                (guild_id, guild_id, user_id),
            )
            # The time of the erase is kept (ids and a time, no words), so a copy made before it
            # is restored without these lines (#2605). Written even when nothing is left to
            # delete: a copy may still hold lines this server no longer does.
            await conn.execute(
                "INSERT INTO transcript_erasures (guild_id, user_id, erased_at)"
                " VALUES (%s, %s, extract(epoch FROM now())::BIGINT)"
                " ON CONFLICT (guild_id, user_id) DO UPDATE SET erased_at = EXCLUDED.erased_at",
                (guild_id, user_id),
            )
            await conn.execute(  # the name kept for the website's transcripts first (#2337)
                "UPDATE transcript_sessions SET speaker_names = speaker_names - %s"
                " WHERE guild_id = %s AND speaker_names ? %s",
                (str(user_id), guild_id, str(user_id)),
            )
            await conn.execute(
                "DELETE FROM transcript_suggestions WHERE guild_id = %s AND user_id = %s",
                (guild_id, user_id),
            )
            await conn.execute(  # where a fixed spelling was written into their lines (#2170)
                "DELETE FROM transcript_respells WHERE guild_id = %s AND user_id = %s",
                (guild_id, user_id),
            )
            await conn.execute(  # and the maps they posted, kept in words for MapBot (#2450)
                "DELETE FROM map_notes WHERE guild_id = %s AND user_id = %s",
                (guild_id, user_id),
            )
            await conn.execute(  # and the questions kept about their words (#1572)
                "DELETE FROM waiting_name_questions WHERE guild_id = %s AND speaker_id = %s",
                (guild_id, user_id),
            )
            cur = await conn.execute(
                "DELETE FROM transcript_lines WHERE guild_id = %s AND user_id = %s"
                " RETURNING session_id, started_ms",
                (guild_id, user_id),
            )
            gone = await cur.fetchall()
            if not gone:
                # Nothing written down, but leftovers that belong with their lines still go.
                await _forget_memory_traces(conn, guild_id, user_id, [])
                return Deleted(0, 0)
            sessions = sorted({str(r["session_id"]) for r in gone})
            await _forget_memory_traces(
                conn,
                guild_id,
                user_id,
                [line_ref(str(r["session_id"]), int(r["started_ms"]), user_id) for r in gone],
            )
            await conn.execute(
                "UPDATE transcript_sessions s SET"
                " line_count = (SELECT count(*) FROM transcript_lines l"
                "   WHERE l.session_id = s.id AND l.sidebar IS NULL),"
                " speakers = ARRAY(SELECT DISTINCT l.user_id FROM transcript_lines l"
                "   WHERE l.session_id = s.id AND l.sidebar IS NULL ORDER BY l.user_id)"
                " WHERE s.guild_id = %s AND s.id = ANY(%s)",
                (guild_id, sessions),
            )
            return Deleted(len(gone), len(sessions))

    # ---- Keep transcripts for (#1533) ---------------------------------------------------

    async def expired_sessions(self, guild_id: int, campaign_id: str, cutoff: int) -> list[str]:
        """Ids of the campaign's sessions that ended before `cutoff` (Unix seconds) and still
        have a transcript, oldest first. A session still running, or saved without an end, is
        never listed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
                " AND ended_at IS NOT NULL AND "
                + _KEPT_SINCE
                + " < %s AND transcripts_deleted_at IS NULL"
                " AND (line_count > 0 OR EXISTS (SELECT 1 FROM transcript_lines l"
                "   WHERE l.session_id = transcript_sessions.id))"
                " ORDER BY started_at, id",
                (guild_id, campaign_id, cutoff),
            )
            return [str(r["id"]) for r in await cur.fetchall()]

    async def delete_session_transcript(
        self, guild_id: int, campaign_id: str, session_id: str, cutoff: int, now: int
    ) -> int:
        """Delete one old session's transcript in ONE transaction (so one failing session rolls
        back only itself): its lines in both versions, its fix suggestions and the story-memory
        mentions that point at its lines; the count and speaker list are cleared and the row
        marked. The row stays with its number, start and end. Re-checks that the session ended
        before `cutoff` and is not already done, so a session that resumed meanwhile or a second
        run does nothing. Returns how many lines went (0: nothing to do)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND ended_at IS NOT NULL AND "
                + _KEPT_SINCE
                + " < %s AND transcripts_deleted_at IS NULL FOR UPDATE",
                (guild_id, campaign_id, session_id, cutoff),
            )
            if await cur.fetchone() is None:
                return 0
            cur = await conn.execute(
                "DELETE FROM transcript_lines WHERE guild_id = %s AND session_id = %s"
                " RETURNING started_ms, user_id",
                (guild_id, session_id),
            )
            gone = await cur.fetchall()
            await conn.execute(
                "DELETE FROM transcript_suggestions WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            await conn.execute(
                "DELETE FROM waiting_name_questions WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            await conn.execute(
                "DELETE FROM transcript_respells WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            await _forget_mentions(
                conn,
                guild_id,
                [line_ref(session_id, int(r["started_ms"]), int(r["user_id"])) for r in gone],
                keep_pending=True,
            )
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 0, speakers = '{}',"
                " speaker_names = '{}', transcripts_deleted_at = %s"
                " WHERE guild_id = %s AND id = %s",
                (now, guild_id, session_id),
            )
            return len(gone)


# How long a session has been kept: an imported one from the day it was imported (a 2024 game
# loaded today is not deleted tonight by a 30-day setting), any other from when it ended (#2105).
_KEPT_SINCE = (
    "coalesce((SELECT i.imported_at FROM session_imports i"
    " WHERE i.session_id = transcript_sessions.id), ended_at)"
)


def _lineage_columns(lines: Sequence[Line]) -> list[list[str | None]]:
    """The sidebar lineage of each line as one list per column; NULL for table speech."""

    def column(pick: Callable[[Line], str]) -> list[str | None]:
        return [pick(line) or None if line.sidebar else None for line in lines]

    return [
        column(lambda x: x.lineage.ref),
        column(lambda x: x.lineage.reply_to),
        column(lambda x: x.lineage.via),
        column(lambda x: x.lineage.stt),
        column(lambda x: x.lineage.model),
        column(lambda x: x.lineage.prompt),
        [
            json.dumps(list(x.lineage.sources)) if x.sidebar and x.lineage.sources else None
            for x in lines
        ],
    ]


def _lineage(row: dict[str, Any]) -> Lineage:
    sources = row["sidebar_sources"] or ()
    return Lineage(
        ref=row["sidebar_ref"] or "",
        reply_to=row["sidebar_reply_to"] or "",
        via=row["sidebar_via"] or "",
        stt=row["sidebar_stt"] or "",
        model=row["sidebar_model"] or "",
        prompt=row["sidebar_prompt"] or "",
        sources=tuple(str(x) for x in sources),
    )


def _session(row: dict[str, Any]) -> TranscriptSession:
    return TranscriptSession(
        id=str(row["id"]),
        guild_id=int(row["guild_id"]),
        campaign_id=str(row["campaign_id"]),
        started_at=int(row["started_at"]),
        ended_at=row_int(row, "ended_at"),
        lines=int(row["line_count"]),
        speakers=tuple(sorted(int(u) for u in row["speakers"])),
        number=int(row["number"]),
        engines=tuple(row["engines"]),
        deleted_at=row_int(row, "transcripts_deleted_at"),
        speaker_names={int(u): str(n) for u, n in (row.get("speaker_names") or {}).items()},
    )


async def _forget_mentions(
    conn: Any, guild_id: int, refs: Sequence[str], *, keep_pending: bool = False
) -> None:
    """The story-memory mentions that point at these lines, and their undo-log rows.
    `keep_pending`: leave the pointers of a name still waiting for the DM's check (#2329), so its
    card can say the transcript is gone; they hold a line reference and a place, never words."""
    mentions: dict[str, list[str]] = {}
    for batch in (refs[i : i + 1000] for i in range(0, len(refs), 1000)):
        cur = await conn.execute(
            "DELETE FROM memory_mentions m WHERE m.guild_id = %s AND m.line_ref = ANY(%s)"
            " AND NOT (%s AND EXISTS (SELECT 1 FROM memory_entities e"
            "   WHERE e.guild_id = m.guild_id AND e.campaign_id = m.campaign_id"
            "   AND e.id = m.entity_id AND e.status = 'proposed'))"
            " RETURNING m.campaign_id, m.id",
            (guild_id, list(batch), keep_pending),
        )
        for mention in await cur.fetchall():
            mentions.setdefault(str(mention["campaign_id"]), []).append(str(mention["id"]))
    await _drop_log_rows(conn, guild_id, "memory_mentions", mentions)


async def _forget_memory_traces(
    conn: Any, guild_id: int, user_id: int, refs: Sequence[str]
) -> None:
    """The story-memory rows that exist only because of this person's lines (see
    `delete_person`), in the caller's transaction. Memory relations may still list a deleted
    mention's id; recall skips ids it can't find."""
    await _forget_mentions(conn, guild_id, refs)
    await conn.execute(
        "DELETE FROM memory_heard WHERE guild_id = %s AND speaker_id = %s", (guild_id, user_id)
    )
    # A claim that cites this person goes even if it cites others too: it is only a waiting
    # proposal, and keeping it would keep their words in its details.
    cur = await conn.execute(
        "DELETE FROM memory_claims WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status <> 'confirmed' RETURNING campaign_id, id",
        (guild_id, user_id),
    )
    claims: dict[str, list[str]] = {}
    for claim in await cur.fetchall():
        claims.setdefault(str(claim["campaign_id"]), []).append(str(claim["id"]))
    await _drop_log_rows(conn, guild_id, "memory_claims", claims)


async def _drop_log_rows(
    conn: Any, guild_id: int, table: str, ids_by_campaign: dict[str, list[str]]
) -> None:
    """Take deleted rows out of the memory undo log too, so an Undo can't bring them back and
    the log doesn't keep their words: one statement per campaign."""
    for campaign_id, ids in ids_by_campaign.items():
        await conn.execute(
            "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
            " AND table_name = %s AND row_id = ANY(%s)",
            (guild_id, campaign_id, table, ids),
        )
