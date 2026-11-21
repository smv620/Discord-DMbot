"""The outbox: what a worker wants said in Discord, kept as a row the bot delivers (#3843).

Only the bot holds the Discord connection, so a worker never posts; it writes a row here, in the
same transaction as the work that made it (`add` takes the caller's connection), and the bot claims
it, says it and marks it sent only after Discord accepted it. The payload holds ids and counts only,
never a name, a transcript line or a document's words: `clean_payload` refuses sentences, text with
spaces, and anything that is not a number, a flag or one short word, and the producers send only
ids (a single word such as a name would pass, so a producer must never pass one), so the bot builds
the words itself when it delivers (a secret stays as secret as the bot's own reads
make it). A claim is a lease: a row left `sending` by a bot that died is claimed again after
`STALE_S`, and two bots never claim one row (`FOR UPDATE SKIP LOCKED`). A failed delivery is retried
up to `MAX_TRIES` times, then ends `failed` with a short fixed reason. Rows older than `KEEP_S` go.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.db import Conn, Database

CHANNEL = "dmbot_outbox"  # NOTIFY carries the server's id only (notifications skip row security)
RECAP_READY = "recap_ready"  # a recap is ready: the bot posts it, with its buttons, for the DM
RECAP_FAILED = "recap_failed"  # a recap could not be written (payload: session_at, keeps)
RECAP_NAMES = "recap_names"  # names the recap kept were added to the check (session_at, added)
RECAP_UNCHECKED = "recap_unchecked"  # a recap could not be finished checking (session_at, keeps)
RECAP_ALLOWANCE = "recap_allowance"  # the month's allowance is used up (nothing but the kind)

MAX_TRIES = 5
STALE_S = 5 * 60  # a delivery that has been `sending` this long was a bot that died
KEEP_S = 3 * 24 * 3600  # finished rows are swept after a few days
_WORD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # an id or a fixed word, never a sentence
_KIND = re.compile(r"^[a-z_]{1,40}$")
MAX_ERROR = 40


class NotAnId(ValueError):
    """A payload value was not an id, a count or a fixed word."""


def clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The payload as it is stored: keys are fixed words, values are whole numbers, booleans or
    ids (letters, digits, '_' and '-', at most 64 characters), so no session word rides in it."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if not _WORD.match(str(key)):
            raise NotAnId("a key")
        if isinstance(value, bool | int) or (isinstance(value, str) and _WORD.match(value)):
            out[str(key)] = value
        else:
            raise NotAnId(str(key))
    return out


@dataclass(frozen=True, slots=True)
class Row:
    id: int
    guild_id: int
    campaign_id: str
    kind: str
    payload: dict[str, Any]
    tries: int


class Outbox:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    def listen(self, channel: str) -> AsyncGenerator[str, None]:
        """The bot's wake-ups (`Database.listen`): the server's id, never a word."""
        return self._db.listen(channel)

    async def add(
        self, conn: Conn, guild_id: int, campaign_id: str, kind: str, payload: Mapping[str, Any]
    ) -> int:
        """Write a row in the caller's transaction (so it exists exactly when the work's result
        does); the insert notifies the bot. Returns the row's id."""
        if not _KIND.match(kind):
            raise NotAnId("kind")
        cur = await conn.execute(
            "INSERT INTO outbox (guild_id, campaign_id, kind, payload, created_at)"
            " VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id",
            (guild_id, campaign_id, kind, json.dumps(clean_payload(payload)), int(self._now())),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row["id"])

    async def add_now(
        self, guild_id: int, campaign_id: str, kind: str, payload: Mapping[str, Any]
    ) -> int:
        """`add` in a transaction of its own, for a producer with no transaction of its work to
        join (the result is already saved)."""
        async with self._db.guild(guild_id) as conn:
            return await self.add(conn, guild_id, campaign_id, kind, payload)

    async def claim(self, guild_id: int, limit: int = 10) -> list[Row]:
        """The oldest rows that wait, or whose delivery went stale, marked `sending` for this bot.
        Each try is counted when it is claimed, so a bot that dies on a row cannot loop forever."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            # A delivery that died on its last try is given up on, never claimed again: a row that
            # crashes or hangs the process cannot be tried for ever.
            await conn.execute(
                "UPDATE outbox SET state = 'failed', claimed_at = NULL, error = 'gave-up'"
                " WHERE guild_id = %s AND state = 'sending' AND claimed_at <= %s AND tries >= %s",
                (guild_id, now - STALE_S, MAX_TRIES),
            )
            cur = await conn.execute(
                "UPDATE outbox SET state = 'sending', tries = tries + 1, claimed_at = %s"
                " WHERE (guild_id, id) IN (SELECT guild_id, id FROM outbox WHERE guild_id = %s"
                "   AND (state = 'waiting' OR (state = 'sending' AND claimed_at <= %s))"
                "   ORDER BY created_at, id LIMIT %s FOR UPDATE SKIP LOCKED)"
                " RETURNING id, guild_id, campaign_id, kind, payload, tries",
                (now, guild_id, now - STALE_S, limit),
            )
            rows = await cur.fetchall()
        return sorted(
            (
                Row(int(r["id"]), int(r["guild_id"]), r["campaign_id"], r["kind"],
                    dict(r["payload"]), int(r["tries"]))
                for r in rows
            ),
            key=lambda r: r.id,
        )  # fmt: skip

    async def sent(self, guild_id: int, row_id: int) -> None:
        """Discord accepted it."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE outbox SET state = 'sent', claimed_at = NULL"
                " WHERE guild_id = %s AND id = %s AND state = 'sending'",
                (guild_id, row_id),
            )

    async def failed(self, guild_id: int, row_id: int, why: str, *, final: bool = False) -> None:
        """Delivery failed: try again, or after `MAX_TRIES` (at once when `final`) end it with the
        short reason."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE outbox SET state = CASE WHEN %s OR tries >= %s THEN 'failed'"
                " ELSE 'waiting' END, claimed_at = NULL, error = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'sending'",
                (final, MAX_TRIES, why[:MAX_ERROR], guild_id, row_id),
            )

    async def sweep(self, guild_id: int) -> int:
        """Finished rows older than a few days."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM outbox WHERE guild_id = %s AND state IN ('sent', 'failed')"
                " AND created_at <= %s",
                (guild_id, int(self._now()) - KEEP_S),
            )
            return int(cur.rowcount)
