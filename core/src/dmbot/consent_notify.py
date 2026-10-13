"""The Postgres NOTIFY that tells the bot a person stopped being recorded on the website (#1151).

The bot checks consent from a cache, so a stop saved only in the database wouldn't reach a
running session. Notifications skip row-level security, so the payload carries only the server
and person ids: never names. It is only a hint about which server to check again: any database
role can send one, so the bot never trusts who it names. It reads the server's consent from the
database and stops whoever it still holds but the database no longer lists, as its own Stop
button does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps this module free of the database driver
    from dmbot.db import Conn

CHANNEL = "dmbot_consent_stop"


def payload(guild_id: int, user_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}"


def parse(raw: str) -> tuple[int, int] | None:
    """(server ID, person ID) from a notification, or None if it isn't one of ours."""
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() and len(p) <= 20 for p in parts):
        return None
    guild_id, user_id = int(parts[0]), int(parts[1])
    if not (0 < guild_id < 2**63 and 0 < user_id < 2**63):
        return None
    return guild_id, user_id


async def send(conn: Conn, guild_id: int, user_id: int) -> None:
    """Queue the notification; Postgres delivers it when the transaction commits."""
    await conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, payload(guild_id, user_id)))
