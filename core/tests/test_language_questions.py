"""A lore language for a character the campaign already had (#4229 slice 1c, Supervisor's ruling):
a character with no sheet gets a question, like a lineage (#3926), and Yes keeps it as the DM's
word; a player character whose sheet lacks it is named on the upload's note, with nothing stored.
The website's listing leaves language questions out until its page can word them. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import sheets
from dmbot.memory import trait_offers as to
from dmbot.memory.models import CONFIRMED, DM, LANGUAGE, NewName, Trait
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.traits_read import Spoken
from dmbot.ui import name_background as nb
from dmbot.ui import trait_questions as tq
from dmbot.web import trait_offers as web_offers
from tests.test_memory_names import GUILD, NamesTest
from tests.test_sheets import answer
from tests.test_web_trait_offers import DWARF
from tests.test_web_trait_offers import Base as WebBase

GIANT = Trait(LANGUAGE, "Giant", "", None, False)
PLAYER = 9


class TheWords(unittest.TestCase):
    def test_the_question_asks_if_they_speak_it(self) -> None:
        offer = to.Offer("e1", GIANT, "Brewner speaks Giant", "Notes", "Brewner")
        text = tq.card_text(offer, 1)
        self.assertTrue(text.startswith(tq.LANGUAGE_HEADING))
        self.assertIn("Does **Brewner** speak **Giant**?", text)
        self.assertIn("Giant is added to Brewner's languages.", text)
        self.assertEqual(
            tq.kept_text(offer), "Done: **Brewner** speaks **Giant**. ↩️ Undo takes it back."
        )

    def test_undo_and_a_refusal_say_what_happened_to_the_language(self) -> None:
        offer = to.Offer("e1", GIANT, "Brewner speaks Giant", "Notes", "Brewner")
        self.assertIn("**Brewner** no longer speaks **Giant**", tq.undone_text(offer))
        self.assertIn("has a sheet now, so add **Giant** there", tq.could_not_text(offer))

    def test_the_note_names_what_a_sheet_lacks_and_counts_the_rest(self) -> None:
        self.assertEqual(nb.sheet_languages_line(()), "")
        line = nb.sheet_languages_line([("Testa", "Elvish")])
        self.assertIn("**Testa** speaks **Elvish**, but the sheet doesn't list it", line)
        many = nb.sheet_languages_line([("Testa", f"Lang{n}") for n in range(7)])
        self.assertIn("(and 2 more)", many)

    def test_the_count_line_is_about_characters_not_only_lineage(self) -> None:
        self.assertIn("2 questions about your characters", nb.trait_questions_line(2))


class Asked(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = to.TraitOfferStore(self.db, lambda: 1_000)
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Brewner", "npc", CONFIRMED)], source="dm"
        )
        (brewner,) = made.value
        assert brewner is not None
        self.brewner: str = brewner

    def offer(self) -> to.Offer:
        return to.Offer(self.brewner, GIANT, "Brewner speaks Giant", "Notes")

    async def test_yes_keeps_it_as_the_dms_word_and_undo_takes_it_back(self) -> None:
        self.assertEqual(await self.store.offer(GUILD, self.campaign.id, [self.offer()]), 1)
        batch = await self.store.keep(self.memory, GUILD, self.campaign.id, self.offer())
        (kept,) = await self.memory.story_languages(GUILD, self.campaign.id, self.brewner)
        self.assertEqual((kept.name, kept.given_by), ("Giant", DM))
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)
        assert batch is not None
        await self.memory.undo(GUILD, self.campaign.id, batch)
        gone = await self.memory.story_languages(GUILD, self.campaign.id, self.brewner)
        self.assertEqual(gone, [])

    async def test_a_question_the_dm_answered_another_way_closes(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        await self.memory.add_story_language(
            GUILD, self.campaign.id, self.brewner, "giant", source=DM
        )
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_no_is_final(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        self.assertTrue(await self.store.never(GUILD, self.campaign.id, self.offer()))
        self.assertEqual(await self.store.offer(GUILD, self.campaign.id, [self.offer()]), 0)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_a_sheet_added_since_closes_the_question(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        async with self.db.guild(GUILD) as conn:  # (a typed sheet, as if the DM added one since)
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, sheet, source,"
                " fetched_at, player_id) VALUES (%s, %s, %s, '{}'::jsonb, 'typed', 1, %s)",
                (GUILD, self.campaign.id, self.brewner, PLAYER),
            )
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)

    async def test_the_upload_asks_about_one_with_no_sheet_and_names_a_sheets_gap(self) -> None:
        testa = (
            await self.memory.add_entity(
                GUILD,
                self.campaign.id,
                type="player_character",
                name="Testa",
                source="dm",
                status=CONFIRMED,
                played_by=PLAYER,
            )
        ).value.id
        data = sheets.parse(answer())
        data["languages"] = ["Common"]
        await SheetStore(self.db).save(GUILD, self.campaign.id, testa, data, 5, player=PLAYER)
        asked, on_sheets = await nb.ask_languages(
            self.memory,
            self.campaign,
            [
                Spoken("Brewner", "Giant", "Brewner speaks Giant"),
                Spoken("Testa", "Elvish", "Testa speaks Elvish"),
                Spoken("Testa", "Common", "Testa speaks Common"),  # on the sheet: nothing
            ],
            "Notes",
            None,
        )
        self.assertEqual((asked, on_sheets), (1, [("Testa", "Elvish")]))
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual((waiting.entity_id, waiting.trait.name), (self.brewner, "Giant"))
        self.assertEqual(await self.memory.story_languages(GUILD, self.campaign.id, testa), [])

    async def test_an_upload_never_asks_about_a_language_the_dm_said_no_to(self) -> None:
        # (#4362: the lore upload's own path, through `ask_languages`, for a character it knew)
        made = await self.memory.add_story_language(
            GUILD, self.campaign.id, self.brewner, "Giant", source="background"
        )
        assert made.value is not None
        await self.memory.remove_story_language(GUILD, self.campaign.id, made.value.id, source="dm")
        asked, _ = await nb.ask_languages(
            self.memory, self.campaign, [Spoken("Brewner", "Giant", "Brewner speaks Giant")],
            "Notes", None,
        )  # fmt: skip
        self.assertEqual((asked, await self.store.count(GUILD, self.campaign.id)), (0, 0))

    async def test_a_waiting_question_closes_when_the_dm_says_no_on_the_card(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        made = await self.memory.add_story_language(
            GUILD, self.campaign.id, self.brewner, "Giant", source="dm"
        )
        assert made.value is not None
        await self.memory.remove_story_language(GUILD, self.campaign.id, made.value.id, source="dm")
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])


class TheWebsiteLeavesThemOut(WebBase):
    async def test_a_language_question_is_not_listed_yet(self) -> None:
        await self.leave(
            to.Offer(self.mira, GIANT, "Mira speaks Giant", "Notes"),
            to.Offer(self.mira, DWARF, "Mira is a dwarf", "Notes"),
        )
        got = (await web_offers.waiting(self.web, self.session, self.cref))["offers"]
        self.assertEqual([o["kind"] for o in got], ["species"])  # the lineage one only
