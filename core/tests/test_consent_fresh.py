"""A worker reads consent again now and then (#4904 3b-4c): it is told of no stop made in the bot
or on the website, so a person who stops must drop out of its next read. Needs Postgres."""

from __future__ import annotations

from dmbot.consent import ConsentStore
from tests.pg import DatabaseTest

GUILD, MIA = 7401, 9401


class TheWorkersCache(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1000.0
        self.worker = ConsentStore(self.db, clock=lambda: self.now, max_age_s=10.0)
        self.bot = ConsentStore(self.db)  # (another process, which takes the stop)
        await self.bot.grant(GUILD, MIA)

    async def test_a_stop_made_elsewhere_reaches_the_workers_next_read(self) -> None:
        self.assertEqual(await self.worker.consenting(GUILD), {MIA})
        await self.bot.revoke(GUILD, MIA)
        self.now += 5  # (inside the window: the cached read, as the age allows)
        self.assertEqual(await self.worker.consenting(GUILD), {MIA})
        self.now += 20  # (past it: read again)
        self.assertEqual(await self.worker.consenting(GUILD), frozenset())

    async def test_an_imports_readers_see_the_stop_too(self) -> None:
        campaign_id = "c" * 32
        self.assertIn(MIA, await self.worker.recorded_in(GUILD, campaign_id))
        await self.bot.revoke(GUILD, MIA)
        self.now += 20
        self.assertNotIn(MIA, await self.worker.recorded_in(GUILD, campaign_id))

    async def test_the_bot_keeps_its_cache_for_ever_as_before(self) -> None:
        mine = ConsentStore(self.db, clock=lambda: self.now)  # (no max age)
        self.assertEqual(await mine.consenting(GUILD), {MIA})
        await self.bot.revoke(GUILD, MIA)
        self.now += 10_000
        self.assertEqual(await mine.consenting(GUILD), {MIA})  # (its own revokes and the site's
        # follower keep it right; nothing here reads again)
