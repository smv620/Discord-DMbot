"""A Discord account added to a DMbot account runs on the account's plan (#2533, slice 2b): the
plan, its hours and its campaign count belong to the account, so the bot asks "which account is
this Discord ID" before it checks them, through the one resolver (`accounts.account_of`). And what
stays per Discord ID stays: recording consent, the Try It marker and the lines a person said, so
two logins of one account never share them. Needs Postgres."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from dmbot import hours, usage
from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignError
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from tests.test_account_discord_ids import add_user, link
from tests.test_usage import GUILD_A, NOW, OWNER, UsageTest
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
