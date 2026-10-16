"""A session's recap, kept with the campaign (#1382): the draft the DM reviews, what they did with
it, and the earlier one that is the next recap's bridge. Every read and write goes through
`Database.guild`, so Postgres shows only that server's rows, and each query names the campaign.
Also its part of a campaign backup (an `ExportSection`): the text and music prompts, not the
decision (a restored campaign's recaps are drafts again, never posted)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database

DAMAGED = "That backup file's recaps are damaged, so nothing was restored."
MAX_TRIES = 2  # "Try again" at most this many times for one session
MAX_STORED = 50  # recaps kept for one campaign in a backup


@dataclass(frozen=True, slots=True)
class Recap:
    session_at: int
    title: str
    summary: str
    housekeeping: tuple[str, ...]
    music: tuple[str, ...]
    game_day: int | None
    status: str  # draft, posted or dropped
    tries: int
    speakers: tuple[int, ...] = ()  # whose words were sent to write it (checked again at Post)


def _recap(r: dict[str, Any]) -> Recap:
    return Recap(
        int(r["session_at"]),
        r["title"],
        r["summary"],
        tuple(r["housekeeping"]),
        tuple(r["music"]),
        None if r["game_day"] is None else int(r["game_day"]),
        r["status"],
        int(r["tries"]),
        tuple(int(s) for s in r["speakers"]),
    )


_COLUMNS = "session_at, title, summary, housekeeping, music, game_day, status, tries, speakers"


class RecapStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def get(self, guild_id: int, campaign_id: str, session_at: int) -> Recap | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM session_recaps"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
        return None if row is None else _recap(row)

    async def bridge(self, guild_id: int, campaign_id: str, before: int) -> Recap | None:
        """The latest earlier recap of this campaign that was posted: the bridge into this
        one (a draft the DM never approved is not a bridge). Never another campaign's: the query
        names the campaign."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM session_recaps"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at < %s"
                " AND status = 'posted' ORDER BY session_at DESC LIMIT 1",
                (guild_id, campaign_id, before),
            )
            row = await cur.fetchone()
        return None if row is None else _recap(row)

    async def save(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        *,
        title: str,
        summary: str,
        housekeeping: tuple[str, ...],
        music: tuple[str, ...],
        game_day: int | None,
        tries: int = 0,
        speakers: tuple[int, ...] = (),
    ) -> None:
        """A new draft for this session (or a replacement, when the DM asked again). A posted
        recap is never replaced."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO session_recaps (guild_id, campaign_id, session_at, title, summary,"
                " housekeeping, music, game_day, status, tries, speakers, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO UPDATE SET"
                " title = EXCLUDED.title, summary = EXCLUDED.summary, speakers = EXCLUDED.speakers,"
                " housekeeping = EXCLUDED.housekeeping, music = EXCLUDED.music,"
                " game_day = EXCLUDED.game_day, status = 'draft', tries = EXCLUDED.tries,"
                " decided_at = NULL WHERE session_recaps.status <> 'posted'",
                (
                    guild_id,
                    campaign_id,
                    session_at,
                    title,
                    summary,
                    list(housekeeping),
                    list(music),
                    game_day,
                    tries,
                    list(speakers),
                    int(self._now()),
                ),
            )

    async def edit(self, guild_id: int, campaign_id: str, session_at: int, *, summary: str) -> bool:
        """The DM's edit of the text (only while it is a draft). The music prompts stay as they
        were. True if a draft was changed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_recaps SET summary = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND status = 'draft'",
                (summary, guild_id, campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def decide(self, guild_id: int, campaign_id: str, session_at: int, status: str) -> bool:
        """Posted or dropped, once: only a draft can be decided. True if this call decided it, so
        two presses cannot both post."""
        if status not in ("posted", "dropped"):
            raise ValueError(status)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_recaps SET status = %s, decided_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'draft'",
                (status, int(self._now()), guild_id, campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def claim_try(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """Use up one of the DM's "Try again"s before the AI is called, so a try that fails
        still counts and two quick presses cannot both go through. True if one was left."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE session_recaps SET tries = tries + 1 WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s AND status = 'draft' AND tries < %s",
                (guild_id, campaign_id, session_at, MAX_TRIES),
            )
            return bool(cur.rowcount)

    async def reopen(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        """Back to a draft: the DM pressed Post but nothing could be sent, so it is still theirs
        to post."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE session_recaps SET status = 'draft', decided_at = NULL"
                " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
                " AND status = 'posted'",
                (guild_id, campaign_id, session_at),
            )


class RecapSection:
    """Recaps in campaign backups (an `ExportSection`)."""

    name = "session_recaps"
    _KEYS = frozenset({"session_at", "title", "summary", "housekeeping", "music", "game_day"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM session_recaps WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY session_at",
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
            for r in map(_recap, await cur.fetchall())
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
            for key, limit in (("title", 120), ("summary", 4000)):
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
        for row in self.check(list(rows)) if rows else []:
            await conn.execute(
                "INSERT INTO session_recaps (guild_id, campaign_id, session_at, title, summary,"
                " housekeeping, music, game_day, status, tries, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', 0, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
                (
                    guild_id,
                    campaign_id,
                    row["session_at"],
                    row["title"],
                    row["summary"],
                    row["housekeeping"],
                    row["music"],
                    row["game_day"],
                    int(time.time()),
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM session_recaps WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
