"""The Postgres NOTIFY that tells processes a campaign's memory changed.

Notifications skip row-level security, so the payload carries only the campaign ID,
the new version, and whether names changed: never names or facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps this module (and the lookup) free of the database driver
    from dmbot.db import Conn

CHANNEL = "dmbot_memory"
# Tables the in-memory lookup copies. Other changes (mentions, flags, rule terms) bump
# the version without making copies reload.
LOOKUP_TABLES = frozenset(
    {"memory_entities", "memory_aliases", "memory_relations", "memory_corrections"}
)


@dataclass(frozen=True, slots=True)
class MemoryChanged:
    campaign_id: str
    version: int
    names_changed: bool


def payload(campaign_id: str, version: int, names_changed: bool) -> str:
    return f"{campaign_id}:{version}:{int(names_changed)}"


def parse(raw: str) -> MemoryChanged | None:
    """A notification's payload, or None if it isn't one of ours."""
    parts = raw.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("0", "1"):
        return None
    return MemoryChanged(parts[0], int(parts[1]), parts[2] == "1")


async def send(conn: Conn, campaign_id: str, version: int, names_changed: bool) -> None:
    """Queue the notification; Postgres delivers it when the transaction commits."""
    await conn.execute(
        "SELECT pg_notify(%s, %s)", (CHANNEL, payload(campaign_id, version, names_changed))
    )
