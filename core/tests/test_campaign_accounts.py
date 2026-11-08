"""A campaign's people when a DMbot account holds several Discord accounts (#2533, slice 2a).

The tables keep account numbers; the store expands them into the Discord IDs that can act, so
"is this Discord user a DM" is true for any Discord account of a DM, and anything that sends, lists
or counts per DM goes by account. Needs Postgres."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import Campaign, CampaignError
from tests.pg import DatabaseTest
from tests.test_account_discord_ids import add_user, link, unlink

OWNER = 7001
OWNER_2 = 7002  # a second Discord account of the owner
CO_DM = 8001
CO_DM_2 = 8002
GUILD = 111


class Expansion(DatabaseTest):
    async def make(self) -> tuple[CampaignStore, Campaign]:
        store = CampaignStore(self.db)
        campaign = await store.create(GUILD, "Frostmaiden", OWNER)
        return store, campaign

    async def test_two_discord_ids_of_one_account_are_both_the_owner_and_both_dm(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, OWNER)
        self.assertEqual(await link(self.db, OWNER, OWNER_2), "ok")
        again = await store.get(GUILD, campaign.id)
        assert again is not None
        for who in (OWNER, OWNER_2):
            self.assertTrue(again.is_owner(who))
            self.assertTrue(again.is_dm(who))
        self.assertFalse(again.is_dm(CO_DM))
        # ...but one DM: a message, a mention and a count go by account.
        self.assertEqual(again.dm_accounts, frozenset({OWNER}))
        self.assertEqual(again.dm_user_ids, frozenset({OWNER, OWNER_2}))
        self.assertEqual(again.owner_user_id, OWNER)

    async def test_a_newly_added_discord_id_is_a_dm_on_the_very_next_read(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, OWNER)
        before = await store.get(GUILD, campaign.id)
        assert before is not None
        self.assertFalse(before.is_dm(OWNER_2))
        await link(self.db, OWNER, OWNER_2)
        after = await store.get(GUILD, campaign.id)
        assert after is not None
        self.assertTrue(after.is_dm(OWNER_2))
        await unlink(self.db, OWNER, OWNER_2)
        gone = await store.get(GUILD, campaign.id)
        assert gone is not None
        self.assertFalse(gone.is_dm(OWNER_2))

    async def test_a_removed_first_id_no_longer_acts_but_the_account_still_does(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        await unlink(self.db, OWNER, OWNER)
        again = await store.get(GUILD, campaign.id)
        assert again is not None
        self.assertFalse(again.is_dm(OWNER))
        self.assertFalse(again.is_owner(OWNER))
        self.assertTrue(again.is_dm(OWNER_2))
        self.assertTrue(again.is_owner(OWNER_2))
        self.assertEqual(again.dm_accounts, frozenset({OWNER}))

    async def test_adding_a_second_discord_account_of_a_dm_changes_nothing(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        same = await store.add_dm(GUILD, campaign.id, OWNER_2)
        self.assertEqual(same.dm_accounts, frozenset({OWNER}))
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT user_id FROM campaign_dms")
            self.assertEqual([int(r["user_id"]) for r in await cur.fetchall()], [OWNER])

    async def test_a_dm_added_by_a_second_discord_account_is_stored_as_the_account(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, CO_DM)
        await link(self.db, CO_DM, CO_DM_2)
        added = await store.add_dm(GUILD, campaign.id, CO_DM_2)
        self.assertEqual(added.dm_accounts, frozenset({OWNER, CO_DM}))
        self.assertTrue(added.is_dm(CO_DM) and added.is_dm(CO_DM_2))
        self.assertEqual(added.dm_account_of(CO_DM_2), CO_DM)
        removed = await store.remove_dm(GUILD, campaign.id, CO_DM_2)
        self.assertEqual(removed.dm_accounts, frozenset({OWNER}))
        self.assertFalse(removed.is_dm(CO_DM))

    async def test_the_last_dm_account_cannot_be_removed_by_any_of_its_discord_ids(self) -> None:
        store, campaign = await self.make()
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        with self.assertRaises(CampaignError):
            await store.remove_dm(GUILD, campaign.id, OWNER_2)

    async def test_people_who_never_signed_in_are_their_own_account(self) -> None:
        store, campaign = await self.make()
        added = await store.add_dm(GUILD, campaign.id, CO_DM)
        self.assertEqual(added.dm_accounts, frozenset({OWNER, CO_DM}))
        self.assertEqual(added.dm_user_ids, frozenset({OWNER, CO_DM}))
        self.assertTrue(added.is_owner(OWNER))
        self.assertFalse(added.is_owner(CO_DM))

    async def test_a_campaign_built_by_hand_follows_its_discord_ids(self) -> None:
        by_hand = Campaign(
            id="x",
            guild_id=1,
            name="n",
            created_at=0,
            last_played_at=None,
            target_ruleset="2024",
            fallback_ruleset="2014",
            optional_rules_default=True,
            dm_user_ids=frozenset({5, 6}),
            dm_screen_channel_id=None,
            last_voice_channel_id=None,
            dm_screen_visibility="private",
            owner_user_id=5,
        )
        self.assertEqual(by_hand.dm_accounts, frozenset({5, 6}))
        self.assertTrue(by_hand.is_owner(5) and not by_hand.is_owner(6))
        self.assertEqual(by_hand.dm_account_of(6), 6)


class Guards(DatabaseTest):
    async def test_the_last_dm_account_stays_when_its_first_id_was_taken_off(self) -> None:
        store = CampaignStore(self.db)
        campaign = await store.create(GUILD, "Frostmaiden", OWNER)
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        await unlink(self.db, OWNER, OWNER)
        for who in (OWNER, OWNER_2):  # the account number, or a Discord ID that acts as it
            with self.assertRaises(CampaignError):
                await store.remove_dm(GUILD, campaign.id, who)
        again = await store.get(GUILD, campaign.id)
        assert again is not None
        self.assertEqual(again.dm_accounts, frozenset({OWNER}))

    async def test_a_taken_off_first_id_cannot_be_added_as_a_dm(self) -> None:
        store = CampaignStore(self.db)
        campaign = await store.create(GUILD, "Frostmaiden", OWNER)
        await add_user(self.db, CO_DM)
        await link(self.db, CO_DM, CO_DM_2)
        await unlink(self.db, CO_DM, CO_DM)
        with self.assertRaises(CampaignError):
            await store.add_dm(GUILD, campaign.id, CO_DM)
        again = await store.get(GUILD, campaign.id)
        assert again is not None
        self.assertEqual(again.dm_accounts, frozenset({OWNER}))
        # ...while its other Discord account can be added, and is stored as the account.
        added = await store.add_dm(GUILD, campaign.id, CO_DM_2)
        self.assertEqual(added.dm_accounts, frozenset({OWNER, CO_DM}))

    async def test_a_whole_server_list_mixes_accounts_and_single_people(self) -> None:
        store = CampaignStore(self.db)
        first = await store.create(GUILD, "One", OWNER)
        second = await store.create(GUILD, "Two", CO_DM)
        await add_user(self.db, OWNER)
        await link(self.db, OWNER, OWNER_2)
        by_id = {c.id: c for c in await store.list_campaigns(GUILD)}
        self.assertEqual(by_id[first.id].dm_user_ids, frozenset({OWNER, OWNER_2}))
        self.assertEqual(by_id[first.id].dm_accounts, frozenset({OWNER}))
        self.assertEqual(by_id[second.id].dm_user_ids, frozenset({CO_DM}))
        self.assertTrue(by_id[second.id].is_owner(CO_DM))
