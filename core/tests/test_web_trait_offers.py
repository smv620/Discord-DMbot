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


class Base(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.offers = to.TraitOfferStore(self.db)

    async def leave(self, *offers: to.Offer) -> None:
        await self.offers.offer(self.g, self.one.id, list(offers), "a" * 32)


class TraitOffersRead(Base):
    async def test_a_waiting_question_is_listed_with_what_dmbot_has_now(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes, 9 Nov"))
        got = (await trait_offers.waiting(self.web, self.session, self.cref))["offers"]
        (one,) = got
        self.assertEqual((one["entity"], one["kind"], one["name"]), (self.mira, "species", "Dwarf"))
        self.assertEqual((one["quote"], one["upload"]), ("Mira is a dwarf", "Notes, 9 Nov"))
        self.assertEqual((one["known"], one["edition"], one["current"]), (True, "2024", None))
        self.assertEqual(one["classes"], [])
        self.assertEqual(one["who"], "Mira Voss")

    async def test_nothing_waiting_is_an_empty_list(self) -> None:
        got = await trait_offers.waiting(self.web, self.session, self.cref)
        self.assertEqual(got, {"offers": []})

    async def test_a_question_answered_never_is_not_listed(self) -> None:
        offer = to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes")
        await self.leave(offer)
        self.assertTrue(await self.offers.never(self.g, self.one.id, offer))
        got = await trait_offers.waiting(self.web, self.session, self.cref)
        self.assertEqual(got["offers"], [])

    async def test_a_question_shows_the_lineage_they_have_now(self) -> None:
        await self.memory.set_rule_link(
            self.g, self.one.id, self.mira, SPECIES, "Elf",
            source=DM, rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        (one,) = (await trait_offers.waiting(self.web, self.session, self.cref))["offers"]
        self.assertEqual(one["current"], "Elf")

    async def test_a_character_not_confirmed_is_not_asked_about(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'proposed' WHERE id = %s", (self.mira,)
            )
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

    async def test_a_player_and_a_dm_of_another_campaign_are_told_nothing(self) -> None:
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        with self.assertRaises(RecapError) as player:
            await trait_offers.waiting(self.web, self.player, self.cref)
        self.assertEqual(player.exception.code, "not_found")
        with self.assertRaises(RecapError) as other:  # a campaign this person is no DM of
            await trait_offers.waiting(self.web, self.session, f"{self.g}.{self.two.id}")
        self.assertEqual(other.exception.code, "not_found")


class TraitAnswers(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.leave(to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"))
        self.key = to.Offer(self.mira, DWARF).key

    async def press(self, how: str = "keep", who: object = None, key: object = None) -> str:
        return await trait_offers.answer(
            self.web, who or self.session, self.cref, self.mira, "species", key or self.key, how
        )

    async def refused(self, **kw: object) -> str:  # type: ignore[override]
        with self.assertRaises(RecapError) as caught:
            await self.press(**kw)  # type: ignore[arg-type]
        return caught.exception.code

    async def test_an_answer_is_queued_and_read_back_waiting(self) -> None:
        made = await self.press("keep")
        got = await trait_offers.read_answer(self.web, self.session, self.cref, made)
        self.assertEqual((got["state"], got["action"], got["batch"]), ("waiting", "keep", None))
        self.assertEqual(got["progress"]["state"], "running")

    async def test_a_second_press_on_the_same_question_is_given_the_first_id(self) -> None:
        first = await self.press("keep")
        self.assertEqual(await self.press("never"), first)  # (one answer at a time)

    async def test_a_question_that_is_not_waiting_is_not_found(self) -> None:
        self.assertEqual(await self.refused(key="elf"), "not_found")
        offer = to.Offer(self.mira, DWARF)
        await self.offers.never(self.g, self.one.id, offer)  # (answered meanwhile)
        self.assertEqual(await self.refused(), "not_found")

    async def test_a_wrong_shape_is_refused_before_the_door(self) -> None:
        self.assertEqual(await self.refused(how="maybe"), "bad_value")
        with self.assertRaises(RecapError) as caught:
            await trait_offers.answer(
                self.web, self.session, self.cref, "nope", "species", self.key, "keep"
            )
        self.assertEqual(caught.exception.code, "bad_value")

    async def test_a_player_cannot_answer_or_read_an_answer(self) -> None:
        self.assertEqual(await self.refused(who=self.player), "not_found")
        made = await self.press("never")
        with self.assertRaises(RecapError) as caught:
            await trait_offers.read_answer(self.web, self.player, self.cref, made)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_the_answer_table_is_not_open_to_the_website_role(self) -> None:
        await self.press("keep")
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM trait_answers")

    async def test_a_press_that_stopped_moving_is_lost_and_can_be_pressed_again(self) -> None:
        first = await self.press("keep")
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE trait_answers SET created_at = created_at - 3600 WHERE id = %s", (first,)
            )
        self.assertNotEqual(await self.press("keep"), first)
