"""The Postgres NOTIFY that tells processes a campaign's memory changed.

Notifications skip row-level security, so the payload carries only the campaign ID,
the new version, and whether names changed: never names or facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.memory.models import is_id

if TYPE_CHECKING:  # keeps this module (and the lookup) free of the database driver
    from dmbot.db import Conn

CHANNEL = "dmbot_memory"
# Tables the in-memory lookup copies. Other changes (mentions, flags, rule terms) bump
# the version without making copies reload.
LOOKUP_TABLES = frozenset(
    {"memory_entities", "memory_aliases", "memory_relations", "memory_corrections"}
)


# The campaign's own kinds (a label such as "Guild" for "group"): the NPC list shows them (#1792).
KIND_TABLES = frozenset({"memory_types"})


@dataclass(frozen=True, slots=True)
class MemoryChanged:
    campaign_id: str
    version: int
    names_changed: bool
    kinds_changed: bool = False


def payload(
    campaign_id: str, version: int, names_changed: bool, kinds_changed: bool = False
) -> str:
    """The flag is a small number: 1 names changed, 2 kinds changed, 3 both. A sender that
    doesn't know about kinds (an older one) sends 0 or 1, which still reads right."""
    return f"{campaign_id}:{version}:{int(names_changed) + 2 * int(kinds_changed)}"


def parse(raw: str) -> MemoryChanged | None:
    """A notification's payload, or None if it isn't one of ours."""
    parts = raw.split(":")
    if (
        len(parts) != 3
        or not is_id(parts[0])
        or not (parts[1].isascii() and parts[1].isdigit())
        or parts[2] not in ("0", "1", "2", "3")
    ):
        return None
    flag = int(parts[2])
    return MemoryChanged(parts[0], int(parts[1]), bool(flag & 1), bool(flag & 2))


async def send(
    conn: Conn,
    campaign_id: str,
    version: int,
    names_changed: bool,
    kinds_changed: bool = False,
) -> None:
    """Queue the notification; Postgres delivers it when the transaction commits."""
    await conn.execute(
        "SELECT pg_notify(%s, %s)",
        (CHANNEL, payload(campaign_id, version, names_changed, kinds_changed)),
    )
