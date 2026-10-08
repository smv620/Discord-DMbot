"""The Postgres NOTIFY that tells the bot an offer made on the website needs its private
message (#690).

Notifications skip row-level security, so the payload carries only the server and offer
IDs: never names. The bot reads the offer itself, scoped to that server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps this module free of the database driver
    from dmbot.db import Conn

CHANNEL = "dmbot_handover_offers"


def payload(guild_id: int, offer_id: int) -> str:
    return f"{int(guild_id)}:{int(offer_id)}"


def parse(raw: str) -> tuple[int, int] | None:
    """(server ID, offer ID) from a notification, or None if it isn't one of ours."""
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() and len(p) <= 20 for p in parts):
        return None
    guild_id, offer_id = int(parts[0]), int(parts[1])
    if not (0 < guild_id < 2**63 and 0 < offer_id < 2**63):
        return None
    return guild_id, offer_id


async def send(conn: Conn, guild_id: int, offer_id: int) -> None:
    """Queue the notification; Postgres delivers it when the transaction commits."""
    await conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, payload(guild_id, offer_id)))
