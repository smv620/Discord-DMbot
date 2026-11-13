"""Where the story is, in campaign backups (an `ExportSection`; #4173 slice 4, Supervisor's ruling:
"in the backup").

A row is a chain's key, a place's rank on it and the place's entry id: ids only, never a word, and
the places themselves are in the same backup's memory section (restored first, so the foreign key
holds). Without this a restored campaign would start its next session not knowing where the story
was. Rows from the file are untrusted: the section is refused whole if any is damaged. An older
backup has no such section and restores with no chain: the next session starts afresh.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

DAMAGED = "This backup file is damaged (bad places for where the story is)."
ROWS_MAX = 200  # far above any campaign: a few chains of at most 21 places
INT64_MAX = 2**63 - 1
_KEYS = {"chain", "rank", "entity_id", "updated_at"}
_CHAIN = re.compile(r"^(main|mark|s[0-9]{1,12}g[1-3])$")  # (the table's own check)
_ID = re.compile(r"^[0-9a-f]{32}$")

Row = tuple[str, int, str | None, int]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    seen: set[tuple[str, int]] = set()
    for raw in rows:
        if not (
            isinstance(raw, dict)
            and set(raw) == _KEYS
            and isinstance(raw["chain"], str)
            and _CHAIN.match(raw["chain"])
            and _int(raw["rank"], 0, 20)
            # (the one 'mark' row has no place; every other row is a place)
            and (
                raw["entity_id"] is None
                if raw["chain"] == "mark"
                else isinstance(raw["entity_id"], str) and bool(_ID.match(raw["entity_id"]))
            )
            and (raw["chain"] != "mark" or raw["rank"] == 0)
            and _int(raw["updated_at"], 1, INT64_MAX)
            and (raw["chain"], raw["rank"]) not in seen
        ):
            raise CampaignError(DAMAGED)
        seen.add((raw["chain"], raw["rank"]))
        out.append((raw["chain"], raw["rank"], raw["entity_id"], raw["updated_at"]))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class StoryChainSection:
    name = "memory_story_chain"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT chain, rank, entity_id, updated_at FROM memory_story_chain"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY chain, rank LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "chain": r["chain"],
                "rank": int(r["rank"]),
                "entity_id": r["entity_id"],
                "updated_at": int(r["updated_at"]),
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
            # (a place the memory section did not restore is left out, never an error: the chain
            # simply starts without it)
            await cursor.executemany(
                "INSERT INTO memory_story_chain (guild_id, campaign_id, chain, rank, entity_id,"
                " updated_at) SELECT %s, %s, %s, %s, %s, %s WHERE %s::text IS NULL OR EXISTS"
                " (SELECT 1 FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s)",
                [
                    (guild_id, campaign_id, c, r, e, at, e, guild_id, campaign_id, e)
                    for c, r, e, at in checked
                ],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_story_chain WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "StoryChainSection"]
