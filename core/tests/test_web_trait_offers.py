"""The lineage and class questions waiting for the DM, read by the website (#3946): only a DM of the
campaign can read them; the same rows the bot's own reader gives (a confirmed character, still
open, a "never" left out); the quote and the upload's label are the DM's own words. Real Postgres;
invented names."""

from __future__ import annotations

from dmbot.memory import trait_offers as to
from dmbot.memory.models import CLASS, DM, SPECIES, Trait
from dmbot.web import trait_offers
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web

DWARF = Trait(SPECIES, "Dwarf", "SRD 5.2.1", "2024", True)
CLERIC = Trait(CLASS, "Cleric", "SRD 5.2.1", "2024", True)


class TraitOffersRead(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.offers = to.TraitOfferStore(self.db)

    async def leave(self, *offers: to.Offer) -> None:
        await self.offers.offer(self.g, self.one.id, list(offers), "a" * 32)

    async def test_a_waiting_question_is_listed_with_what_dmbot_has_now(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes, 9 Nov"))
        got = (await trait_offers.waiting(self.web, self.session, self.cref))["offers"]
        (one,) = got
        self.assertEqual((one["entity"], one["kind"], one["name"]), (self.mira, "species", "Dwarf"))
        self.assertEqual((one["quote"], one["upload"]), ("Mira is a dwarf", "Notes, 9 Nov"))
        self.assertEqual((one["known"], one["edition"], one["current"]), (True, "2024", None))
        self.assertEqual(one["classes"], [])
        self.assertTrue(one["who"])

    async def test_nothing_waiting_is_an_empty_list(self) -> None:
        got = await trait_offers.waiting(self.web, self.session, self.cref)
        self.assertEqual(got, {"offers": []})

    async def test_a_question_answered_never_is_not_listed(self) -> None:
        offer = to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes")
        await self.leave(offer)
        self.assertTrue(await self.offers.never(self.g, self.one.id, offer))
        got = await trait_offers.waiting(self.web, self.session, self.cref)
        self.assertEqual(got["offers"], [])

    async def test_a_class_question_lists_the_classes_they_have(self) -> None:
        await self.memory.set_rule_link(
            self.g, self.one.id, self.mira, CLASS, "Fighter",
            source=DM, rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        await self.leave(to.Offer(self.mira, CLERIC, "Mira prays", "Notes"))
        (one,) = (await trait_offers.waiting(self.web, self.session, self.cref))["offers"]
        self.assertEqual((one["kind"], one["classes"]), ("class", ["Fighter"]))

    async def test_a_question_already_true_is_not_listed(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        await self.memory.set_rule_link(
            self.g, self.one.id, self.mira, SPECIES, "dwarf",
            source=DM, rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        got = await trait_offers.waiting(self.web, self.session, self.cref)
        self.assertEqual(got["offers"], [])

    async def test_a_player_and_another_campaigns_dm_are_told_nothing(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        with self.assertRaises(RecapError) as player:
            await trait_offers.waiting(self.web, self.player, self.cref)
        self.assertEqual(player.exception.code, "not_found")
        with self.assertRaises(RecapError):  # a campaign this person is no DM of
            await trait_offers.waiting(self.web, self.session, f"{self.g}.{self.two.id}")
