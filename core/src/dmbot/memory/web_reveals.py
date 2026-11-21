"""What the characters learned, in words, kept for one DM's page on the website (#3863 step 2).

The words (who learned what, and how) need the knowledge gate and the consent re-check, which only
the bot has (`RevealProposals.waiting` and `describe`), so the bot works them out when asked
(`reveals-ask` on the queue) and keeps them here (`web_reveals`, migration 0586); the page reads
them through the DM-only door `dmbot_web_reveals`. One set for each (campaign, DM): a new ask
replaces it, so asking again, as the page does before every read, never adds a second row for a
proposal. The rows hold campaign words the DM may read on the card, with forced row-level security,
and are swept after an hour. Never logged."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from dmbot.db import Database
from dmbot.memory.reveal_proposals import Shown

LIFE_S = 60 * 60  # the page asks again before it reads; a set older than an hour is not wanted


class RevealsStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def replace(
        self, guild_id: int, campaign_id: str, user_id: int, shown: Sequence[tuple[str, Shown]]
    ) -> int:
        """This DM's set for the campaign becomes exactly `shown` (proposal id, words), in this
        order; returns how many are kept. One transaction: the page never reads half a set."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM web_reveals WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
                (guild_id, campaign_id, user_id),
            )
            now = int(self._now())
            for position, (proposal_id, words) in enumerate(shown):
                await conn.execute(
                    "INSERT INTO web_reveals (guild_id, campaign_id, user_id, proposal_id,"
                    " position, who, what, how, player, guessing, asked_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, campaign_id, user_id, proposal_id, position,
                        words.who[:300], words.what[:600], words.how[:300],
                        words.player, words.guessing, now,
                    ),
                )  # fmt: skip
        return len(shown)

    async def drop(self, guild_id: int, campaign_id: str, proposal_id: str) -> None:
        """A proposal that was answered leaves every DM's set (its page row goes at once)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM web_reveals WHERE guild_id = %s AND campaign_id = %s"
                " AND proposal_id = %s",
                (guild_id, campaign_id, proposal_id),
            )

    async def sweep(self, guild_id: int) -> int:
        """Sets older than an hour; how many rows (counts only in the log)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_reveals WHERE guild_id = %s AND asked_at < %s",
                (guild_id, int(self._now()) - LIFE_S),
            )
            return int(cur.rowcount)
