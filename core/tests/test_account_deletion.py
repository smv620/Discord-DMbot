"""Deleting an account keeps the campaigns it owns (#1059; owner, 2026-10-11): they become
ownerless, nothing of anyone else's changes, and a DM can take one on, which stops the
120-day countdown. Needs Postgres."""

from __future__ import annotations

from dmbot import entitlements, plans
from dmbot.campaigns import CampaignStore
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB, CAL = 7001, 7002, 7003
GUILD_A, GUILD_B = 111, 222
NOW = 1_800_000_000


class AccountDeletion(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users((BOB,))  # BOB has room to take a campaign on
        self.store = CampaignStore(self.db, clock=lambda: NOW)
        for user in (ALICE, BOB):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        self.a1 = await self.store.create(GUILD_A, "Alice one", ALICE)
        self.a2 = await self.store.create(GUILD_B, "Alice two", ALICE)
        self.b1 = await self.store.create(GUILD_A, "Bob one", BOB)

    async def owned(self, user: int) -> int:
        async with self.db.user(user) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM owner_campaigns")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_the_campaigns_stay_ownerless_in_every_server_and_others_are_untouched(
        self,
    ) -> None:
        self.assertEqual(await self.owned(ALICE), 2)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for guild, campaign in ((GUILD_A, self.a1), (GUILD_B, self.a2)):
            kept = await self.store.get(guild, campaign.id)
            assert kept is not None  # still there: players can read and download it
            self.assertIsNone(kept.owner_user_id)
            self.assertEqual(kept.owner_left_at, NOW + 50)
        bobs = await self.store.get(GUILD_A, self.b1.id)
        assert bobs is not None
        self.assertEqual((bobs.owner_user_id, bobs.owner_left_at), (BOB, None))
        # The cap table follows (the trigger), so a deleted account holds no slots.
        self.assertEqual(await self.owned(ALICE), 0)
        self.assertEqual(await self.owned(BOB), 1)

    async def test_deleting_twice_or_with_no_campaigns_is_harmless(self) -> None:
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        await accounts.delete_person(self.db, ALICE, now=NOW + 99)  # nothing left to orphan
        kept = await self.store.get(GUILD_A, self.a1.id)
        assert kept is not None
        self.assertEqual(kept.owner_left_at, NOW + 5)  # the first time stands
        await accounts.delete_person(self.db, CAL, now=NOW)  # never had an account

    async def test_a_dm_takes_one_on_and_the_countdown_stops(self) -> None:
        await self.store.add_dm(GUILD_A, self.a1.id, BOB)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        taken = await self.store.take_ownership(GUILD_A, self.a1.id, BOB, NOW + 10)
        self.assertEqual(taken, "taken")
        got = await self.store.get(GUILD_A, self.a1.id)
        assert got is not None
        self.assertEqual((got.owner_user_id, got.owner_left_at), (BOB, None))
        self.assertEqual(await self.owned(BOB), 2)

    async def test_someone_who_is_not_one_of_its_dms_cannot_take_it_on(self) -> None:
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.store.take_ownership(GUILD_B, self.a2.id, CAL, NOW), "gone")

    async def test_the_countdown_is_120_days_from_the_deletion(self) -> None:
        self.assertEqual(plans.load().keep_after_plan_stops_paying.days, 120)
