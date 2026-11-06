"""Importance assessments in campaign backups (an `ExportSection`; #3172).

PlotBot's reading of a session cannot be made again by re-reading it (the lines are not in a
backup), so without this a restored campaign would drop an off-stage villain back to the floor.
A row is a rank and a fixed reason code, never a word: entry, rank, reason, source, session start,
and when it was written. Line numbers are left out of the file on purpose: a restored campaign has
none of those lines. Rows from the file are untrusted: the section is refused whole if any is
damaged, and a row for an entry that is not in the restored campaign is dropped (the memory
section loads first; a backup outlives its entries). Rows keep their order, so the newest
assessment stays the newest. Like the rest of a backup it holds the DM's secrets (a backup is for
the DM and warned about), and the rank is never read by a player-facing screen. An older backup
has no such section and restores at the floor.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.importance_rank import BASES, PLAYER, SOURCE_PLOTBOT, SOURCE_RULE
from dmbot.memory.models import is_id

DAMAGED = "This backup file is damaged (bad importance ranks)."
# One row for each time a character is reassessed, at about 120 bytes: far below the size limit.
ROWS_MAX = 50_000
INT64_MAX = 2**63 - 1
_KEYS = {"entity_id", "rank", "basis", "source", "session_started_at", "recorded_at"}

Row = tuple[str, int, str, str, int | None, int]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    for raw in rows:
        if not (
            isinstance(raw, dict)
            and set(raw) == _KEYS
            and is_id(raw["entity_id"])
            and _int(raw["rank"], 0, PLAYER)
            and raw["basis"] in BASES
            and raw["source"] in (SOURCE_PLOTBOT, SOURCE_RULE)
            and (raw["session_started_at"] is None or _int(raw["session_started_at"], 1, INT64_MAX))
            and _int(raw["recorded_at"], 1, INT64_MAX)
        ):
            raise CampaignError(DAMAGED)
        out.append(
            (
                raw["entity_id"], raw["rank"], raw["basis"], raw["source"],
                raw["session_started_at"], raw["recorded_at"],
            )
        )  # fmt: skip
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class ImportanceSection:
    name = "memory_importance"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT entity_id, rank, basis, source, session_started_at, recorded_at"
            " FROM memory_importance WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY seq DESC LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        # Past the cap the oldest rows go (the newest assessments are the ones that count), and
        # the file is oldest first, so loading it in order keeps the newest the newest.
        return [
            {
                "entity_id": r["entity_id"],
                "rank": int(r["rank"]),
                "basis": r["basis"],
                "source": r["source"],
                "session_started_at": None
                if r["session_started_at"] is None
                else int(r["session_started_at"]),
                "recorded_at": int(r["recorded_at"]),
            }
            for r in reversed(await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        cur = await conn.execute(
            "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND type = 'character' AND status IN ('proposed', 'confirmed')",
            (guild_id, campaign_id),
        )
        live = {str(r["id"]) for r in await cur.fetchall()}
        # One at a time and in file order: the database numbers them as they go in, so the
        # order of the history is the file's.
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, basis,"
                " source, session_started_at, lines, recorded_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, '{}', %s)",
                [
                    (guild_id, campaign_id, e, rank, basis, source, session, at)
                    for e, rank, basis, source, session, at in checked
                    if e in live
                ],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_importance WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "ImportanceSection"]
