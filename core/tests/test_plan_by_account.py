"""A Discord account added to a DMbot account runs on the account's plan (#2533, slice 2b): the
plan, its hours and its campaign count belong to the account, so the bot asks "which account is
this Discord ID" before it checks them, through the one resolver (`accounts.account_of`). And what
stays per Discord ID stays: recording consent, the Try It marker and the lines a person said, so
two logins of one account never share them. Needs Postgres."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from dmbot import entitlements, hours, usage
from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignError
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from tests.test_account_discord_ids import add_user, link
from tests.test_usage import GUILD_A, GUILD_B, NOW, OWNER, UsageTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

OWNER_2 = 9001  # a second Discord account of the owner
SITE = "https://dmbot.example"


class PlanByAccount(UsageTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await add_user(self.db, OWNER)
        self.assertEqual(await link(self.db, OWNER, OWNER_2), "ok")
        patcher = patch.object(DMBot, "_settle_cap", AsyncMock())  # (the pause is tested apart)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s", enforce_plans=True, site_url=SITE),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            meter=usage.Meter(self.db),
        )

    async def campaign(self) -> Campaign:
        found = await self.campaigns.get(GUILD_A, self.a.id)
        assert found is not None
        return found

    async def valid_plan(self, **values: object) -> None:
        """The account's plan (its web account is already there: `link` needs one)."""
        row = {
            **PLAN_ROW,
            "period_start": NOW - 1000,
            "period_end": int(time.time()) + 5000,
            **values,
            "user_id": OWNER,
        }
        async with self.db.plan_writer(OWNER) as conn:
            await conn.execute(INSERT_PLAN, row)

    async def test_the_added_login_is_the_owner_in_the_refusal_words(self) -> None:
        for starter in (OWNER, OWNER_2):  # (no plan: the owner is told to pick one)
            self.assertEqual(
                await self.bot.plan_refusal(GUILD_A, await self.campaign(), starter),
                f"Your plan has ended. Pick one here: {SITE}/account",
            )

    async def test_a_co_dm_is_still_not_told_about_the_owners_plan(self) -> None:
        self.assertEqual(
            await self.bot.plan_refusal(GUILD_A, await self.campaign(), 5555),
            hours.START_BLOCKED,
        )

    async def test_an_added_login_may_start_the_accounts_campaign_on_the_accounts_plan(
        self,
    ) -> None:
        await self.valid_plan(campaign_cap=2)  # the account has two campaigns
        for starter in (OWNER, OWNER_2):
            self.assertIsNone(await self.bot.plan_refusal(GUILD_A, await self.campaign(), starter))

    async def test_an_added_login_counts_the_accounts_campaigns_when_it_makes_one(self) -> None:
        await self.valid_plan(campaign_cap=2)  # the account has two already
        expected = (
            "Your plan covers 2 campaigns, and you have 2. To make a new one, "
            f"{hours.PAUSE_WAY} or change your plan here: {SITE}/account"
        )
        self.assertEqual(await self.bot.create_refusal(GUILD_A, OWNER), expected)
        self.assertEqual(await self.bot.create_refusal(GUILD_A, OWNER_2), expected)

    async def test_an_added_login_may_make_one_when_the_plan_has_room(self) -> None:
        await self.valid_plan(campaign_cap=3)
        self.assertIsNone(await self.bot.create_refusal(GUILD_A, OWNER_2))

    async def test_a_campaign_made_by_an_added_login_belongs_to_the_account(self) -> None:
        made = await self.campaigns.create(GUILD_A, "Made from the second login", OWNER_2)
        self.assertEqual(made.owner_user_id, OWNER)
        self.assertEqual(made.dm_accounts, frozenset({OWNER}))
        self.assertTrue(made.is_owner(OWNER_2) and made.is_dm(OWNER_2))

    async def test_someone_who_never_signed_in_is_their_own_account(self) -> None:
        made = await self.campaigns.create(GUILD_A, "No website account", 4242)
        self.assertEqual(made.owner_user_id, 4242)
        self.assertEqual(await self.bot._account_number(4242), 4242)
        self.assertEqual(await self.bot._account_number(OWNER_2), OWNER)

    async def test_a_failed_lookup_falls_back_to_the_discord_id(self) -> None:
        with (
            patch("dmbot.bot.accounts.account_of", AsyncMock(side_effect=RuntimeError("down"))),
            self.assertLogs("dmbot.bot", "WARNING"),
        ):
            self.assertEqual(await self.bot._account_number(OWNER_2), OWNER_2)

    async def test_a_first_login_taken_off_cannot_make_a_campaign(self) -> None:
        from tests.test_account_discord_ids import unlink

        await unlink(self.db, OWNER, OWNER)
        with self.assertRaises(CampaignError):
            await self.campaigns.create(GUILD_A, "From a login taken off", OWNER)

    # What stays per Discord ID: one test for each, so it cannot drift.

    async def test_recording_consent_stays_per_discord_id(self) -> None:
        consent = ConsentStore(self.db)
        await consent.grant(GUILD_A, OWNER)
        self.assertTrue(consent.has_consent(GUILD_A, OWNER))
        self.assertFalse(consent.has_consent(GUILD_A, OWNER_2))  # (the account's other login)

    async def test_the_try_it_marker_stays_per_discord_id(self) -> None:
        async with self.db.plan_writer(OWNER) as conn:
            await conn.execute("INSERT INTO try_it_used (user_id) VALUES (%s)", (OWNER,))
            cur = await conn.execute(
                "SELECT user_id FROM try_it_used WHERE user_id IN (%s, %s)", (OWNER, OWNER_2)
            )
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [OWNER])


class HandOversByAccount(UsageTest):
    """Ownership writes store the account, whichever login of it acted (#2533, slice 2b)."""

    RECIP = 6001
    RECIP_2 = 6002  # a second login of the recipient's account

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        await add_user(self.db, self.RECIP)
        self.assertEqual(await link(self.db, self.RECIP, self.RECIP_2), "ok")
        # The recipient's account has room (the free list is by account: the added login is not
        # on it, so the slot check passing proves it is read for the account, #2533).
        entitlements.configure_free_users((self.RECIP,))

    async def owner_of(self, campaign_id: str, guild: int = GUILD_A) -> int | None:
        found = await self.campaigns.get(guild, campaign_id)
        assert found is not None
        return found.owner_user_id

    async def offer(self, from_login: int, to_login: int) -> int:
        offer = await self.campaigns.offer_handover(
            GUILD_A, self.a.id, from_login, to_login, NOW, from_name="A", to_name="B"
        )
        return offer.id

    async def test_the_owner_may_offer_from_an_added_login(self) -> None:
        await self.offer(OWNER_2, self.RECIP)  # (does not raise NOT_THE_OWNER)

    async def test_an_offer_to_another_login_of_the_owners_own_account_is_to_themselves(
        self,
    ) -> None:
        with self.assertRaises(CampaignError):
            await self.offer(OWNER, OWNER_2)

    async def test_an_added_login_of_the_person_offered_may_accept_and_the_account_owns_it(
        self,
    ) -> None:
        offer_id = await self.offer(OWNER, self.RECIP)
        result = await self.campaigns.accept_handover(GUILD_A, offer_id, self.RECIP_2, NOW + 10)
        self.assertEqual(result, "accepted")
        self.assertEqual(await self.owner_of(self.a.id), self.RECIP)  # the account, not the login
        kept = await self.campaigns.get(GUILD_A, self.a.id)
        assert kept is not None
        self.assertIn(self.RECIP, kept.dm_accounts)
        self.assertNotIn(self.RECIP_2, kept.dm_accounts)
        self.assertTrue(kept.is_owner(self.RECIP_2))

    async def test_a_stranger_login_cannot_accept_someone_elses_offer(self) -> None:
        offer_id = await self.offer(OWNER, self.RECIP)
        self.assertEqual(
            await self.campaigns.accept_handover(GUILD_A, offer_id, 4242, NOW + 10), "gone"
        )

    async def test_an_added_login_may_take_on_a_campaign_with_no_owner(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD_A, self.a.id),
            )
        await self.campaigns.add_dm(GUILD_A, self.a.id, self.RECIP_2)
        self.assertEqual(
            await self.campaigns.take_ownership(GUILD_A, self.a.id, self.RECIP_2, NOW), "taken"
        )
        self.assertEqual(await self.owner_of(self.a.id), self.RECIP)

    async def test_a_backup_restored_by_an_added_login_belongs_to_the_account(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, self.RECIP_2)
        self.assertEqual(restored.owner_user_id, self.RECIP)
        self.assertIn(self.RECIP, restored.dm_accounts)
