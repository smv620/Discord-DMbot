"""The names an imported session's name scan heard past its first 10, waiting for the DM behind
"📝 More names" (#5055 B; Supervisor's ruling on GraphEngineer's design note): the table
`import_more_names` (migration 0601).

A live session keeps its short list of 10 (`scan.MAX_SUGGESTIONS`). An imported session is read
once, so a name cut there would be lost for good: it waits here instead, and the DM's press turns
every waiting name into a name to check, exactly as the scan makes them (`bot._propose_new_names`),
deleting the rows. Nothing that lists names reads this table. Rows go with their session (the
foreign key) and with its transcript (`forget_session`). Counts only leave this module; its words
are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import NAME_MAX, name_key

TAKE_MAX = 300  # names one press makes: a long import past this keeps the rest for the next press
ALSO_MAX = 10  # other spellings kept with a name (the table's own limit)
TIMES_MAX = 100_000  # the table's own limit on times heard
PROGRESS_EVERY = 10  # a press says how far it has got every this many names (the wait rule)


@dataclass(frozen=True)
class HeldName:
    """One waiting name: as heard most often, how many times, and the other ways it was said."""

    name: str
    times: int
    also: tuple[str, ...] = ()
    session_id: str = ""


class MoreNames:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def hold(
        self, guild_id: int, campaign_id: str, session_id: str, names: Sequence[HeldName]
    ) -> int:
        """Keep these names for the session, once each (a scan run again changes nothing: the
        first row stays). Returns how many were new."""
        rows = [
            (
                session_id, name, key, min(max(1, n.times), TIMES_MAX),
                [a.strip()[:NAME_MAX] for a in n.also[:ALSO_MAX]], int(self._now()),
            )
            for n in names
            if (name := n.name.strip()[:NAME_MAX]) and (key := name_key(name))
        ]  # fmt: skip
        if not rows:
            return 0
        made = 0
        async with self._db.guild(guild_id) as conn:
            for row in rows:
                cur = await conn.execute(
                    "INSERT INTO import_more_names (guild_id, campaign_id, session_id, name,"
                    " name_key, times, also, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, *row),
                )
                made += int(cur.rowcount or 0)
        return made

    async def count(self, guild_id: int, campaign_id: str) -> int:
        """How many names wait (the Questions list's count). Changes nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM import_more_names WHERE guild_id = %s"
                " AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    async def take(self, guild_id: int, campaign_id: str, limit: int = TAKE_MAX) -> list[HeldName]:
        """The waiting names, the most heard first, deleted as they are read (one statement, so two
        presses never take the same name twice). At most `limit`; the rest keep waiting."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM import_more_names WHERE (guild_id, campaign_id, session_id, name_key)"
                " IN (SELECT guild_id, campaign_id, session_id, name_key FROM import_more_names"
                " WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY times DESC, created_at, name_key LIMIT %s)"
                " RETURNING name, times, also, session_id",
                (guild_id, campaign_id, max(0, limit)),
            )
            got = [_held(r) for r in await cur.fetchall()]
        got.sort(key=lambda h: (-h.times, h.name))
        return got

    async def of_session(self, guild_id: int, campaign_id: str, session_id: str) -> list[HeldName]:
        """This session's waiting names, as stored (for tests and tools)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT name, times, also, session_id FROM import_more_names WHERE guild_id = %s"
                " AND campaign_id = %s AND session_id = %s ORDER BY times DESC, name_key",
                (guild_id, campaign_id, session_id),
            )
            return [_held(r) for r in await cur.fetchall()]


def _held(r: Any) -> HeldName:
    return HeldName(str(r["name"]), int(r["times"]), tuple(r["also"] or ()), str(r["session_id"]))


async def forget_session(conn: Any, guild_id: int, session_id: str) -> None:
    """A session's transcript is deleted: the names waiting from it go too (they are words heard
    in its lines). In the caller's transaction."""
    await conn.execute(
        "DELETE FROM import_more_names WHERE guild_id = %s AND session_id = %s",
        (guild_id, session_id),
    )
