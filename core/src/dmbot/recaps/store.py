"""A session's recap job, kept with the campaign (#1382): where the work has got to, the beat sheet
and the draft the DM reviews, what they did with it, and the earlier posted one that is the next
recap's bridge. Every read and write goes through `Database.guild`, so Postgres shows only that
server's rows, and each query names the campaign.

The job is resumable: `state` is the last stage that finished, so a restart carries on from the
next one (the same idea as the extractor's `extracted_through_ms`). A worker takes a job with a
lease (`lease`), so two never work on one, and a stage that fails waits (`next_at`) before it is
tried again.

Also its part of a campaign backup (an `ExportSection`): the text, the housekeeping and the music
prompts, not the decision (a restored campaign's recaps are drafts again, never posted).
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.memory.models import name_key
from dmbot.recaps.allowance import Usage, month_bounds
from dmbot.recaps.approval import CHOICES, TEXT, text_hash
from dmbot.recaps.fixed_lines import STORY_COLUMN_MAX
from dmbot.transcript.respell import respell

DAMAGED = "That backup file's recaps are damaged, so nothing was restored."
MAX_TRIES = 2  # "Try again" at most this many times for one session
MAX_CORRECTIONS = 10  # corrections kept for one recap (#2076)
MAX_STORED = 50  # recaps kept for one campaign in a backup
LEASE_S = 15 * 60  # a worker that stops leaves a job free again after this long
AUDIO_MAX_TRIES = 2  # starts: the first, and one more an hour later
# A recap whose narration is waiting to be made and may start (placeholders: the tries limit, now).
# One text for the worker's pick (`audio_due`), its lease (`audio_lease`) and the campaign move
# (campaigns/moves.py), which must refuse exactly while the worker could start.
AUDIO_DUE_WHERE = (
    "state = 'ready' AND audio = 'queued' AND audio_tries < %s"
    " AND status IN ('draft', 'posting', 'posted') AND audio_next_at <= %s"
)
STUCK = "The narration and music were never finished. The text is fine."
AUDIO_AGAIN_WAIT_S = 600  # "Make it again" at most once in 10 minutes, as the website's door does
_NOW_HASH = "encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
AUDIO_LEASE_S = 20 * 60  # the audio takes longer: several provider calls and the mix
TERMINAL = ("ready", "failed")
STATES = ("queued", "beats", "checked", "prose", "housekeeping", "ready")

COLUMNS = (
    "session_at, dm_user_id, state, attempt, errors, restarts, next_at, calls, window_count,"
    " window_done, spent, last_error,"
    " problems, beats, title, summary, original, housekeeping, music, game_day, status, tries,"
    " speakers, grade, emailed_at, created_at, updated_at, audio, audio_tries, audio_next_at,"
    " audio_credits, audio_error, audio_text_hash, to_confirm, names_state, corrections,"
    " approved_by, approved_at, approved_hash, audio_choice, split, critics,"
    " posting_since, posting_step, posted_url, restarted_at, previous, written_by, uploaded_by,"
    " uploaded_at, earlier, checked_at"
)


_LIMITS = {"title": 120, "summary": STORY_COLUMN_MAX, "original": STORY_COLUMN_MAX}  # the table's


@dataclass(frozen=True, slots=True)
class RecapRespelled:
    """What a fixed spelling did to a campaign's recaps (#2170): counts only."""

    waiting: int = 0  # drafts and approved ones not yet posted
    posted: int = 0
    approvals_kept: int = 0
    audio_old: int = 0  # recaps whose finished audio still says the old spelling
    skipped: int = 0  # recaps the new spelling would make too long: left as they were


_DISCORD_LINK = re.compile(r"^https://discord\.com/channels/[0-9]+/[0-9]+/[0-9]+$")


@dataclass(frozen=True, slots=True)
class Job:
    session_at: int
    dm_user_id: int
    state: str
    attempt: int
    errors: int
    restarts: int
    next_at: int
    calls: int
    window_count: int  # parts the transcript is read in, and how many are done
    window_done: int
    spent: int  # calls ever made for this session, never reset (their dates are in `spent_log`)
    last_error: str
    problems: tuple[str, ...]
    beats: Any  # the stored beat sheet (dmbot.recaps.beats.from_json reads it)
    title: str
    summary: str
    original: str
    housekeeping: tuple[str, ...]
    music: tuple[str, ...]
    game_day: int | None
    status: str  # draft, posting, posted or dropped
    tries: int
    speakers: tuple[int, ...]
    grade: dict[str, int]
    emailed_at: int | None
    created_at: int
    updated_at: int
    audio: str = "off"  # off, queued, ready or failed (#1383): the text never waits for it
    audio_tries: int = 0
    audio_next_at: int = 0
    audio_credits: int = 0
    audio_error: str = ""
    audio_text_hash: str = ""
    to_confirm: tuple[str, ...] = ()  # names the recap kept that the DM is asked to confirm (#2027)
    names_state: int = (
        0  # 0 none, 1 the DM was told of the names to confirm, 2 and reminded (#2027)
    )
    corrections: tuple[str, ...] = ()  # the DM's corrections for this recap's next try (#2076)

    approved_by: int = 0  # the DM who approved the text (#2083); 0: nobody yet
    approved_at: int = 0
    approved_hash: str = ""  # the fingerprint of the text they approved
    audio_choice: str = ""  # voice, music, both or text, once approved
    split: Any = None  # the opening's parts (dmbot.recaps.split.from_json reads it); None: not yet
    # Where the critics loop is (dmbot.recaps.critics.loop_from_json reads it); None: not begun.
    critics: Any = None
    # The progress clock (#2435): since when the post or the narration has been under way (Unix
    # seconds), at which step (check, post or audio; "" when none), and the posted message's link.
    posting_since: int | None = None
    posting_step: str = ""
    posted_url: str = ""
    # The DM started this recap over (Unix seconds; 0: never, #2608), and the approved or posted
    # recap that was there before, kept until the new one is approved.
    restarted_at: int = 0
    previous: Any = None
    # A recap written by someone at the table (#2609): who wrote it (empty: DMbot did), the DM who
    # added it and when, the recaps it replaced (newest first), and when its names were checked.
    written_by: str = ""
    uploaded_by: int = 0
    uploaded_at: int = 0
    earlier: Any = None
    checked_at: int = 0

    @property
    def keeps_previous(self) -> bool:
        """An earlier approved or posted recap is still held for the DM: the new one is not yet
        approved or posted, so the old one may still be kept (#2608)."""
        return self.previous is not None and not self.approved_hash and self.status == "draft"

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL

    @property
    def approved(self) -> bool:
        """Whether the DM has approved the text as it is now: an edit since ends it."""
        return bool(self.approved_hash) and self.approved_hash == text_hash(
            self.title, self.summary
        )


def _job(r: dict[str, Any]) -> Job:
    return Job(
        int(r["session_at"]),
        int(r["dm_user_id"]),
        r["state"],
        int(r["attempt"]),
        int(r["errors"]),
        int(r["restarts"]),
        int(r["next_at"]),
        int(r["calls"]),
        int(r["window_count"]),
        int(r["window_done"]),
        int(r["spent"]),
        r["last_error"],
        tuple(r["problems"]),
        r["beats"],
        r["title"],
        r["summary"],
        r["original"],
        tuple(r["housekeeping"]),
        tuple(r["music"]),
        None if r["game_day"] is None else int(r["game_day"]),
        r["status"],
        int(r["tries"]),
        tuple(int(s) for s in r["speakers"]),
        dict(r["grade"] or {}),
        None if r["emailed_at"] is None else int(r["emailed_at"]),
        int(r["created_at"]),
        int(r["updated_at"]),
        r["audio"],
        int(r["audio_tries"]),
        int(r["audio_next_at"]),
        int(r["audio_credits"]),
        r["audio_error"],
        r["audio_text_hash"],
        tuple(r["to_confirm"]),
        int(r["names_state"]),
        tuple(r["corrections"]),
        int(r["approved_by"]),
        int(r["approved_at"]),
        r["approved_hash"],
        r["audio_choice"],
        r["split"],
        r["critics"],
        None if r.get("posting_since") is None else int(r["posting_since"]),
        r.get("posting_step") or "",
        r.get("posted_url") or "",
        int(r.get("restarted_at") or 0),
        r.get("previous"),
        r.get("written_by") or "",
        int(r.get("uploaded_by") or 0),
        int(r.get("uploaded_at") or 0),
        r.get("earlier") or [],
        int(r.get("checked_at") or 0),
    )


# The progress clock stops when the narration stops, whatever happened to it: the right-hand sides
# see the row as it was, so the step is read before it is cleared (#2435). A post's own step stays.
_AUDIO_CLOCK_OFF = (
    "posting_since = CASE WHEN posting_step = 'audio'"
    " THEN NULLIF(posting_since, posting_since) ELSE posting_since END,"
    " posting_step = CASE WHEN posting_step = 'audio'"
    " THEN NULLIF(posting_step, posting_step) ELSE posting_step END"
)

LOG_KEPT_S = 2 * 86400  # how long a call's date is kept (the daily tally needs only the last day)

# The columns a stage may write: a fixed list, so a value never decides a column name.
_WRITABLE = frozenset(
    {
        "state",
        "attempt",
        "errors",
        "restarts",
        "next_at",
        "calls",
        "spent",
        "window_count",
        "window_done",
        "last_error",
        "problems",
        "beats",
        "split",
        "critics",
        "title",
        "summary",
        "original",
        "housekeeping",
        "music",
        "game_day",
        "speakers",
        "grade",
        "emailed_at",
        "to_confirm",
        "names_state",
    }
)
_LISTS = frozenset({"problems", "housekeeping", "music", "speakers", "to_confirm"})


# An issue PlotBot found for the exact text `{hash}` that the DM has not answered yet (#2122).
_PLOT_OPEN = (
    "EXISTS (SELECT 1 FROM plot_checks k JOIN plot_issues i ON i.guild_id = k.guild_id"
    " AND i.campaign_id = k.campaign_id AND i.session_at = k.session_at"
    " AND i.text_hash = k.text_hash WHERE k.guild_id = recap_jobs.guild_id"
    " AND k.campaign_id = recap_jobs.campaign_id AND k.session_at = recap_jobs.session_at"
    " AND k.text_hash = {hash} AND k.state = 'done' AND COALESCE(i.answer_kind, '') = '')"
)


@dataclass(frozen=True, slots=True)
class SavedLists:
    """What making a recap again would throw away (#2727): how many scenes have a "who was there"
    list the DM saved (None: it could not be read, so the question warns anyway) and whether a
    finished recap stands (which the DM is asked about anyway)."""

    count: int | None
    finished: bool


class RecapStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    @property
    def db(self) -> Database:
        return self._db

    async def get(self, guild_id: int, campaign_id: str, session_at: int) -> Job | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM recap_jobs"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
        return None if row is None else _job(row)

    async def enqueue(
        self, guild_id: int, campaign_id: str, session_at: int, *, dm_user_id: int
    ) -> bool:
        """Queue the recap of a session. True if this call queued it; False if there already is
        a job for it (a restart, or the session ended twice): one job for each session."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id,"
                " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
                (guild_id, campaign_id, session_at, dm_user_id, now, now),
            )
            return bool(cur.rowcount)

    async def add_finished(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        *,
        dm_user_id: int,
        title: str,
        summary: str,
        original: str,
        housekeeping: Sequence[str],
        music: Sequence[str],
        game_day: int | None,
        speakers: Sequence[int],
        grade: dict[str, int],
        beats: Any,
    ) -> bool:
        """A finished recap as a draft in ONE statement (the test run's `--keep`, #1424): it is
        `ready` and already marked told from the start, so the job worker never sees it as work
        to do and no email goes out. False if that session already has a recap."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state,"
                " title, summary, original, housekeeping, music, game_day, speakers, grade,"
                " beats, emailed_at, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
                (
                    guild_id, campaign_id, session_at, dm_user_id, title, summary, original,
                    list(housekeeping), list(music), game_day, list(speakers), Jsonb(grade),
                    Jsonb(beats), now, now, now,
                ),
            )  # fmt: skip
            return bool(cur.rowcount)

    async def due(self, guild_id: int, *, limit: int = 20) -> list[tuple[str, int]]:
        """(campaign, session) of the unfinished jobs in a server that may run now: not leased,
        past their wait, and not already decided by the DM. At most one for each campaign, the
        oldest, so a campaign's recaps never run side by side."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (campaign_id) campaign_id, session_at FROM recap_jobs"
                " WHERE guild_id = %s AND state NOT IN ('ready', 'failed') AND status = 'draft'"
                " AND next_at <= %s AND leased_to <= %s"
                " AND NOT EXISTS (SELECT 1 FROM recap_jobs r2"
                "   WHERE r2.guild_id = recap_jobs.guild_id"
                "   AND r2.campaign_id = recap_jobs.campaign_id AND r2.leased_to > %s)"
                " ORDER BY campaign_id, session_at LIMIT %s",
                (guild_id, now, now, now, limit),
            )
            return [(r["campaign_id"], int(r["session_at"])) for r in await cur.fetchall()]

    async def lease(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Take the job for a while. False if another worker has it, or it is done or waiting."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET leased_to = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND leased_to <= %s AND next_at <= %s AND status = 'draft'"
                " AND state NOT IN ('ready', 'failed')"
                " AND NOT EXISTS (SELECT 1 FROM recap_jobs r2 WHERE r2.guild_id = %s"
                "   AND r2.campaign_id = %s AND r2.leased_to > %s)",
                (
                    now + LEASE_S,
                    guild_id,
                    campaign_id,
                    session_at,
                    now,
                    now,
                    guild_id,
                    campaign_id,
                    now,
                ),
            )
            return bool(cur.rowcount)

    async def release(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                # The lease is over, so a narration clock it ran is over (#2435), whatever state or
                # status the recap is in by now (a dropped one is never reached by `set_audio`).
                f"UPDATE recap_jobs SET leased_to = 0, {_AUDIO_CLOCK_OFF} WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )

    async def update(self, guild_id: int, campaign_id: str, session_at: int, **fields: Any) -> None:
        """Write what a stage produced. Only the listed columns (`_WRITABLE`)."""
        await self._write(guild_id, campaign_id, session_at, None, fields)

    async def update_seen(
        self, guild_id: int, campaign_id: str, session_at: int, approval: int, **fields: Any
    ) -> None:
        """`update`, but only if the DM's approval of an unchecked draft (#2305) is what the step
        saw when it started (`approval`: its time, 0 for none): a step that began before the DM
        approved must not put its wait or its failure over the approval."""
        await self._write(guild_id, campaign_id, session_at, approval, fields)

    async def _write(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        approval: int | None,
        fields: dict[str, Any],
    ) -> None:
        if not fields or not set(fields) <= _WRITABLE:
            raise ValueError(sorted(set(fields) - _WRITABLE))
        now = int(self._now())
        parts: list[str] = []
        # A save of the critics' loop is made from what the worker read some time ago. A DM who
        # approved the unchecked draft meanwhile (#2305) must not lose that to it, nor have the
        # revised words they did not read put over theirs: once approved, a save of the loop (not
        # a clearing, which Try again does) changes nothing.
        guard = (
            " AND COALESCE((critics->>'approved_at')::bigint, 0) = 0"
            if fields.get("critics") is not None
            else ""
        )
        if approval is not None:
            guard += " AND COALESCE((critics->>'approved_at')::bigint, 0) = %s"
        values: list[Any] = []
        for name, value in fields.items():
            if name == "spent":
                # Each call is dated, so the day's tally counts the last 24 hours of calls and a
                # waiting recap's lifetime spend does not stay in "today" (#2308). The new total
                # minus the old is the calls just made; stamps older than two days are dropped.
                parts.append(
                    "spent = %s, spent_log = ARRAY(SELECT t FROM unnest("
                    # a job from before the calls were dated: its earlier calls are dated by the
                    # last time it was updated, so gaining one stamp does not lose them
                    "CASE WHEN cardinality(spent_log) = 0 AND spent > 0"
                    " THEN array_fill(updated_at, ARRAY[spent]) ELSE spent_log END) AS t"
                    " WHERE t >= %s) || array_fill(%s::bigint, ARRAY[GREATEST(%s - spent, 0)])"
                )
                values += [value, now - LOG_KEPT_S, now, value]
                continue
            parts.append(f"{name} = %s")
            if name in _LISTS:
                values.append(list(value))
            elif name in ("beats", "grade"):
                values.append(Jsonb(value))
            elif name in ("split", "critics"):  # None is SQL NULL ("not yet"), not a JSON null
                values.append(None if value is None else Jsonb(value))
            else:
                values.append(value)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                f"UPDATE recap_jobs SET {', '.join(parts)}, updated_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'draft'" + guard,
                (
                    *values,
                    now,
                    guild_id,
                    campaign_id,
                    session_at,
                    *([] if approval is None else [approval]),
                ),
            )

    # --- the audio tracks (#1383) -------------------------------------------------------

    async def audio_due(self, guild_id: int, *, limit: int = 10) -> list[tuple[str, int]]:
        """(campaign, session) of recaps whose text is ready and whose audio waits to be made
        and may run now: not leased, past their wait, not dropped. A posted recap is included: its
        narration can be made again after a spelling fix changed its words (#2183). One for each
        campaign at a time, so a campaign's audio never runs side by side."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (campaign_id) campaign_id, session_at FROM recap_jobs"
                f" WHERE guild_id = %s AND {AUDIO_DUE_WHERE} AND leased_to <= %s"
                " ORDER BY campaign_id, session_at LIMIT %s",
                (guild_id, AUDIO_MAX_TRIES, now, now, limit),
            )
            return [(r["campaign_id"], int(r["session_at"])) for r in await cur.fetchall()]

    async def audio_lease(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Take the recap for its audio for a while (the lease the text worker uses)."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            # The try is counted here, before any paid call: a process that is killed mid-work
            # (a deploy, a crash) still used one, so a crash loop can't bill without end.
            cur = await conn.execute(
                "UPDATE recap_jobs SET leased_to = %s, audio_tries = audio_tries + 1,"
                # The clock runs while the narration is made, unless a post is under way (#2435).
                " posting_step = COALESCE(posting_step, 'audio'),"
                # (the database's own clock, as the trigger uses, so the two never differ)
                " posting_since = COALESCE(posting_since,"
                " floor(extract(epoch FROM clock_timestamp()))::BIGINT)"
                " WHERE guild_id = %s AND campaign_id = %s"
                f" AND session_at = %s AND leased_to <= %s AND {AUDIO_DUE_WHERE}"
                " AND NOT EXISTS (SELECT 1 FROM recap_jobs r2 WHERE r2.guild_id = %s"
                "   AND r2.campaign_id = %s AND r2.leased_to > %s)",
                (
                    now + AUDIO_LEASE_S,
                    guild_id,
                    campaign_id,
                    session_at,
                    now,
                    AUDIO_MAX_TRIES,
                    now,
                    guild_id,
                    campaign_id,
                    now,
                ),
            )
            return bool(cur.rowcount)

    async def fail_stuck_audio(self, guild_id: int) -> int:
        """Audio whose tries ran out without an answer (the process died each time): failed,
        so it stops waiting. Returns how many."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET audio = 'failed', audio_error = %s,"
                f" {_AUDIO_CLOCK_OFF} WHERE guild_id = %s"
                " AND state = 'ready' AND audio = 'queued' AND audio_tries >= %s"
                " AND leased_to <= %s",
                (STUCK, guild_id, AUDIO_MAX_TRIES, int(self._now())),
            )
            return int(cur.rowcount)

    async def set_audio(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        *,
        audio: str,
        tries: int | None = None,
        next_at: int | None = None,
        credits: int | None = None,
        error: str = "",
        text_hash: str | None = None,
        unchanged: tuple[str, str] | None = None,
        how: str | None = None,
        why: str = "",
    ) -> bool:
        """Record where the audio has got to. Only while the recap is draft, posting or posted (a
        posted one is narrated again for the page when its text changed, #2183), and, with
        `unchanged` (title, summary), only if the text is still what the audio was made from.
        False if nothing was written."""
        # (the live progress ends with the making, whatever came of it, #2630)
        sets = [
            "audio = %s", "audio_error = %s", "audio_stage = ''", "audio_done = 0",
            "audio_total = 0", _AUDIO_CLOCK_OFF,
        ]  # fmt: skip
        values: list[Any] = [audio, error[:200]]
        if how is not None:
            sets += ["audio_how = %s", "audio_why = %s"]
            values += [how, why]
        for column, value in (
            ("audio_tries", tries),
            ("audio_next_at", next_at),
            ("audio_credits", credits),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                values.append(value)
        if text_hash is not None:
            sets.append("audio_text_hash = %s")
            values.append(text_hash)
        where = (
            " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s AND state = 'ready'"
            " AND status IN ('draft', 'posting', 'posted')"
        )
        args: list[Any] = [guild_id, campaign_id, session_at]
        if unchanged is not None:
            where += " AND title = %s AND summary = %s"
            args += [unchanged[0], unchanged[1]]
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"UPDATE recap_jobs SET {', '.join(sets)}, updated_at = %s" + where,
                (*values, int(self._now()), *args),
            )
            return bool(cur.rowcount)

    async def set_audio_progress(
        self, guild_id: int, campaign_id: str, session_at: int, *, stage: str, done: int, total: int
    ) -> None:
        """Where the audio under way has got to (#2630): a stage code and counts. Only while it is
        being made (`queued`); it also says when anything last moved."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET audio_stage = %s, audio_done = %s, audio_total = %s,"
                " audio_moved_at = %s WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND audio = 'queued'",
                (stage, done, total, int(self._now()), guild_id, campaign_id, session_at),
            )

    async def audio_to_tell(self, guild_id: int, *, limit: int = 10) -> list[tuple[str, int, str]]:
        """(campaign, session, 'ready' or 'failed') of recaps whose audio has finished and whose
        end the DM has not been told (#2621). Not while the text is dropped; a posted recap is told
        too (its page carries the audio)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_at, audio FROM recap_jobs"
                " WHERE guild_id = %s AND state = 'ready' AND audio IN ('ready', 'failed')"
                " AND audio_told <> audio AND status IN ('draft', 'posting', 'posted')"
                " ORDER BY updated_at LIMIT %s",
                (guild_id, limit),
            )
            rows = await cur.fetchall()
        return [(r["campaign_id"], int(r["session_at"]), r["audio"]) for r in rows]

    async def audio_again(self, guild_id: int, campaign_id: str, session_at: int) -> str:
        """Make the audio again from the Discord button (#2621), under the website door's rules
        (the website's own door): an approved text that makes audio, whose audio is ready or
        failed, once every 10 minutes. 'queued', 'too_soon' or 'not_allowed'. The plan and the
        allowance are checked by the audio step before any paid call."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT audio, audio_choice, audio_asked_at, status,"
                f" approved_hash = {_NOW_HASH} AS current FROM recap_jobs"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s AND state = 'ready'"
                " FOR UPDATE",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            if (
                row is None
                or row["status"] not in ("draft", "posting")
                or row["audio"] not in ("ready", "failed")
                or row["audio_choice"] not in ("voice", "music", "both")
                or not row["current"]
            ):
                return "not_allowed"
            if int(row["audio_asked_at"]) > now - AUDIO_AGAIN_WAIT_S:
                return "too_soon"
            await conn.execute(
                "UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,"
                " audio_error = '', audio_credits = 0, audio_asked_at = %s, updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s",
                (now, now, guild_id, campaign_id, session_at),
            )
        return "queued"

    async def mark_audio_told(
        self, guild_id: int, campaign_id: str, session_at: int, told: str
    ) -> bool:
        """The DM was told the audio is `told` ('ready' or 'failed'). Only while the audio still
        is that, so a new making that began meanwhile is not marked: False then."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET audio_told = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND audio = %s",
                (told, guild_id, campaign_id, session_at, told),
            )
            return bool(cur.rowcount)

    async def approve(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        user_id: int,
        choice: str,
        *,
        expected: str,
        post: bool = False,
        plot_gate: bool = False,
    ) -> bool:
        """The DM approved the text as it is now and picked what to make (#2083). Records who and
        when, and the hash of the text, and for any choice but text queues the audio. `expected`:
        the hash (`approval.text_hash`) of the text whose cues, names and consent were just
        checked; if the text has changed since, nothing is approved. False: not allowed now (not
        ready, already decided, a different text, or its audio is under way). Audio made from
        older text may be made again. The caller has checked the DM.
        `post` (#2420, "Approve and post text only"): hand the recap to the bot to post in the same
        statement, as the website does, so a refusal leaves nothing approved and half-posted. Only
        for a text-only choice: audio is made and reviewed before anything is posted.
        `plot_gate` (#2122): PlotBot's check is on, so nothing is approved while an issue it found
        for this very text has no answer (the website's rule, in the same statement)."""
        if choice not in CHOICES or (post and choice != TEXT):
            raise ValueError(choice)
        queue = choice != TEXT
        now_hash = "encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
        sets = f"approved_by = %s, approved_at = %s, approved_hash = {now_hash}, audio_choice = %s"
        if queue:
            sets += ", audio = 'queued', audio_tries = 0, audio_next_at = 0, audio_error = ''"
        if post:
            sets += ", status = 'posting'"
        # Nothing is approved over audio that is queued or being made, text only included: the
        # spend so far would be lost and the caps dodged.
        where = (
            f" WHERE guild_id = %s AND campaign_id = %s AND session_at = %s AND state = 'ready'"
            f" AND status = 'draft' AND audio <> 'queued' AND {now_hash} = %s"
        )
        if plot_gate:
            where += f" AND NOT ({_PLOT_OPEN.format(hash=now_hash)})"
        if queue:
            where += (
                " AND (audio IN ('off', 'failed')"
                f" OR (audio = 'ready' AND audio_text_hash <> {now_hash}))"
            )
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"UPDATE recap_jobs SET {sets}" + where,
                (user_id, int(self._now()), choice, guild_id, campaign_id, session_at, expected),
            )
            if not cur.rowcount:
                return False
            # The approved text becomes the session's next accepted version (#2122), the same
            # statement-for-statement as the website's door, in this transaction.
            await conn.execute(
                "SELECT dmbot_recap_keep_version(%s, %s, %s, %s, %s)",
                (guild_id, campaign_id, session_at, user_id, int(self._now())),
            )
            return True

    async def plot_open(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Does PlotBot have an issue for this recap's text that the DM has not answered (#2122)?"""
        now_hash = "encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT 1 FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                f" AND session_at = %s AND {_PLOT_OPEN.format(hash=now_hash)}",
                (guild_id, campaign_id, session_at),
            )
            return await cur.fetchone() is not None

    async def respell(
        self,
        guild_id: int,
        campaign_id: str,
        old: str,
        new: str,
        longer: Sequence[str],
        hidden: Sequence[str] = (),
    ) -> RecapRespelled:
        """A fixed spelling reaches this campaign's recaps (#2170): the written ones that are not
        dropped get `new` where they say `old` as a whole name
        (never inside a longer name in `longer`, and never completing a name in `hidden`), in title,
        text and the kept original. A waiting
        recap the DM already approved stays approved: a spelling fix is not a change of story, so
        its approval follows the new text. Finished audio is left as it is; it no longer matches
        the text, which is how the page already knows to offer making it again. A posted recap's
        Discord message is not touched; its stored text is, and `respelled_at` lets the page say
        so. One transaction. Counts only."""
        waiting = posted = kept = audio = skipped = 0
        # The longest run of letters and digits: the name may be written with a look-alike
        # apostrophe or dash, which a prefilter on a whole word would miss.
        word = max(re.findall(r"\w+", old), key=len, default="")
        like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, status, title, summary, original, to_confirm, approved_hash,"
                " audio, audio_text_hash FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND state = 'ready' AND status IN ('draft', 'posted')"
                " AND (title ILIKE %s ESCAPE '\\' OR summary ILIKE %s ESCAPE '\\'"
                " OR original ILIKE %s ESCAPE '\\') FOR UPDATE",
                (guild_id, campaign_id, like, like, like),
            )
            for row in await cur.fetchall():
                title, summary, original = row["title"], row["summary"], row["original"]
                changed: dict[str, str] = {}
                blocked = False
                for name, text in (("title", title), ("summary", summary), ("original", original)):
                    found = respell(text, old, new, longer, hidden)
                    if found is not None:
                        changed[name] = found.text
                    elif hidden and respell(text, old, new, longer) is not None:
                        blocked = True  # the new spelling would complete a hidden name here
                if blocked:
                    skipped += 1  # the recap is left whole, never half respelled
                    continue
                # The names the recap asks the DM to check: the same name, spelled right.
                confirm = list(
                    dict.fromkeys(
                        new if name_key(c) == name_key(old) else c for c in row["to_confirm"]
                    )
                )
                confirm_changed = confirm != list(row["to_confirm"])
                if not changed and not confirm_changed:
                    continue
                if any(len(text) > _LIMITS[name] for name, text in changed.items()):
                    skipped += 1  # the new spelling is longer and would not fit: left as it was
                    continue
                if not ({"title", "summary"} & changed.keys()):
                    # Only the kept original or the names to check had it: written, but nothing
                    # the DM reads in the text changed.
                    quiet = [f"{name} = %s" for name in changed]
                    quiet_values: list[Any] = list(changed.values())
                    if confirm_changed:
                        quiet.append("to_confirm = %s")
                        quiet_values.append(confirm)
                    await conn.execute(
                        f"UPDATE recap_jobs SET {', '.join(quiet)} WHERE guild_id = %s"
                        " AND campaign_id = %s AND session_at = %s",
                        (*quiet_values, guild_id, campaign_id, row["session_at"]),
                    )
                    continue
                before = text_hash(title, summary)
                after = text_hash(changed.get("title", title), changed.get("summary", summary))
                sets = [f"{name} = %s" for name in changed]
                values: list[Any] = list(changed.values())
                if row["approved_hash"] and row["approved_hash"] == before:
                    sets.append("approved_hash = %s")
                    values.append(after)
                    kept += 1
                if confirm_changed:
                    sets.append("to_confirm = %s")
                    values.append(confirm)
                sets.append("respelled_at = %s")
                values.append(int(self._now()))
                await conn.execute(
                    f"UPDATE recap_jobs SET {', '.join(sets)} WHERE guild_id = %s"
                    " AND campaign_id = %s AND session_at = %s",
                    (*values, guild_id, campaign_id, row["session_at"]),
                )
                if row["status"] == "posted":
                    posted += 1
                else:
                    waiting += 1
                if row["audio"] == "ready" and row["audio_text_hash"] == before:
                    audio += 1
        return RecapRespelled(waiting, posted, kept, audio, skipped)

    async def reset_audio(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        """A new recap is being written (Try again): the old audio is no longer its audio."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET audio = 'off', audio_tries = 0, audio_next_at = 0,"
                f" audio_credits = 0, audio_error = '', audio_text_hash = '', {_AUDIO_CLOCK_OFF}"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )

    async def dictionary(self, guild_id: int, campaign_id: str) -> tuple[str, str, str] | None:
        """The campaign's pronunciation dictionary: (id, version, hash of its rules)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT dictionary_id, version_id, rules_hash FROM recap_dictionaries"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return None if row is None else (row["dictionary_id"], row["version_id"], row["rules_hash"])

    async def save_dictionary(
        self, guild_id: int, campaign_id: str, dictionary_id: str, version_id: str, rules_hash: str
    ) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO recap_dictionaries (guild_id, campaign_id, dictionary_id, version_id,"
                " rules_hash, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET"
                " dictionary_id = EXCLUDED.dictionary_id, version_id = EXCLUDED.version_id,"
                " rules_hash = EXCLUDED.rules_hash, updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, dictionary_id, version_id, rules_hash, int(self._now())),
            )

    async def bridge(self, guild_id: int, campaign_id: str, before: int) -> Job | None:
        """The latest earlier recap of this campaign that was posted: the bridge into this one (a
        draft the DM never approved is not a bridge). Never another campaign's: the query names
        the campaign."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM recap_jobs"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at < %s"
                " AND status = 'posted' ORDER BY session_at DESC LIMIT 1",
                (guild_id, campaign_id, before),
            )
            row = await cur.fetchone()
        return None if row is None else _job(row)

    async def earlier_job(self, guild_id: int, campaign_id: str, before: int) -> Job | None:
        """The latest earlier recap job of this campaign with a finished, checked beat sheet,
        whether or not it was posted: what the next session is compared with to find a retelling
        (#2253). Never another campaign's: the query names the campaign."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM recap_jobs"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at < %s"
                " AND status <> 'dropped'"
                " AND state IN ('checked', 'prose', 'housekeeping', 'ready')"
                " ORDER BY session_at DESC LIMIT 1",
                (guild_id, campaign_id, before),
            )
            row = await cur.fetchone()
        return None if row is None else _job(row)

    async def calls_since(self, guild_id: int, campaign_id: str, since: int) -> int:
        """AI calls the campaign's recaps made since then, for the daily cap: the calls dated
        since then, not a waiting job's whole lifetime (#2308). A job from before the calls were
        dated is counted by its total while it was updated since then."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT COALESCE(sum(CASE WHEN cardinality(spent_log) = 0 THEN spent"
                " ELSE (SELECT count(*) FROM unnest(spent_log) AS t WHERE t >= %s) END), 0) AS n"
                " FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s AND updated_at >= %s",
                (since, guild_id, campaign_id, since),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def used_in_month(self, guild_id: int, campaign_id: str, now: float) -> Usage:
        """The campaign's recaps and narrated recaps this calendar month, UTC (#1428). A written
        recap counts once and each Try again counts again; a job that never got written does not.
        Read from the rows themselves; raises if it can't be read, and the caller then spends
        nothing."""
        start, end = month_bounds(now)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                # (a recap written by the table is not one of DMbot's recaps: it costs nothing and
                # is not counted; its narration still is)
                "SELECT COALESCE(sum(CASE WHEN uploaded_by > 0 THEN 0 ELSE"
                " (CASE WHEN state = 'ready' THEN 1 ELSE 0 END) + tries END), 0)"
                " AS recaps, COALESCE(sum((CASE WHEN audio = 'ready' THEN 1 ELSE 0 END)"
                " + (CASE WHEN audio <> 'off' THEN tries ELSE 0 END)), 0)"
                " AS narrated FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                # (a recap the DM started over counts in the month they did)
                " AND COALESCE(NULLIF(restarted_at, 0), created_at) >= %s"
                " AND COALESCE(NULLIF(restarted_at, 0), created_at) < %s",
                (guild_id, campaign_id, start, end),
            )
            row = await cur.fetchone()
        assert row is not None
        return Usage(int(row["recaps"]), int(row["narrated"]))

    async def requeue(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Start one session's recap job over (#2341): back to the queue with its tries, errors
        and half-done work cleared, so a failure on our side (fixed since) costs the DM nothing.
        Only a failed recap that is still a draft (a good one is never wiped). Used by the
        `recap_requeue` devtool, which dev1 runs on the server; the worker picks it up next
        round. False: no such recap."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET state = 'queued', attempt = 0, errors = 0, restarts = 0,"
                " next_at = 0, calls = 0, spent = 0, window_count = 0,"
                " window_done = 0,"
                " last_error = '', problems = '{}', beats = '[]', split = NULL, critics = NULL,"
                " tries = 0, approved_by = 0, approved_at = 0, approved_hash = '',"
                " audio_choice = ''"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND status = 'draft' AND state = 'failed'",
                (guild_id, campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def start_over(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        who: int,
        *,
        replace_ok: bool = False,
    ) -> str:
        """A DM makes the recap of a session, or makes it again (#2608). One of: `queued` (the
        worker writes it next round), `needs_confirm` (it has a recap already; ask once, then call
        again with `replace_ok`), `busy` (it is being written or sent now), `no_lines` (the
        session's words are gone, so it cannot be written), `not_allowed` (`who` is no DM of the
        campaign). An approved or posted recap is kept, in `previous`, until the new one is
        approved. The plan, the switches and the month's allowance are the worker's, as for every
        recap, and fail closed there."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT dmbot_recap_start_over(%s, %s, %s, %s, %s) AS r",
                (guild_id, campaign_id, session_at, who, replace_ok),
            )
            row = await cur.fetchone()
        return str(row["r"]) if row else "not_allowed"

    async def upload(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        who: int,
        *,
        title: str,
        text: str,
        written_by: str,
        replace_ok: bool = False,
        rights: bool = False,
    ) -> str:
        """Keep a recap written by someone at the table as the session's recap (#2609), approved by
        the DM who adds it, never through the AI writer. One of: `stored`, `needs_rights` (the
        campaign's first upload: the DM confirms the right to use it once, then send `rights`),
        `needs_confirm` (the session has a recap: ask once, then send `replace_ok`; the old one is
        kept in `earlier`), `busy` (being written or sent), `bad_text`, `not_allowed` (`who` is no
        DM). The session need not have a transcript."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT dmbot_recap_add_uploaded(%s, %s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (
                    guild_id, campaign_id, session_at, who, title, text, written_by,
                    replace_ok, rights,
                ),
            )  # fmt: skip
            row = await cur.fetchone()
        return str(row["r"]) if row else "not_allowed"

    async def unchecked_uploads(self, guild_id: int, limit: int = 5) -> list[tuple[str, int]]:
        """(campaign, session) of uploaded recaps whose names were not checked yet, oldest first:
        the website stores one without the memory it needs to do that (#2609)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_at FROM recap_jobs WHERE guild_id = %s"
                " AND written_by <> '' AND checked_at = 0 AND state = 'ready' ORDER BY uploaded_at"
                " LIMIT %s",
                (guild_id, limit),
            )
            return [(r["campaign_id"], int(r["session_at"])) for r in await cur.fetchall()]

    async def set_upload_check(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        *,
        to_confirm: Sequence[str],
        note: str,
    ) -> bool:
        """What the check of an uploaded recap found (#2609): the names to ask the DM about, and the
        note for the recap page when it names something kept secret (advice, never a block). Once:
        False when it was checked already, or the recap changed meanwhile."""
        from dmbot.recaps import critics as cr  # (the loop's own JSON, for the page's notes)

        notes = (note,) if note else ()
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET to_confirm = %s, checked_at = %s, critics = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND written_by <> '' AND checked_at = 0",
                (
                    list(to_confirm), int(self._now()),
                    Jsonb(cr.Loop(done=True, notes=notes).to_json()) if notes else None,
                    guild_id, campaign_id, session_at,
                ),
            )  # fmt: skip
            return bool(cur.rowcount)

    async def rights(self, guild_id: int, campaign_id: str) -> tuple[int, int] | None:
        """(who, when) the campaign's right-to-use confirmation for uploaded recaps was given, or
        None: the next upload asks (#2609)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT user_id, confirmed_at FROM recap_upload_rights"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return None if row is None else (int(row["user_id"]), int(row["confirmed_at"]))

    async def overview(self, guild_id: int, campaign_id: str) -> dict[int, tuple[str, str, bool]]:
        """For each session of the campaign that has a recap job: (state, status, approved), to say
        in a session picker what each one has (#2608). Words only, never the recap's text."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, state, status, approved_hash <> '' AS approved"
                " FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {
                int(r["session_at"]): (r["state"], r["status"], bool(r["approved"]))
                for r in await cur.fetchall()
            }

    async def saved_lists(
        self, guild_id: int, campaign_id: str, session_at: int
    ) -> SavedLists | None:
        """For the question before a recap is made again (#2727): how many scenes of the session's
        recap have a "who was there" list the DM saved (#2684), and whether a finished recap
        stands (one the DM is asked about anyway). None when the session has no recap job. A count
        only: no name and no word of the recap leaves the database."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM jsonb_array_elements("
                "          CASE WHEN jsonb_typeof(beats) = 'array' THEN beats ELSE '[]'::jsonb END"
                "        ) AS b WHERE jsonb_typeof(b) = 'object'"
                "          AND (b -> 'saved') = 'true'::jsonb) AS lists,"
                " state = 'ready' AND status IN ('draft', 'posted') AS finished"
                " FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
        return None if row is None else SavedLists(int(row["lists"]), bool(row["finished"]))

    async def others_working(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Whether another recap of the campaign is being written, so this one waits its turn: the
        worker runs one at a time for a campaign (#2608)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at <> %s AND state NOT IN ('ready', 'failed') AND status = 'draft'"
                " LIMIT 1",
                (guild_id, campaign_id, session_at),
            )
            return await cur.fetchone() is not None

    async def keep_previous(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Put the earlier approved or posted recap back, instead of the new one (#2608): for a new
        one that failed, or that the DM does not want. Only while the new one is not approved.
        False:
        there is nothing to put back."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET state = 'ready', last_error = '', tries = 0,"
                " title = previous->>'title', summary = previous->>'summary',"
                " original = previous->>'original',"
                " housekeeping = ARRAY(SELECT jsonb_array_elements_text(previous->'housekeeping')),"
                " music = ARRAY(SELECT jsonb_array_elements_text(previous->'music')),"
                " game_day = (previous->>'game_day')::INTEGER, status = previous->>'status',"
                " approved_by = (previous->>'approved_by')::BIGINT,"
                " approved_at = (previous->>'approved_at')::BIGINT,"
                " approved_hash = previous->>'approved_hash',"
                " audio_choice = previous->>'audio_choice',"
                " speakers = ARRAY(SELECT jsonb_array_elements_text(previous->'speakers')::BIGINT),"
                " grade = previous->'grade', posted_url = previous->>'posted_url',"
                " decided_at = (previous->>'decided_at')::BIGINT, previous = NULL,"
                " emailed_at = COALESCE(emailed_at, updated_at), critics = NULL, split = NULL"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND previous IS NOT NULL AND approved_hash = '' AND status = 'draft'"
                " AND state IN ('ready', 'failed')",
                (guild_id, campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def give_back_try(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        """Undo the count of a Try again that the allowance refused (#1428): nothing was written."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET tries = GREATEST(tries - 1, 0) WHERE guild_id = %s"
                # (not once more for a job already failed: a restart between the give-back and the
                # failing must not give a second try back)
                " AND campaign_id = %s AND session_at = %s AND state <> 'failed'",
                (guild_id, campaign_id, session_at),
            )

    async def waiting_drafts(
        self, guild_id: int, campaign_id: str, limit: int = 2
    ) -> list[tuple[int, str]]:
        """The campaign's finished recaps the DM has neither posted nor skipped, newest first, as
        (session_at, title) (#1474). Only 'ready' and 'draft': a posted, dropped or unfinished one
        is never listed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, title FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND state = 'ready' AND status = 'draft' ORDER BY session_at DESC LIMIT %s",
                (guild_id, campaign_id, limit),
            )
            return [(int(r["session_at"]), str(r["title"])) for r in await cur.fetchall()]

    async def set_names_state(
        self, guild_id: int, campaign_id: str, session_at: int, state: int, *, only_from: int
    ) -> bool:
        """Move the names notice on (#2027), once: True only for the call that moved it from
        `only_from`, so the DM is told and reminded once however many rounds race."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET names_state = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND names_state = %s",
                (state, guild_id, campaign_id, session_at, only_from),
            )
            return cur.rowcount == 1

    async def names_waiting(self, guild_id: int, campaign_id: str) -> list[tuple[int, list[str]]]:
        """The campaign's recaps whose names the DM was told of and not yet reminded about, as
        (session_at, names), oldest first (#2027)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, to_confirm FROM recap_jobs WHERE guild_id = %s"
                " AND campaign_id = %s AND state = 'ready' AND names_state = 1"
                " ORDER BY session_at LIMIT 5",
                (guild_id, campaign_id),
            )
            return [(int(r["session_at"]), list(r["to_confirm"])) for r in await cur.fetchall()]

    async def to_post(self, guild_id: int) -> list[tuple[str, int]]:
        """Recaps the DM pressed Post on, which the bot has yet to send."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_at FROM recap_jobs WHERE guild_id = %s"
                " AND status = 'posting' ORDER BY session_at LIMIT 20",
                (guild_id,),
            )
            return [(r["campaign_id"], int(r["session_at"])) for r in await cur.fetchall()]

    async def posted(
        self, guild_id: int, campaign_id: str, session_at: int, *, url: str = ""
    ) -> bool:
        """The bot sent it: 'posting' becomes 'posted', once. `url`: the link to the first
        message (an https Discord link, else none is kept). The database ends the progress clock."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posted', decided_at = %s, posted_url = %s"
                " WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'posting'",
                (
                    int(self._now()),
                    url if _DISCORD_LINK.match(url) else None,
                    guild_id,
                    campaign_id,
                    session_at,
                ),
            )
            return bool(cur.rowcount)

    async def posting_step(
        self, guild_id: int, campaign_id: str, session_at: int, step: str
    ) -> None:
        """The post has got to `step` (check, then post). Only while it is under way: the database
        starts the clock at 'check' when the status becomes 'posting' and stops it when it ends."""
        if step not in ("check", "post"):
            raise ValueError(step)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET posting_step = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND status = 'posting' AND posting_step IS NOT NULL",
                (step, guild_id, campaign_id, session_at),
            )

    async def decide(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        action: str,
        *,
        require_approved: bool = False,
        require_current: bool = False,
    ) -> bool:
        """The DM's choice made on the DM screen (#1455), the same two moves the website's page
        makes: `post` (a ready draft becomes 'posting', which the bot then sends after its
        checks) or `drop` (a draft is not posted). False: it was already decided, or isn't ready.
        `require_current`: only if the DM's approval is of the text as it is now, in the same
        statement (an edit after approving, or a draft never approved, posts nothing)."""
        if action == "post":
            sql = "status = 'posting' WHERE state = 'ready' AND status = 'draft'"
            if require_current:  # an approval of other words than these ends here, in SQL
                sql += (
                    " AND approved_hash = encode(sha256(convert_to("
                    "title || E'\\n' || summary, 'UTF8')), 'hex')"
                )
            if require_approved:  # the text as it is now is the text the DM approved, in SQL
                sql += (
                    " AND approved_hash = encode(sha256(convert_to("
                    "title || E'\\n' || summary, 'UTF8')), 'hex')"
                    # Only a text-only approval is posted at once; audio is made and reviewed first,
                    # so a stale press after the choice became audio must post nothing.
                    " AND audio_choice = 'text'"
                )
        elif action == "drop":
            # "Not this one" on a recap made again keeps the one it replaced (#2608).
            if await self.keep_previous(guild_id, campaign_id, session_at):
                return True
            sql = "status = 'dropped' WHERE status = 'draft'"
        else:
            raise ValueError(action)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"UPDATE recap_jobs SET {sql} AND guild_id = %s AND campaign_id = %s"
                " AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def recent(
        self, guild_id: int, campaign_id: str, limit: int = 25
    ) -> list[tuple[int, str, str]]:
        """(session_at, title, status) of the campaign's ready recaps, newest first: the list
        "Share a recap" picks from (#2118). Dropped ones are left out; drafts are listed because
        sharing one counts as posting it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_at, title, status FROM recap_jobs WHERE guild_id = %s"
                " AND campaign_id = %s AND state = 'ready' AND status IN ('draft', 'posted')"
                " ORDER BY session_at DESC LIMIT %s",
                (guild_id, campaign_id, max(1, min(limit, 25))),
            )
            return [(int(r["session_at"]), r["title"], r["status"]) for r in await cur.fetchall()]

    async def share_token(
        self, guild_id: int, campaign_id: str, session_at: int, *, new: bool = False
    ) -> str | None:
        """The public link's token for a POSTED recap, made on first use (or replaced with
        `new`), the same column the website's share door writes. None: it isn't posted. The token
        is a secret link: it is returned, never logged."""
        fresh = secrets.token_urlsafe(32)  # 43 URL-safe characters, 256 bits
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET share_token = CASE WHEN %s OR share_token IS NULL"
                " THEN %s ELSE share_token END WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND status = 'posted' RETURNING share_token",
                (new, fresh, guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["share_token"])

    async def latest_draft(self, guild_id: int, campaign_id: str) -> Job | None:
        """The newest recap that is ready and not yet posted or skipped: the one a DM's spoken
        feedback is about (#2076)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND state = 'ready' AND status = 'draft' ORDER BY session_at DESC LIMIT 1",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return None if row is None else _job(row)

    async def add_corrections(
        self, guild_id: int, campaign_id: str, session_at: int, items: Sequence[str]
    ) -> bool:
        """Keep what the DM corrected in this recap, for its next try only (#2076). Newest
        `MAX_CORRECTIONS` kept. False: the recap is gone or already posted or skipped."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT corrections FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND status = 'draft' FOR UPDATE",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            if row is None:
                return False
            have = list(row["corrections"])
            kept = [*have, *(i for i in items if i not in have)][-MAX_CORRECTIONS:]  # (once each)
            await conn.execute(
                "UPDATE recap_jobs SET corrections = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s",
                (kept, guild_id, campaign_id, session_at),
            )
            return True

    async def try_again(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """The website's Try again from the DM screen (#2076): the same move (one more try, back
        to the queue), at most `MAX_TRIES` times. False: not allowed now. The worker checks the
        month's allowance and gives the try back when it is refused."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET tries = tries + 1, state = 'queued',"
                # A new try is a new text: the approval of the old one ends here (#2083), as it
                # does on the website, where the text no longer matches the approved hash.
                " approved_by = 0, approved_at = 0, approved_hash = '', audio_choice = ''"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND status = 'draft' AND state IN ('ready', 'failed') AND tries < %s",
                (guild_id, campaign_id, session_at, MAX_TRIES),
            )
            return bool(cur.rowcount)

    async def back_to_draft(
        self, guild_id: int, campaign_id: str, session_at: int, *, note: str
    ) -> None:
        """Nothing could be sent: it is still the DM's to post. `note` (plain words) is shown
        on the page."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'draft', decided_at = NULL, last_error = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND status = 'posting'",
                (note[:200], guild_id, campaign_id, session_at),
            )

    async def address(self, guild_id: int, user_id: int) -> str | None:
        """The DM's account address, through the one door that opens it (migration 0079)."""
        async with self._db.recap_mail(guild_id) as conn:
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (user_id,))
            row = await cur.fetchone()
        return None if row is None or not row["a"] else str(row["a"])


class RecapSection:
    """Recaps in campaign backups (an `ExportSection`)."""

    name = "recaps"

    def __init__(self, forget_audio: Callable[[int, str], None] | None = None) -> None:
        self._forget_audio = forget_audio  # removes a campaign's audio files from the disk

    _KEYS = frozenset({"session_at", "title", "summary", "housekeeping", "music", "game_day"})
    # A recap written by someone at the table also carries who wrote it, when it was added and
    # the recaps it replaced (#2609); a copy made before has none of the three.
    _UPLOADED = frozenset({"written_by", "uploaded_at", "earlier"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {COLUMNS} FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
            " AND state = 'ready' AND summary <> '' ORDER BY session_at",
            (guild_id, campaign_id),
        )
        return [
            {
                "session_at": r.session_at,
                "title": r.title,
                "summary": r.summary,
                "housekeeping": list(r.housekeeping),
                "music": list(r.music),
                "game_day": r.game_day,
                **(
                    {
                        "written_by": r.written_by,
                        "uploaded_at": r.uploaded_at,
                        "earlier": _earlier_out(r.earlier),
                    }
                    if r.written_by
                    else {}
                ),
            }
            for r in map(_job, await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if len(rows) > MAX_STORED:
            raise CampaignError(DAMAGED)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) not in (
                set(self._KEYS),
                set(self._KEYS | self._UPLOADED),
            ):
                raise CampaignError(DAMAGED)
            if "written_by" in row:
                author, at, earlier = row["written_by"], row["uploaded_at"], row["earlier"]
                if not isinstance(author, str) or not 1 <= len(author) <= 80:
                    raise CampaignError(DAMAGED)
                if type(at) is not int or at < 0:
                    raise CampaignError(DAMAGED)
                row["earlier"] = _earlier_in(earlier)
            if type(row["session_at"]) is not int or row["session_at"] <= 0:
                raise CampaignError(DAMAGED)
            day = row["game_day"]
            if day is not None and (type(day) is not int or day <= 0):
                raise CampaignError(DAMAGED)
            for key, limit in (("title", 120), ("summary", STORY_COLUMN_MAX)):
                if not isinstance(row[key], str) or not 1 <= len(row[key]) <= limit:
                    raise CampaignError(DAMAGED)
            for key in ("housekeeping", "music"):
                value = row[key]
                if not isinstance(value, list) or len(value) > 20:
                    raise CampaignError(DAMAGED)
                if not all(isinstance(v, str) and len(v) <= 1000 for v in value):
                    raise CampaignError(DAMAGED)
            out.append(row)
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = self.check(list(rows)) if rows else []
        if not checked:
            return
        cur = await conn.execute(
            "SELECT owner_user_id FROM campaigns WHERE guild_id = %s AND id = %s",
            (guild_id, campaign_id),
        )
        owner = await cur.fetchone()
        who = int(owner["owner_user_id"] or 0) if owner else 0
        if who <= 0:
            return  # nobody to email or to ask: the recaps are not restored
        now = int(time.time())
        for row in checked:
            await conn.execute(
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state,"
                " title, summary, original, housekeeping, music, game_day, status, created_at,"
                " updated_at, written_by, uploaded_by, uploaded_at, earlier, checked_at)"
                " VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s, 'draft',"
                " %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (guild_id, campaign_id, session_at)"
                " DO NOTHING",
                (
                    guild_id,
                    campaign_id,
                    row["session_at"],
                    who,
                    row["title"],
                    row["summary"],
                    row["summary"],
                    row["housekeeping"],
                    row["music"],
                    row["game_day"],
                    now,
                    now,
                    row.get("written_by", ""),
                    who if row.get("written_by") else 0,
                    row.get("uploaded_at", 0),
                    Jsonb(row.get("earlier", [])),
                    now if row.get("written_by") else 0,  # (its names were checked where it was)
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        if self._forget_audio is not None:
            await asyncio.to_thread(  # the narration and music go with it
                self._forget_audio, guild_id, campaign_id
            )


def _earlier_out(earlier: Any) -> list[dict[str, Any]]:
    """The recaps an uploaded one replaced, as a backup keeps them: the words and who wrote them,
    never where they were posted (a link into one server means nothing in another)."""
    return [
        {
            "title": str(e.get("title", ""))[:120],
            "summary": str(e.get("summary", ""))[:50000],
            "written_by": str(e.get("written_by", ""))[:80],
            "replaced_at": int(e.get("replaced_at") or 0),
        }
        for e in (earlier if isinstance(earlier, list) else [])
        if isinstance(e, dict)
    ]


def _earlier_in(raw: object) -> list[dict[str, Any]]:
    """The same, read from an untrusted file: a short list of well-formed entries or a refusal."""
    if not isinstance(raw, list) or len(raw) > 10:
        raise CampaignError(DAMAGED)
    out: list[dict[str, Any]] = []
    for e in raw:
        if (
            not isinstance(e, dict)
            or set(e) != {"title", "summary", "written_by", "replaced_at"}
            or not isinstance(e["title"], str)
            or len(e["title"]) > 120
            or not isinstance(e["summary"], str)
            or len(e["summary"]) > 50000
            or not isinstance(e["written_by"], str)
            or len(e["written_by"]) > 80
            or type(e["replaced_at"]) is not int
            or e["replaced_at"] < 0
        ):
            raise CampaignError(DAMAGED)
        out.append(e)
    return out


class RecapRightsSection:
    """Who confirmed the right to use recaps written by the table, and when (#2609), in a backup:
    it belongs to the campaign, so a copy that holds such recaps is not asked again."""

    name = "recap_rights"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT user_id, confirmed_at FROM recap_upload_rights"
            " WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        return [
            {"user_id": str(r["user_id"]), "confirmed_at": int(r["confirmed_at"])}
            for r in await cur.fetchall()
        ]

    @staticmethod
    def check(rows: list[Any]) -> list[dict[str, Any]]:
        if len(rows) > 1:
            raise CampaignError(DAMAGED)
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"user_id", "confirmed_at"}
                or not isinstance(row["user_id"], str)
                or not row["user_id"].isascii()
                or not row["user_id"].isdigit()
                or not 0 < int(row["user_id"]) <= 2**63 - 1
                or type(row["confirmed_at"]) is not int
                or row["confirmed_at"] < 0
            ):
                raise CampaignError(DAMAGED)
        return rows

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        for row in self.check(list(rows)) if rows else []:
            await conn.execute(
                "INSERT INTO recap_upload_rights (guild_id, campaign_id, user_id, confirmed_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, int(row["user_id"]), row["confirmed_at"]),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM recap_upload_rights WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
