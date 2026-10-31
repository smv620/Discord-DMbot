"""The game minute to stamp on a live belief (#3091, part 3): none without a clock, the clock's
minute with one, the picked group's while split. Needs the test database."""

from __future__ import annotations

from dmbot.timebot.clock import Clock
from dmbot.timebot.stamp import live_minute
from dmbot.timebot.store import ClockStore
from tests.test_memory_store import GUILD_A, MemoryTest


class Stamping(MemoryTest):
    async def test_no_clock_means_no_minute(self) -> None:
        self.assertIsNone(await live_minute(ClockStore(self.db), GUILD_A, self.c))

    async def test_a_clock_gives_its_minute(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO game_clocks"
                " (guild_id, campaign_id, minute, last_long_rest, updated_at)"
                " VALUES (%s, %s, %s, 0, 1)",
                (GUILD_A, self.c, 2000),
            )
        self.assertEqual(await live_minute(ClockStore(self.db), GUILD_A, self.c), 2000)
        self.assertEqual(Clock(2000, 0).minute, 2000)
