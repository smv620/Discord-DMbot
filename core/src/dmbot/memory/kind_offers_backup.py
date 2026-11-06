"""The DM's answers to "make these words a kind of connection?" in campaign backups (an
`ExportSection`; #3033).

A row is the folded words of a catch-all connection, "later" or "never", and the count seen: the
words already sit in the same backup on the connections themselves, and nothing else is stored.
Without this a restored campaign would ask again about words the DM said Never to. Rows from the
file are untrusted: the section is refused whole if any is damaged. A row whose words appear on no
connection of the restored campaign is dropped when the next sweep runs (`kind_offers.sweep`), so
a backup outlives its words without keeping them. An older backup has no such section and restores
with no answers.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.kind_offers import LATER, NEVER

DAMAGED = "This backup file is damaged (bad answers about kinds of connection)."
ROWS_MAX = 5_000  # far above any campaign: one answer per set of words
INT64_MAX = 2**63 - 1
_KEYS = {"words_key", "state", "seen", "created_at"}

Row = tuple[str, str, int, int]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    seen: set[str] = set()
    for raw in rows:
        if not (
            isinstance(raw, dict)
            and set(raw) == _KEYS
            and isinstance(raw["words_key"], str)
            and 1 <= len(raw["words_key"]) <= 300
            and raw["words_key"] not in seen
            and raw["state"] in (LATER, NEVER)
            and _int(raw["seen"], 0, 1_000_000)
            and _int(raw["created_at"], 1, INT64_MAX)
        ):
            raise CampaignError(DAMAGED)
        seen.add(raw["words_key"])
        out.append((raw["words_key"], raw["state"], raw["seen"], raw["created_at"]))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class KindOffersSection:
    name = "memory_kind_offers"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT words_key, state, seen, created_at FROM memory_kind_offers"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY words_key LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "words_key": r["words_key"],
                "state": r["state"],
                "seen": int(r["seen"]),
                "created_at": int(r["created_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_kind_offers (guild_id, campaign_id, words_key, state, seen,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                [(guild_id, campaign_id, k, st, n, at) for k, st, n, at in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_kind_offers WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "KindOffersSection"]
