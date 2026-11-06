"""The DM's and players' icon choices for player characters in campaign backups (an
`ExportSection`; #3621).

A row is a choice and nothing else: the entry's id, `discord`, `dndbeyond`, `hidden` or `custom`
(with the id of a campaign picture), and when and by whom it was set. The avatar hash and the
portrait link are not carried (they are read again), so a restored campaign never holds a stale
picture; a choice whose picture is not there reads as none. Rows from the file are untrusted: the
section is refused whole if any is damaged. A choice for an entry the restored campaign does not
have is dropped. An older backup has no such section and restores with the default for everyone.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

DAMAGED = "This backup file is damaged (bad icon choices)."
ROWS_MAX = 5_000
INT64_MAX = 2**63 - 1
_KEYS = {"entity_id", "choice", "custom_ref", "set_by", "updated_at"}
_HEX = re.compile(r"[0-9a-f]{32}")
CHOICES = ("discord", "dndbeyond", "custom", "hidden")

Row = tuple[str, str, str | None, int, int]


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
            and isinstance(raw["entity_id"], str)
            and _HEX.fullmatch(raw["entity_id"])
            and raw["entity_id"] not in seen
            and raw["choice"] in CHOICES
            and (raw["custom_ref"] is None or _is_hex(raw["custom_ref"]))
            and ((raw["choice"] == "custom") == (raw["custom_ref"] is not None))
            and _int(raw["set_by"], 1, INT64_MAX)
            and _int(raw["updated_at"], 1, INT64_MAX)
        ):
            raise CampaignError(DAMAGED)
        seen.add(raw["entity_id"])
        out.append(
            (raw["entity_id"], raw["choice"], raw["custom_ref"], raw["set_by"], raw["updated_at"])
        )
    return out


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class PcIconsSection:
    name = "pc_icons"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT entity_id, choice, custom_ref, set_by, updated_at FROM pc_icons"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY entity_id LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "entity_id": r["entity_id"],
                "choice": r["choice"],
                "custom_ref": r["custom_ref"],
                "set_by": int(r["set_by"]),
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
            # (only for an entry the restored campaign has: a choice cannot outlive its character)
            await cursor.executemany(
                "INSERT INTO pc_icons (guild_id, campaign_id, entity_id, choice, custom_ref,"
                " set_by, updated_at) SELECT %s, %s, %s, %s, %s, %s, %s WHERE EXISTS"
                " (SELECT 1 FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s)",
                [
                    (guild_id, campaign_id, e, ch, ref, by, at, guild_id, campaign_id, e)
                    for e, ch, ref, by, at in checked
                ],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM pc_icons WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "PcIconsSection"]
