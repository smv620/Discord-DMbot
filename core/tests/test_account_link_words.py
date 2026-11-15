"""Adding and taking off a Discord account, with the words for each answer (#2533, slice 4). The
three refusals of an add are Supervisor's ruled words; none says which other account or what it
holds. Needs Postgres; invented numbers."""

from __future__ import annotations

import re

from dmbot import accounts
from dmbot.campaigns import CampaignStore
from tests.pg import DatabaseTest
from tests.test_account_discord_ids import add_user
from tests.test_owner_campaigns import WORKS
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

ACCOUNT, SECOND, THIRD, OWNER_OF_CAMPAIGNS, OTHER_ACCOUNT = 9001, 9002, 9003, 9004, 9005
NOW = 1_700_000_000


class LinkingWithWords(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await add_user(self.db, ACCOUNT)

    async def test_an_add_is_done_listed_and_a_remove_is_done(self) -> None:
        done = await accounts.link_discord_id(self.db, ACCOUNT, SECOND, now=NOW)
        self.assertEqual((done.ok, done.code, done.words), (True, "ok", ""))
        logins = await accounts.logins_of(self.db, ACCOUNT)
        self.assertEqual(
            [(x.discord_id, x.first, x.added_at) for x in logins],
            [(ACCOUNT, True, None), (SECOND, False, NOW)],
        )
        gone = await accounts.unlink_discord_id(self.db, ACCOUNT, SECOND, now=NOW + 1)
        self.assertTrue(gone.ok)
        self.assertEqual(
            [x.discord_id for x in await accounts.logins_of(self.db, ACCOUNT)], [ACCOUNT]
        )

    async def test_one_that_runs_campaigns_gets_the_ruled_words(self) -> None:
        await CampaignStore(self.db).create(1, "Frostmaiden", OWNER_OF_CAMPAIGNS)
        got = await accounts.link_discord_id(self.db, ACCOUNT, OWNER_OF_CAMPAIGNS, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "has_campaigns"))
        self.assertEqual(
            got.words,
            "That Discord account is already the DM of its own campaigns. Hand those campaigns "
            "over or delete them first, then add the account.",
        )

    async def test_one_with_its_own_plan_gets_the_ruled_words(self) -> None:
        # As give_plan in test_owner_campaigns does: the account row, then its plan. The door
        # checks a plan before a plain account (after taken and has_campaigns), so this is has_plan.
        async with self.db.user(OTHER_ACCOUNT) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (OTHER_ACCOUNT,),
            )
        async with self.db.plan_writer(OTHER_ACCOUNT) as conn:
            await conn.execute(
                INSERT_PLAN, {**PLAN_ROW, **WORKS, "campaign_cap": 1, "user_id": OTHER_ACCOUNT}
            )
        got = await accounts.link_discord_id(self.db, ACCOUNT, OTHER_ACCOUNT, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "has_plan"))
        self.assertEqual(
            got.words,
            "That Discord account pays for its own DMbot plan. Sign in as that account and cancel "
            "its plan first, then add it here.",
        )

    async def test_one_that_is_only_an_account_with_no_plan_gets_the_other_account_words(
        self,
    ) -> None:
        await add_user(self.db, OTHER_ACCOUNT)
        got = await accounts.link_discord_id(self.db, ACCOUNT, OTHER_ACCOUNT, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "has_account"))
        self.assertEqual(got.words, accounts.LINK_WORDS["has_account"])

    async def test_one_already_on_another_account_gets_the_ruled_words(self) -> None:
        await add_user(self.db, OTHER_ACCOUNT)
        self.assertTrue((await accounts.link_discord_id(self.db, OTHER_ACCOUNT, THIRD, now=NOW)).ok)
        got = await accounts.link_discord_id(self.db, ACCOUNT, THIRD, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "taken"))
        self.assertEqual(
            got.words,
            "That Discord account is already on another DMbot account. Remove it there first, "
            "then add it here.",
        )

    async def test_the_only_account_cannot_be_taken_off(self) -> None:
        got = await accounts.unlink_discord_id(self.db, ACCOUNT, ACCOUNT, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "last"))
        self.assertIn("only Discord account", got.words)

    async def test_one_that_is_not_on_the_account_says_so(self) -> None:
        got = await accounts.unlink_discord_id(self.db, ACCOUNT, THIRD, now=NOW)
        self.assertEqual((got.ok, got.code), (False, "not_linked"))

    async def test_another_persons_account_is_refused_with_the_sign_in_words(self) -> None:
        got = await accounts.link_discord_id(self.db, OTHER_ACCOUNT, SECOND, now=NOW)
        # (the door only works for the signed-in account: `db.user` here is OTHER_ACCOUNT, who has
        # no account row)
        self.assertFalse(got.ok)
        self.assertEqual(got.words, accounts.LINK_WORDS["not_yours"])

    def test_no_refusal_names_a_number_another_account_or_what_it_holds(self) -> None:
        for words in [*accounts.LINK_WORDS.values(), *accounts.UNLINK_WORDS.values()]:
            self.assertIsNone(re.search(r"\d", words))
            self.assertNotIn("@", words)
