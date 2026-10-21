"""A session's recap waits for the DM's name answers (#2169; docs/PLAN.md, "Recaps").

The recap is written from the cleaned transcript and the confirmed names. While the DM's name
questions from the session are unanswered it would use guesses, and its "Facts to be confirmed"
list would repeat the same questions. So at the end of a session with open questions the recap is
held instead of queued: a row here, no job and no cost. It is released, and the job made in the
same transaction, when the last question is answered (the recap round checks) or when the DM
presses "Write the recap now". A hold never expires on its own.

A session whose story extractor is still to run is held at once, even with nothing open: the
names it proposes are name questions too (#2169, Supervisor's ruling). That hold is silent
(`asked` false) until the extractor is done and the questions are counted again; if that never
happens the round releases it after `QUIET_WAIT_S` rather than leave the recap waiting.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database


@dataclass(frozen=True, slots=True)
class Hold:
    campaign_id: str
    session_at: int
    session_id: str
    dm_user_id: int
    channel_id: int  # the DM screen, where the question and the "writing it now" line go
    created_at: int
    asked: bool = True  # the DM was told; False while the story extractor may still add names


QUEUED, ALREADY, GONE = "queued", "already", "gone"
QUIET_WAIT_S = 3 * 60 * 60  # a silent hold the extractor never reopened is let go after this


def question_text(count: int) -> str:
    """The line on the DM screen, in plain words."""
    noun = "name question" if count == 1 else "name questions"
    return f"📝 {count} {noun} from tonight. Answer them and DMbot writes the recap."


class HoldStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock
        # Servers last seen holding nothing, so a round asks the database about the others only.
        # This process is the only one that holds, so a hold made here clears the mark.
        self._empty: set[int] = set()
        self._made: dict[int, int] = {}  # holds made per server, so a read can tell it raced one

    async def hold(
        self,
        guild_id: int,
        campaign_id: str,
        session_at: int,
        *,
        session_id: str,
        dm_user_id: int,
        channel_id: int,
        asked: bool = True,
    ) -> bool:
        """Hold this session's recap. False when it is held already, or a recap job exists for the
        session (a repeat of the end of the session): one wait, and never after a job."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO recap_holds (guild_id, campaign_id, session_at, session_id,"
                " dm_user_id, channel_id, created_at, asked)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s"
                " WHERE NOT EXISTS (SELECT 1 FROM recap_jobs WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
                (
                    guild_id,
                    campaign_id,
                    session_at,
                    session_id,
                    dm_user_id,
                    channel_id,
                    int(self._now()),
                    asked,
                    guild_id,
                    campaign_id,
                    session_at,
                ),
            )
            made = bool(cur.rowcount)
        if made:
            self._made[guild_id] = self._made.get(guild_id, 0) + 1
            self._empty.discard(guild_id)
        return made

    async def _read(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, session_at, session_id, dm_user_id, channel_id, created_at,"
                " asked FROM recap_holds ORDER BY created_at, session_at"
            )
            return list(await cur.fetchall())

    def _mark_empty(self, guild_id: int, seen: int) -> None:
        """Remember a server holds nothing, unless a hold was made while the read was running."""
        if self._made.get(guild_id, 0) == seen:
            self._empty.add(guild_id)

    async def mark_asked(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        """The DM has now been told. False when it was already (or the hold is gone)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_holds SET asked = TRUE WHERE campaign_id = %s AND session_at = %s"
                " AND NOT asked",
                (campaign_id, session_at),
            )
            return bool(cur.rowcount)

    async def unmark_asked(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        """Give back a claim to ask the DM when the question could not be posted."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE recap_holds SET asked = FALSE WHERE campaign_id = %s AND session_at = %s",
                (campaign_id, session_at),
            )

    async def held(self, guild_id: int) -> list[Hold]:
        """Every held recap in the server, oldest first. A server last seen with none is not asked
        again until a hold is made in it (the round runs every minute for every server)."""
        if guild_id in self._empty:
            return []
        seen = self._made.get(guild_id, 0)
        rows = await self._read(guild_id)
        if not rows:
            self._mark_empty(guild_id, seen)
        return [
            Hold(
                r["campaign_id"],
                int(r["session_at"]),
                r["session_id"],
                int(r["dm_user_id"]),
                int(r["channel_id"]),
                int(r["created_at"]),
                bool(r["asked"]),
            )
            for r in rows
        ]

    async def get(self, guild_id: int, campaign_id: str, session_at: int) -> Hold | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_id, dm_user_id, channel_id, created_at, asked FROM recap_holds"
                " WHERE campaign_id = %s AND session_at = %s",
                (campaign_id, session_at),
            )
            r = await cur.fetchone()
        if r is None:
            return None
        return Hold(
            campaign_id,
            session_at,
            r["session_id"],
            int(r["dm_user_id"]),
            int(r["channel_id"]),
            int(r["created_at"]),
            bool(r["asked"]),
        )

    async def release(
        self, guild_id: int, campaign_id: str, session_at: int, *, queue: bool = True
    ) -> str:
        """End the hold and, in the same transaction, make the recap job (so a stop in between
        cannot lose the recap). "queued" for the one call that did both, "already" when the hold
        went but a job already existed, "gone" when there was no hold (a second press, or another
        release got there first). With `queue=False` the hold is only dropped (recaps were
        switched off meanwhile): "gone"-like, nothing is made."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM recap_holds WHERE campaign_id = %s AND session_at = %s"
                " RETURNING dm_user_id",
                (campaign_id, session_at),
            )
            row = await cur.fetchone()
            if row is None:
                return GONE
            if not queue:
                return GONE
            now = int(self._now())
            made = await conn.execute(
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id,"
                " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO NOTHING",
                (guild_id, campaign_id, session_at, int(row["dm_user_id"]), now, now),
            )
            return QUEUED if made.rowcount else ALREADY
