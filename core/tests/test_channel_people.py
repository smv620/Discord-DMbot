"""Who can see a campaign's DM Bot and transcript channels (#3891): the plans for each choice, the
people list, what an exposed channel looks like, and the stored setting with its by-hand list.
Invented ids."""

from __future__ import annotations

import unittest
from collections.abc import Mapping

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.dm_screen import channel_people as cp
from dmbot.dm_screen.rules import FULL, HIDDEN, READ_ONLY, Perms, Target, everyone
from tests.pg import DatabaseTest

GUILD, BOT, DM, ALICE, BOB, EVE = 1000, 2000, 11, 12, 13, 14
ROLE = 555


class Plans(unittest.TestCase):
    def test_everyone_is_the_channels_as_they_were(self) -> None:
        plan = cp.channel_plan(cp.EVERYONE, guild_id=GUILD, bot_id=BOT, people=[DM, ALICE])
        self.assertEqual(plan, {everyone(GUILD): READ_ONLY, Target("member", BOT): FULL})

    def test_people_hides_the_channel_and_lets_each_person_read_it(self) -> None:
        plan = cp.channel_plan(cp.PEOPLE, guild_id=GUILD, bot_id=BOT, people=[ALICE, DM])
        self.assertEqual(plan[everyone(GUILD)], HIDDEN)
        self.assertEqual(plan[Target("member", DM)], READ_ONLY)
        self.assertEqual(plan[Target("member", ALICE)], READ_ONLY)
        self.assertEqual(plan[Target("member", BOT)], FULL)  # only DMbot posts
        self.assertNotIn(Target("member", EVE), plan)

    def test_a_choice_it_does_not_know_is_the_default_the_campaigns_people(self) -> None:
        plan = cp.channel_plan("anyone", guild_id=GUILD, bot_id=BOT, people=[DM])
        self.assertEqual(plan[everyone(GUILD)], HIDDEN)

    def test_the_people_are_the_dms_players_recorded_and_by_hand_but_not_dmbot(self) -> None:
        people = cp.people_of(
            dm_ids=[DM, 15], player_ids=[ALICE], recorded_ids=[BOB, -3, ALICE],
            by_hand=[EVE, BOT], bot_id=BOT,
        )  # fmt: skip
        self.assertEqual(people, {DM, 15, ALICE, BOB, EVE})  # (-3 is a speaker without Discord)

    def test_switching_back_to_everyone_restores_todays_overwrites(self) -> None:
        hidden = cp.channel_plan(cp.PEOPLE, guild_id=GUILD, bot_id=BOT, people=[DM])
        back = cp.channel_plan(cp.EVERYONE, guild_id=GUILD, bot_id=BOT, people=[DM])
        self.assertNotEqual(hidden, back)
        self.assertEqual(back[everyone(GUILD)], READ_ONLY)
        self.assertNotIn(Target("member", DM), back)  # (the bot owns member overwrites: dropped)


class Exposure(unittest.TestCase):
    def found(self, overwrites: Mapping[Target, Perms]) -> cp.Exposure:
        return cp.find_exposure(
            cp.PEOPLE, overwrites, guild_id=GUILD, bot_id=BOT, people=[DM, ALICE]
        )

    def test_a_channel_set_up_for_the_people_is_not_exposed(self) -> None:
        plan = cp.channel_plan(cp.PEOPLE, guild_id=GUILD, bot_id=BOT, people=[DM, ALICE])
        self.assertFalse(self.found(dict(plan)))

    def test_a_server_role_that_can_see_it_is_reported(self) -> None:
        plan: dict[Target, Perms] = dict(
            cp.channel_plan(cp.PEOPLE, guild_id=GUILD, bot_id=BOT, people=[DM, ALICE])
        )
        plan[Target("role", ROLE)] = {"view_channel": True}
        self.assertEqual(self.found(plan).roles, (ROLE,))

    def test_a_member_who_is_not_one_of_the_people_is_reported(self) -> None:
        plan: dict[Target, Perms] = dict(
            cp.channel_plan(cp.PEOPLE, guild_id=GUILD, bot_id=BOT, people=[DM, ALICE])
        )
        plan[Target("member", EVE)] = {"view_channel": True}
        self.assertEqual(self.found(plan).members, (EVE,))

    def test_everyone_still_able_to_see_it_is_reported(self) -> None:
        self.assertTrue(self.found({everyone(GUILD): READ_ONLY}).everyone)

    def test_nothing_is_exposed_when_the_dm_chose_everyone(self) -> None:
        found = cp.find_exposure(
            cp.EVERYONE, {everyone(GUILD): READ_ONLY}, guild_id=GUILD, bot_id=BOT, people=[DM]
        )
        self.assertFalse(found)


class Stored(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)

    async def test_a_new_campaign_is_for_its_own_people(self) -> None:
        self.assertEqual(self.campaign.channels_visibility, cp.PEOPLE)

    async def test_the_choice_is_kept_and_a_wrong_one_is_refused(self) -> None:
        done = await self.store.set_channels_visibility(GUILD, self.campaign.id, cp.EVERYONE)
        self.assertEqual(done.channels_visibility, cp.EVERYONE)
        with self.assertRaises(CampaignError):
            await self.store.set_channels_visibility(GUILD, self.campaign.id, "some")

    async def test_the_by_hand_list_adds_once_and_removes(self) -> None:
        cid = self.campaign.id
        self.assertTrue(await self.store.add_channel_person(GUILD, cid, EVE, DM, 100))
        self.assertFalse(await self.store.add_channel_person(GUILD, cid, EVE, DM, 101))
        self.assertTrue(await self.store.add_channel_person(GUILD, cid, BOB, DM, 102))
        self.assertEqual(await self.store.channel_people_by_hand(GUILD, cid), [EVE, BOB])
        self.assertTrue(await self.store.remove_channel_person(GUILD, cid, EVE))
        self.assertFalse(await self.store.remove_channel_person(GUILD, cid, EVE))
        self.assertEqual(await self.store.channel_people_by_hand(GUILD, cid), [BOB])

    async def test_a_backup_keeps_the_choice_and_leaves_the_by_hand_list_out(self) -> None:
        cid = self.campaign.id
        await self.store.set_channels_visibility(GUILD, cid, cp.EVERYONE)
        await self.store.add_channel_person(GUILD, cid, EVE, DM, 100)
        data = await self.store.export(GUILD, cid)
        self.assertEqual(data["campaign"]["channels_visibility"], cp.EVERYONE)
        self.assertNotIn("channel_people", str(data))  # (the list is not in any section)


if __name__ == "__main__":
    unittest.main()
