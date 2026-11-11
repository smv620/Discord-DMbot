"""Deleting an account keeps the campaigns it owns (#1059; owner, 2026-10-11): they become
ownerless, nothing of anyone else's changes, and a DM can take one on, which stops the
120-day countdown. Needs Postgres."""

from __future__ import annotations

from dmbot import entitlements, plans, usage
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

    async def test_the_discord_id_goes_out_of_the_ai_credits_and_the_ledger_keeps_its_numbers(
        self,
    ) -> None:
        entitlements.configure_free_users(())  # (so the credits are added to the account's month)
        for owner, campaign, guild, spend in (
            (ALICE, self.a1, GUILD_A, "a" * 32),
            (ALICE, self.a2, GUILD_B, "b" * 32),
            (BOB, self.b1, GUILD_A, "c" * 32),
        ):
            await usage.add_credits(
                self.db, guild_id=guild, campaign_id=campaign.id, owner_user_id=owner,
                spend_id=spend, job_kind="lore", feature="names", credits=10,
                now_ms=NOW * 1000,
            )  # fmt: skip
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for person, want in ((ALICE, 0), (BOB, 10)):
            async with self.db.user(person) as conn:
                cur = await conn.execute("SELECT COALESCE(sum(credits), 0) AS n FROM owner_credits")
                row = await cur.fetchone()
            assert row is not None
            self.assertEqual(int(row["n"]), want, person)  # (Bob's month is untouched)
        for guild in (GUILD_A, GUILD_B):
            async with self.db.guild(guild) as conn:
                cur = await conn.execute(
                    "SELECT owner_user_id, credits FROM ai_credit_spend ORDER BY owner_user_id"
                )
                rows = [(r["owner_user_id"], r["credits"]) for r in await cur.fetchall()]
            # (Alice's rows are kept, for what the campaign cost, with nobody as owner)
            self.assertEqual(rows, [(0, 10), (BOB, 10)] if guild == GUILD_A else [(0, 10)])

    async def add_hours(
        self, user: int, guild: int, campaign: str, started: int, minutes: int
    ) -> None:
        await usage.add_minutes(
            self.db, guild_id=guild, campaign_id=campaign, owner_user_id=user,
            session_started_at=started, minutes=minutes, now=NOW,
        )  # fmt: skip

    async def test_the_discord_id_goes_out_of_the_hours_and_the_minutes_stay_with_nobody(
        self,
    ) -> None:
        entitlements.configure_free_users(())
        await self.add_hours(ALICE, GUILD_A, self.a1.id, 100, 30)
        await self.add_hours(ALICE, GUILD_B, self.a2.id, 200, 45)
        await self.add_hours(BOB, GUILD_A, self.b1.id, 300, 12)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for person, want in ((ALICE, 0), (BOB, 12)):
            async with self.db.user(person) as conn:
                cur = await conn.execute("SELECT COALESCE(sum(minutes), 0) AS n FROM owner_hours")
                row = await cur.fetchone()
            assert row is not None
            self.assertEqual(int(row["n"]), want, person)  # (Bob's hours are untouched)
        for guild, want_rows in (
            (GUILD_A, [(0, 30), (BOB, 12)]),
            (GUILD_B, [(0, 45)]),
        ):
            async with self.db.guild(guild) as conn:
                cur = await conn.execute(
                    "SELECT owner_user_id, minutes FROM session_usage ORDER BY owner_user_id"
                )
                rows = [(r["owner_user_id"], r["minutes"]) for r in await cur.fetchall()]
            # (the campaign keeps its minutes with nobody as owner; no other server's rows show)
            self.assertEqual(rows, want_rows, guild)

    async def test_minutes_for_a_session_that_already_has_a_nobody_row_are_added_to_it(
        self,
    ) -> None:
        entitlements.configure_free_users(())
        async with self.db.guild(GUILD_A) as conn:  # (minutes from before anyone owned it)
            await conn.execute(
                "INSERT INTO session_usage (guild_id, campaign_id, session_started_at,"
                " owner_user_id, minutes, updated_at) VALUES (%s, %s, 100, 0, 5, %s)",
                (GUILD_A, self.a1.id, NOW),
            )
        await self.add_hours(ALICE, GUILD_A, self.a1.id, 100, 30)  # (the same session, later)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT owner_user_id, minutes FROM session_usage WHERE campaign_id = %s",
                (self.a1.id,),
            )
            rows = [(r["owner_user_id"], r["minutes"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [(0, 35)])

    async def test_a_delete_of_hours_outside_the_forget_function_removes_nothing(self) -> None:
        entitlements.configure_free_users(())
        await self.add_hours(ALICE, GUILD_A, self.a1.id, 100, 30)
        async with self.db.user(ALICE) as conn:
            cur = await conn.execute("DELETE FROM owner_hours WHERE owner_user_id = %s", (ALICE,))
            self.assertEqual(cur.rowcount, 0)  # (only the forget function may delete them)

    async def test_a_delete_outside_the_forget_function_removes_nothing(self) -> None:
        entitlements.configure_free_users(())
        await usage.add_credits(
            self.db, guild_id=GUILD_A, campaign_id=self.a1.id, owner_user_id=ALICE,
            spend_id="d" * 32, job_kind="lore", feature="names", credits=10, now_ms=NOW * 1000,
        )  # fmt: skip
        async with self.db.user(ALICE) as conn:
            cur = await conn.execute("DELETE FROM owner_credits WHERE owner_user_id = %s", (ALICE,))
            self.assertEqual(cur.rowcount, 0)  # (only the forget function may delete them)
            cur = await conn.execute("SELECT credits FROM owner_credits")
            self.assertEqual([r["credits"] for r in await cur.fetchall()], [10])

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

    async def test_deleting_removes_the_sent_email_records_and_keeps_the_unsubscribe_list(
        self,
    ) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO sent_emails (user_id, kind, sent_at) VALUES"
                " (%s, 'trial-end:1', 1), (%s, 'dm:trial-end:1', 1), (%s, 'trial-end:1', 1)",
                (ALICE, ALICE, BOB),
            )
            await conn.execute(
                "INSERT INTO email_unsubscribed (user_id, at) VALUES (%s, 1), (%s, 1)",
                (ALICE, BOB),
            )
        await accounts.delete_person(self.db, ALICE, now=NOW)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT user_id FROM sent_emails")
            sent = {r["user_id"] for r in await cur.fetchall()}
            cur = await conn.execute("SELECT user_id FROM email_unsubscribed")
            unsubscribed = {r["user_id"] for r in await cur.fetchall()}
        self.assertEqual(sent, {BOB})  # hers are gone, his are untouched
        self.assertEqual(unsubscribed, {ALICE, BOB})  # the never-email-me list stays
