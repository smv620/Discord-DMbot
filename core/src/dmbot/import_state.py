"""What an import's scans leave for the bot's one end-of-import report, kept in the database
(#4904 step 1), so a worker that scans can hand it to the bot that reports, and a restart loses
nothing. It replaces the bot's per-campaign dicts one at a time (`_import_unchecked`,
`_import_left_told` first). A value is a counter, a set of strings or a list of strings, kept as the
row's JSON `data` under a `kind` word; every call is one statement, so two processes adding at once
never lose an update. `take` deletes and returns, so a report is made once. Counts, ids and the
names the scans added only: never a line of a transcript.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from typing import Any

from dmbot.db import Database

UNCHECKED = "unchecked"  # sessions of an import whose name check was given up
LEFT_TOLD = "left_told"  # sessions the story read has not reached, as last told
ADDED_NAMES = "names"  # names an import's scans added
SPEAKERS = "speakers"  # who spoke in the lines its scans read (user ids as text)
ENTRIES = "entries"  # the entries its scans proposed (the only ones the import may add)
STARTS = "starts"  # the starts of the sessions its scans read (as text)
BATCHES = "batches"  # the import batches whose scans ended in its report
PARTY = "party"  # player characters linked (a counter: the largest seen)
UNHONOURED = "unhonoured"  # why recaps were not made (one line each, kept as many times)
QUEUED = "queued"  # the starts of the sessions whose recap was queued at once
NAME_ASKS = "name_asks"  # words PlotBot was not sure of in a session: "<session> <times> <word>"
REPORTS = "reports"  # each scanned session's name-pass report, as text (#4904)
NAMES_CUT = "names_cut"  # names a cap left out of its sessions (a counter, #5055 A)

_KIND = re.compile(r"^[a-z_]{1,40}$")


class ImportState:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    @staticmethod
    def _kind(kind: str) -> str:
        if not _KIND.match(kind):
            raise ValueError("a kind is letters and underscores")
        return kind

    async def bump(self, guild_id: int, campaign_id: str, kind: str, by: int = 1) -> int:
        """Add to a counter and return it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO import_state (guild_id, campaign_id, kind, data, updated_at)"
                " VALUES (%s, %s, %s, to_jsonb(%s::int), %s)"
                " ON CONFLICT (guild_id, campaign_id, kind) DO UPDATE SET"
                " data = to_jsonb((import_state.data #>> '{}')::int + %s), updated_at = %s"
                " RETURNING (data #>> '{}')::int AS n",
                (guild_id, campaign_id, self._kind(kind), by, int(self._now()), by,
                 int(self._now())),
            )  # fmt: skip
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

    async def put_count(self, guild_id: int, campaign_id: str, kind: str, n: int) -> None:
        """Set a counter (0 removes it, so nothing is left for a thing that is over)."""
        async with self._db.guild(guild_id) as conn:
            if n <= 0:
                await conn.execute(
                    "DELETE FROM import_state WHERE guild_id = %s AND campaign_id = %s"
                    " AND kind = %s",
                    (guild_id, campaign_id, self._kind(kind)),
                )
                return
            await conn.execute(
                "INSERT INTO import_state (guild_id, campaign_id, kind, data, updated_at)"
                " VALUES (%s, %s, %s, to_jsonb(%s::int), %s)"
                " ON CONFLICT (guild_id, campaign_id, kind) DO UPDATE SET"
                " data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, self._kind(kind), n, int(self._now())),
            )

    async def raise_count(self, guild_id: int, campaign_id: str, kind: str, to: int) -> None:
        """Make a counter at least `to`, in one statement (two processes never lower it)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO import_state (guild_id, campaign_id, kind, data, updated_at)"
                " VALUES (%s, %s, %s, to_jsonb(%s::int), %s)"
                " ON CONFLICT (guild_id, campaign_id, kind) DO UPDATE SET"
                " data = to_jsonb(GREATEST((import_state.data #>> '{}')::int, %s)),"
                " updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, self._kind(kind), to, int(self._now()), to),
            )

    async def count(self, guild_id: int, campaign_id: str, kind: str) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT (data #>> '{}')::int AS n FROM import_state"
                " WHERE guild_id = %s AND campaign_id = %s AND kind = %s",
                (guild_id, campaign_id, self._kind(kind)),
            )
            row = await cur.fetchone()
            return 0 if row is None else int(row["n"])

    async def take_count(self, guild_id: int, campaign_id: str, kind: str) -> int:
        """The counter, removed (0 when there was none): the report that uses it is made once."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM import_state WHERE guild_id = %s AND campaign_id = %s AND kind = %s"
                " RETURNING (data #>> '{}')::int AS n",
                (guild_id, campaign_id, self._kind(kind)),
            )
            row = await cur.fetchone()
            return 0 if row is None else int(row["n"])

    async def counted(self, guild_id: int, kind: str) -> dict[str, int]:
        """Every campaign of the server that has a counter of this kind."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, (data #>> '{}')::int AS n FROM import_state"
                " WHERE guild_id = %s AND kind = %s",
                (guild_id, self._kind(kind)),
            )
            return {r["campaign_id"]: int(r["n"]) for r in await cur.fetchall()}

    async def add_strings(
        self, guild_id: int, campaign_id: str, kind: str, values: Sequence[str]
    ) -> None:
        """Add to a set of strings (a value kept twice is kept once, in the order first added; a
        Sequence, so that order is the caller's, never a set's). A kind holds a counter or a list,
        never both: mixing them is a database error."""
        if not values:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO import_state (guild_id, campaign_id, kind, data, updated_at)"
                " VALUES (%s, %s, %s, %s::jsonb, %s)"
                " ON CONFLICT (guild_id, campaign_id, kind) DO UPDATE SET"
                " data = (SELECT COALESCE(jsonb_agg(v ORDER BY first), '[]'::jsonb) FROM ("
                "   SELECT v, min(ord) AS first FROM ("
                "     SELECT e AS v, ord FROM jsonb_array_elements_text(import_state.data)"
                "       WITH ORDINALITY AS a(e, ord)"
                "     UNION ALL"
                "     SELECT e, ord + 1000000 FROM jsonb_array_elements_text(EXCLUDED.data)"
                "       WITH ORDINALITY AS b(e, ord)) x GROUP BY v) y),"
                " updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, self._kind(kind), json.dumps(list(values)),
                 int(self._now())),
            )  # fmt: skip

    async def add_each(
        self, guild_id: int, campaign_id: str, kind: str, values: Sequence[str]
    ) -> None:
        """Add values that count one by one even when alike (two sessions that failed for the same
        reason are two): each is kept under its own key and `take_each` gives them back in order."""
        await self.add_strings(
            guild_id, campaign_id, kind, [f"{uuid.uuid4().hex}:{v}" for v in values]
        )

    async def take_each(self, guild_id: int, campaign_id: str, kind: str) -> list[str]:
        """The values `add_each` kept, in the order added, removed."""
        return [v.partition(":")[2] for v in await self.take_strings(guild_id, campaign_id, kind)]

    async def take_each_starting(
        self, guild_id: int, campaign_id: str, kind: str, prefix: str
    ) -> list[str]:
        """The values `add_each` kept that start with `prefix`, removed in ONE statement (the rest
        stay; a value another process adds meanwhile is never lost), in the order added. For a kind
        shared by several sessions of a campaign: each takes only its own (#4994)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH cur AS (SELECT data FROM import_state WHERE guild_id = %s"
                " AND campaign_id = %s AND kind = %s FOR UPDATE),"
                # (each value is `<32 hex of its own key>:<value>`: the value starts at 34)
                " mine AS (SELECT COALESCE(jsonb_agg(e ORDER BY o), '[]'::jsonb) AS m FROM cur"
                "   CROSS JOIN LATERAL jsonb_array_elements_text(cur.data) WITH ORDINALITY"
                "   AS a(e, o) WHERE starts_with(substr(e, 34), %s)),"
                " upd AS (UPDATE import_state SET updated_at = %s, data = ("
                "   SELECT COALESCE(jsonb_agg(e ORDER BY o), '[]'::jsonb) FROM cur"
                "   CROSS JOIN LATERAL jsonb_array_elements_text(cur.data) WITH ORDINALITY"
                "   AS a(e, o) WHERE NOT starts_with(substr(e, 34), %s))"
                "   WHERE guild_id = %s AND campaign_id = %s AND kind = %s RETURNING 1)"
                " SELECT m FROM mine",
                (guild_id, campaign_id, self._kind(kind), prefix, int(self._now()), prefix,
                 guild_id, campaign_id, self._kind(kind)),
            )  # fmt: skip
            row = await cur.fetchone()
        data: Any = None if row is None else row["m"]
        return [str(v)[33:] for v in data] if isinstance(data, list) else []

    async def take_strings(self, guild_id: int, campaign_id: str, kind: str) -> list[str]:
        """The set, removed ([] when there was none)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM import_state WHERE guild_id = %s AND campaign_id = %s AND kind = %s"
                " RETURNING data",
                (guild_id, campaign_id, self._kind(kind)),
            )
            row = await cur.fetchone()
            data: Any = None if row is None else row["data"]
            return [str(v) for v in data] if isinstance(data, list) else []

    async def campaigns_with(self, guild_id: int, kinds: Collection[str]) -> set[str]:
        """The campaigns of the server that have any of these kinds waiting."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT campaign_id FROM import_state"
                " WHERE guild_id = %s AND kind = ANY(%s)",
                (guild_id, [self._kind(k) for k in kinds]),
            )
            return {r["campaign_id"] for r in await cur.fetchall()}
