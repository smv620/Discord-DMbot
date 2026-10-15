"""Experience points (#1228): an account's all-time minutes, read through the one door the
website's role may use. Each person sees only their own, whatever the code asks."""

from __future__ import annotations

from dmbot import usage
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_role import ensure_web_role, web_url

ANN, BEN, NOBODY = 111, 222, 333
NOW = 1_800_000_000


class ExperiencePoints(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        campaigns = CampaignStore(self.db)
        for uid, guild, minutes in ((ANN, 1001, 30), (BEN, 2002, 50)):
            c = await campaigns.create(guild, f"Campaign {uid}", uid)
            # The same month for both, in two servers, plus a later month for Ann.
            await usage.add_minutes(
                self.db, guild_id=guild, campaign_id=c.id, owner_user_id=uid,
                session_started_at=NOW, minutes=minutes, now=NOW + 600,
            )  # fmt: skip
            if uid == ANN:
                await usage.add_minutes(
                    self.db, guild_id=guild, campaign_id=c.id, owner_user_id=uid,
                    session_started_at=NOW + 90 * 86400, minutes=12, now=NOW + 90 * 86400 + 600,
                )  # fmt: skip

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def test_each_account_sees_only_its_own_minutes_as_the_website(self) -> None:
        self.assertEqual(await usage.total_minutes(self.web, ANN), 42)
        self.assertEqual(await usage.total_minutes(self.web, BEN), 50)
        self.assertEqual(await usage.total_minutes(self.web, NOBODY), 0)

    async def test_the_door_takes_no_person_so_it_cannot_be_pointed_at_another(self) -> None:
        async with self.web.user(ANN) as conn:  # signed in as Ann, asking for Ben's rows
            cur = await conn.execute("SELECT dmbot_minutes_total() AS n")
            row = await cur.fetchone()
            self.assertEqual((row or {})["n"], 42)
