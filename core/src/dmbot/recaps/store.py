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
from dmbot.transcript.respell import respell

DAMAGED = "That backup file's recaps are damaged, so nothing was restored."
MAX_TRIES = 2  # "Try again" at most this many times for one session
MAX_CORRECTIONS = 10  # corrections kept for one recap (#2076)
MAX_STORED = 50  # recaps kept for one campaign in a backup
LEASE_S = 15 * 60  # a worker that stops leaves a job free again after this long
AUDIO_MAX_TRIES = 2  # starts: the first, and one more an hour later
STUCK = "The narration and music were never finished. The text is fine."
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
    " posting_since, posting_step, posted_url"
)


_LIMITS = {"title": 120, "summary": 6000, "original": 6000}  # the table's own limits


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
    spent: int  # calls ever made for this session, never reset: the day's tally
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
    )


# The progress clock stops when the narration stops, whatever happened to it: the right-hand sides
# see the row as it was, so the step is read before it is cleared (#2435). A post's own step stays.
_AUDIO_CLOCK_OFF = (
    "posting_since = CASE WHEN posting_step = 'audio'"
    " THEN NULLIF(posting_since, posting_since) ELSE posting_since END,"
    " posting_step = CASE WHEN posting_step = 'audio'"
    " THEN NULLIF(posting_step, posting_step) ELSE posting_step END"
)

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
        if not fields or not set(fields) <= _WRITABLE:
            raise ValueError(sorted(set(fields) - _WRITABLE))
        sets = ", ".join(f"{name} = %s" for name in fields)
        values: list[Any] = []
        for name, value in fields.items():
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
                f"UPDATE recap_jobs SET {sets}, updated_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'draft'",
                (*values, int(self._now()), guild_id, campaign_id, session_at),
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
                " WHERE guild_id = %s AND state = 'ready' AND audio = 'queued'"
                " AND audio_tries < %s AND status IN ('draft', 'posting', 'posted')"
                " AND audio_next_at <= %s AND leased_to <= %s"
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
                " AND session_at = %s AND leased_to <= %s AND audio_next_at <= %s"
                " AND audio_tries < %s"
                " AND state = 'ready' AND audio = 'queued'"
                " AND status IN ('draft', 'posting', 'posted')"
                " AND NOT EXISTS (SELECT 1 FROM recap_jobs r2 WHERE r2.guild_id = %s"
                "   AND r2.campaign_id = %s AND r2.leased_to > %s)",
                (
                    now + AUDIO_LEASE_S,
                    guild_id,
                    campaign_id,
                    session_at,
                    now,
                    now,
                    AUDIO_MAX_TRIES,
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
    ) -> bool:
        """Record where the audio has got to. Only while the recap is draft, posting or posted (a
        posted one is narrated again for the page when its text changed, #2183), and, with
        `unchanged` (title, summary), only if the text is still what the audio was made from.
        False if nothing was written."""
        sets = ["audio = %s", "audio_error = %s", _AUDIO_CLOCK_OFF]
        values: list[Any] = [audio, error[:200]]
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
        """AI calls the campaign's recaps made since then, for the daily cap."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT COALESCE(sum(spent), 0) AS n FROM recap_jobs WHERE guild_id = %s"
                " AND campaign_id = %s AND updated_at >= %s",
                (guild_id, campaign_id, since),
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
                "SELECT COALESCE(sum((CASE WHEN state = 'ready' THEN 1 ELSE 0 END) + tries), 0)"
                " AS recaps, COALESCE(sum((CASE WHEN audio = 'ready' THEN 1 ELSE 0 END)"
                " + (CASE WHEN audio <> 'off' THEN tries ELSE 0 END)), 0)"
                " AS narrated FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND created_at >= %s AND created_at < %s",
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
                " next_at = 0, calls = 0, spent = 0, window_count = 0, window_done = 0,"
                " last_error = '', problems = '{}', beats = '[]', split = NULL, critics = NULL,"
                " tries = 0, approved_by = 0, approved_at = 0, approved_hash = '',"
                " audio_choice = ''"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND status = 'draft' AND state = 'failed'",
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
    ) -> bool:
        """The DM's choice made on the DM screen (#1455), the same two moves the website's page
        makes: `post` (a ready draft becomes 'posting', which the bot then sends after its
        checks) or `drop` (a draft is not posted). False: it was already decided, or isn't ready."""
        if action == "post":
            sql = "status = 'posting' WHERE state = 'ready' AND status = 'draft'"
            if require_approved:  # the text as it is now is the text the DM approved, in SQL
                sql += (
                    " AND approved_hash = encode(sha256(convert_to("
                    "title || E'\\n' || summary, 'UTF8')), 'hex')"
                    # Only a text-only approval is posted at once; audio is made and reviewed first,
                    # so a stale press after the choice became audio must post nothing.
                    " AND audio_choice = 'text'"
                )
        elif action == "drop":
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
            }
            for r in map(_job, await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        if len(rows) > MAX_STORED:
            raise CampaignError(DAMAGED)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(self._KEYS):
                raise CampaignError(DAMAGED)
            if type(row["session_at"]) is not int or row["session_at"] <= 0:
                raise CampaignError(DAMAGED)
            day = row["game_day"]
            if day is not None and (type(day) is not int or day <= 0):
                raise CampaignError(DAMAGED)
            for key, limit in (("title", 120), ("summary", 6000)):
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
                " updated_at) VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s, 'draft',"
                " %s, %s) ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
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
