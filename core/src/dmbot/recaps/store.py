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

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

DAMAGED = "That backup file's recaps are damaged, so nothing was restored."
MAX_TRIES = 2  # "Try again" at most this many times for one session
MAX_STORED = 50  # recaps kept for one campaign in a backup
LEASE_S = 15 * 60  # a worker that stops leaves a job free again after this long
TERMINAL = ("ready", "failed")
STATES = ("queued", "beats", "checked", "prose", "housekeeping", "ready")

COLUMNS = (
    "session_at, dm_user_id, state, attempt, errors, restarts, next_at, calls, window_count,"
    " window_done, spent, last_error,"
    " problems, beats, title, summary, original, housekeeping, music, game_day, status, tries,"
    " speakers, grade, emailed_at, created_at, updated_at"
)


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

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL


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
        "title",
        "summary",
        "original",
        "housekeeping",
        "music",
        "game_day",
        "speakers",
        "grade",
        "emailed_at",
    }
)
_LISTS = frozenset({"problems", "housekeeping", "music", "speakers"})


class RecapStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

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
                "UPDATE recap_jobs SET leased_to = 0 WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s",
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
            else:
                values.append(value)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                f"UPDATE recap_jobs SET {sets}, updated_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'draft'",
                (*values, int(self._now()), guild_id, campaign_id, session_at),
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

    async def to_post(self, guild_id: int) -> list[tuple[str, int]]:
        """Recaps the DM pressed Post on, which the bot has yet to send."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_at FROM recap_jobs WHERE guild_id = %s"
                " AND status = 'posting' ORDER BY session_at LIMIT 20",
                (guild_id,),
            )
            return [(r["campaign_id"], int(r["session_at"])) for r in await cur.fetchall()]

    async def posted(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """The bot sent it: 'posting' becomes 'posted', once."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posted', decided_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'posting'",
                (int(self._now()), guild_id, campaign_id, session_at),
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
        """The DM's account address, through the one door that opens it (migration 0078)."""
        async with self._db.recap_mail(guild_id) as conn:
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (user_id,))
            row = await cur.fetchone()
        return None if row is None or not row["a"] else str(row["a"])


class RecapSection:
    """Recaps in campaign backups (an `ExportSection`)."""

    name = "recaps"
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
