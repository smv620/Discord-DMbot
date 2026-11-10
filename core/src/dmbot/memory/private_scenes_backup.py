"""Private scenes in campaign backups (an `ExportSection`; #2996 rule 5, as the reveals' are).

A backup holds the whole campaign, so a restored one keeps both the scenes found and the DM's "Not
private" marks (which a later run must not undo). A row is ids and times only: the two player
characters, the session's start (a restore makes the session again under a new id, so the start is
the key), the first and last line's start, how many lines, who spoke, and the DM's mark. Rows from
the file are untrusted: the section is refused whole if any is damaged. A kept row whose speaker is
not recorded in the server it is restored into is left out of every read (`PrivateScenes.listed`
reads only rows whose speakers are all recorded). An older backup has no such section and restores
with none.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.models import is_id

DAMAGED = "This backup file is damaged (bad private scenes)."
ROWS_MAX = 5_000
INT64_MAX = 2**63 - 1
STATUSES = ("kept", "not_private")
_KEYS = {
    "id", "first_id", "second_id", "session_started_at", "from_ms", "to_ms", "line_count",
    "speaker_ids", "status", "created_at", "decided_at",
}  # fmt: skip
_ORDER = sorted(_KEYS)

Row = tuple[Any, ...]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape (the table's own
    rules, checked before the insert so a bad file is refused whole). Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    ids: set[str] = set()
    once: set[tuple[Any, ...]] = set()  # (the table's unique index)
    for r in rows:
        if not (
            isinstance(r, dict)
            and set(r) == _KEYS
            and is_id(r["id"])
            and r["id"] not in ids
            and is_id(r["first_id"])
            and is_id(r["second_id"])
            and r["first_id"] < r["second_id"]
            and _int(r["session_started_at"], 1, INT64_MAX)
            and _int(r["from_ms"], 0, INT64_MAX)
            and _int(r["to_ms"], r["from_ms"], INT64_MAX)
            and _int(r["line_count"], 2, 2**31 - 1)
            and isinstance(r["speaker_ids"], list)
            and len(r["speaker_ids"]) <= 100
            and all(_int(u, 1, INT64_MAX) for u in r["speaker_ids"])
            and r["status"] in STATUSES
            and (r["status"] == "not_private" or len(r["speaker_ids"]) >= 2)
            and _int(r["created_at"], 1, INT64_MAX)
            and (
                _int(r["decided_at"], 1, INT64_MAX)
                if r["status"] == "not_private"
                else r["decided_at"] is None
            )
        ):
            raise CampaignError(DAMAGED)
        key = (r["session_started_at"], r["first_id"], r["second_id"], r["from_ms"])
        if key in once:
            raise CampaignError(DAMAGED)
        once.add(key)
        ids.add(r["id"])
        out.append(tuple(r[k] for k in _ORDER))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class PrivateScenesSection:
    name = "memory_private_scenes"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, first_id, second_id, session_started_at, from_ms, to_ms, line_count,"
            " speaker_ids, status, created_at, decided_at FROM memory_private_scenes"
            # (past the cap, the DM's marks first, then the newest)
            " WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY status = 'not_private' DESC, session_started_at DESC, from_ms, id LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "id": r["id"], "first_id": r["first_id"], "second_id": r["second_id"],
                "session_started_at": int(r["session_started_at"]),
                "from_ms": int(r["from_ms"]), "to_ms": int(r["to_ms"]),
                "line_count": int(r["line_count"]),
                "speaker_ids": [int(u) for u in r["speaker_ids"]], "status": r["status"],
                "created_at": int(r["created_at"]),
                "decided_at": None if r["decided_at"] is None else int(r["decided_at"]),
            }
            for r in await cur.fetchall()
        ]  # fmt: skip

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        at = {k: i for i, k in enumerate(_ORDER)}
        columns = ", ".join(_ORDER)
        async with conn.cursor() as cursor:
            await cursor.executemany(
                f"INSERT INTO memory_private_scenes (guild_id, campaign_id, {columns})"
                f" VALUES (%s, %s, {', '.join(['%s'] * len(_ORDER))})",
                [(guild_id, campaign_id, *(r[at[k]] for k in _ORDER)) for r in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_private_scenes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "PrivateScenesSection"]
