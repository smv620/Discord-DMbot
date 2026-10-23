"""What the bot does after the website deleted a campaign (#2554, part 1b).

The website cannot tidy Discord or message anyone, and by the time the bot hears of the delete
the campaign's row (with its channel IDs) is gone. So the website's delete door leaves a note
(`web_delete_notices`): the campaign's name, who deleted it, the other game masters and the
channel IDs. The bot takes each note once (deleting it) and does what Discord's own delete does:
tidies the channels (`retire_campaign_channels`, which also clears the bot's memory of the
campaign) and tells each other game master. A server the bot cannot reach is still handled: its
channels are skipped and the game masters are still told, where Discord lets the bot message them.

Nothing of the story is in a note, and its words are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.db import Database

KEEP_UNTAKEN_S = 7 * 86_400  # a note the bot never took (it was down) is dropped after a week
PER_ROUND = 10  # notes taken in one round for one server


@dataclass(frozen=True, slots=True)
class Notice:
    """A deleted campaign, as `HasChannels` (`retention_channels`) reads it."""

    guild_id: int
    id: str  # the deleted campaign's ID
    name: str
    by_user_id: int
    other_dms: tuple[int, ...]
    dm_screen_channel_id: int | None
    transcript_channel_id: int | None
    gametime_channel_id: int | None
    dmbchat_channel_id: int | None
    dm_bot_channel_id: int | None
    rules_channel_id: int | None
    npcs_channel_id: int | None


class WebDeleteNotices:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def take(self, guild_id: int) -> list[Notice]:
        """The waiting notes of one server, deleted as they are taken: each is acted on once, even
        if two rounds overlap. If the bot stops before acting, the note is lost, which costs a
        channel left in place and a message not sent, never a duplicate."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_delete_notices WHERE (guild_id, id) IN ("
                " SELECT guild_id, id FROM web_delete_notices WHERE guild_id = %s"
                " ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED)"
                " RETURNING gone_id, campaign_name, by_user_id, other_dms, dm_screen_channel_id,"
                " transcript_channel_id, gametime_channel_id, dmbchat_channel_id,"
                " dm_bot_channel_id, rules_channel_id, npcs_channel_id, created_at",
                (guild_id, PER_ROUND),
            )
            rows = sorted(await cur.fetchall(), key=lambda r: r["created_at"])
        return [
            Notice(
                guild_id,
                str(r["gone_id"]),
                str(r["campaign_name"]),
                int(r["by_user_id"]),
                tuple(int(u) for u in r["other_dms"]),
                r["dm_screen_channel_id"],
                r["transcript_channel_id"],
                r["gametime_channel_id"],
                r["dmbchat_channel_id"],
                r["dm_bot_channel_id"],
                r["rules_channel_id"],
                r["npcs_channel_id"],
            )
            for r in rows
        ]

    async def sweep(self, guild_id: int) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_delete_notices WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_UNTAKEN_S),
            )
            return int(cur.rowcount)
