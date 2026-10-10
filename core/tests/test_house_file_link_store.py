"""The linked house-rules file in the database (#969): who may set it, that a link is
private to one campaign, that a new link forgets what was ignored, that it goes with the
campaign, and that a rule from a file may keep its own number only if it was never used."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.rules import house
from dmbot.rules.house import HOUSE_RULES_MAX, HouseRuleError, HouseRulesSection, HouseRuleStore
from dmbot.rules.house_file_link import BAD_LINK, TOO_LONG, HouseFileLinkStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, CO_DM, PLAYER = 7, 8, 9
DOC = "https://docs.google.com/document/d/" + "a" * 30 + "/edit?usp=sharing"
FINGERPRINT = "f" * 64


class LinkTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HouseRulesSection())
        self.links = HouseFileLinkStore(self.db, clock=lambda: 1_700_000_000.0)
        self.rules = HouseRuleStore(self.db)
        self.campaign = await self.campaigns.create(GUILD_A, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        self.elsewhere = await self.campaigns.create(GUILD_B, "Frostmaiden", DM)


class Linking(LinkTest):
    async def test_a_dm_links_a_file_and_it_is_read_back(self) -> None:
        saved = await self.links.set_link(GUILD_A, self.campaign.id, DM, f"  <{DOC}>  ")
        self.assertEqual((saved.link, saved.ignored, saved.set_by), (DOC, None, DM))
        back = await self.links.get(GUILD_A, self.campaign.id)
        assert back is not None
        self.assertEqual((back.link, back.set_by, back.set_at), (DOC, DM, 1_700_000_000))
        self.assertEqual(back.site, "docs.google.com")
        self.assertNotIn("a" * 30, back.site)  # only the site is ever shown

    async def test_a_second_dm_may_too_but_a_player_may_not(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.campaign.id, CO_DM)
        await self.links.set_link(GUILD_A, self.campaign.id, CO_DM, DOC)
        for call in (
            self.links.set_link(GUILD_A, self.campaign.id, PLAYER, DOC),
            self.links.clear(GUILD_A, self.campaign.id, PLAYER),
            self.links.ignore(GUILD_A, self.campaign.id, PLAYER, FINGERPRINT),
        ):
            with self.assertRaises(HouseRuleError) as caught:
                await call
            self.assertEqual(str(caught.exception), house.NOT_DM)
        back = await self.links.get(GUILD_A, self.campaign.id)
        assert back is not None
        self.assertEqual((back.set_by, back.ignored), (CO_DM, None))

    async def test_a_bad_link_is_refused_in_plain_words(self) -> None:
        for text, words in (
            ("", BAD_LINK),
            ("   ", BAD_LINK),
            ("https://x.com/" + "a" * 2000, TOO_LONG),
        ):
            with self.assertRaises(HouseRuleError) as caught:
                await self.links.set_link(GUILD_A, self.campaign.id, DM, text)
            self.assertEqual(str(caught.exception), words)
        with self.assertRaises(HouseRuleError):
            await self.links.set_link(GUILD_A, self.campaign.id, DM, "not a link at all")
        self.assertIsNone(await self.links.get(GUILD_A, self.campaign.id))

    async def test_a_new_link_forgets_what_was_ignored(self) -> None:
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC)
        await self.links.ignore(GUILD_A, self.campaign.id, DM, FINGERPRINT)
        back = await self.links.get(GUILD_A, self.campaign.id)
        assert back is not None
        self.assertEqual(back.ignored, FINGERPRINT)
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC.replace("edit", "view"))
        again = await self.links.get(GUILD_A, self.campaign.id)
        assert again is not None
        self.assertIsNone(again.ignored)

    async def test_unlinking(self) -> None:
        self.assertFalse(await self.links.clear(GUILD_A, self.campaign.id, DM))
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC)
        self.assertTrue(await self.links.clear(GUILD_A, self.campaign.id, DM))
        self.assertIsNone(await self.links.get(GUILD_A, self.campaign.id))


class Isolation(LinkTest):
    async def test_one_campaigns_link_is_not_another_campaigns(self) -> None:
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC)
        self.assertIsNone(await self.links.get(GUILD_A, self.other.id))
        self.assertIsNone(await self.links.get(GUILD_B, self.elsewhere.id))
        await self.links.set_link(GUILD_A, self.other.id, DM, DOC.replace("a" * 30, "b" * 30))
        mine = await self.links.get(GUILD_A, self.campaign.id)
        theirs = await self.links.get(GUILD_A, self.other.id)
        assert mine is not None and theirs is not None
        self.assertNotEqual(mine.link, theirs.link)

    async def test_another_server_can_neither_read_nor_change_it(self) -> None:
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC)
        self.assertIsNone(await self.links.get(GUILD_B, self.campaign.id))
        with self.assertRaises(HouseRuleError):
            await self.links.set_link(GUILD_B, self.campaign.id, DM, DOC)
        with self.assertRaises(HouseRuleError):
            await self.links.clear(GUILD_B, self.campaign.id, DM)
        back = await self.links.get(GUILD_A, self.campaign.id)
        assert back is not None
        self.assertEqual(back.link, DOC)

    async def test_the_link_goes_with_the_campaign(self) -> None:
        await self.links.set_link(GUILD_A, self.campaign.id, DM, DOC)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s",
                (GUILD_A, self.campaign.id),
            )
        self.assertIsNone(await self.links.get(GUILD_A, self.campaign.id))


class WantedNumbers(LinkTest):
    async def add(self, text: str, wanted: int | None = None) -> int:
        return (await self.rules.add(GUILD_A, self.campaign.id, DM, text, wanted=wanted)).number

    async def test_a_number_never_used_is_kept_and_the_next_goes_on_from_it(self) -> None:
        self.assertEqual(await self.add("From the file", wanted=10), 10)
        self.assertEqual(await self.add("Next"), 11)

    async def test_a_number_already_used_gets_the_next_free_one(self) -> None:
        for n in range(1, 4):
            await self.add(f"Rule {n}")
        self.assertEqual(await self.add("Wants 2", wanted=2), 4)
        self.assertEqual(await self.add("Wants 4", wanted=4), 5)

    async def test_a_removed_rules_number_is_not_given_again(self) -> None:
        for n in range(1, 4):
            await self.add(f"Rule {n}")
        await self.rules.remove(GUILD_A, self.campaign.id, DM, 2)
        self.assertEqual(await self.add("Wants the removed 2", wanted=2), 4)

    async def test_no_wanted_number_is_the_next_as_before(self) -> None:
        self.assertEqual([await self.add("A"), await self.add("B")], [1, 2])

    async def test_a_full_list_refuses_a_wanted_number_too(self) -> None:
        for n in range(HOUSE_RULES_MAX):
            await self.add(f"Rule {n}")
        with self.assertRaises(HouseRuleError) as caught:
            await self.add("One too many", wanted=500)
        self.assertEqual(str(caught.exception), house.FULL)

    async def test_only_a_dm_adds_a_wanted_number(self) -> None:
        with self.assertRaises(HouseRuleError):
            await self.rules.add(GUILD_A, self.campaign.id, PLAYER, "Nope", wanted=7)
        self.assertEqual(await self.rules.list(GUILD_A, self.campaign.id), [])
