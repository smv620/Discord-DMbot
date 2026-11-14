"""World notes (#4285 slice 1): the one list of what DMbot guessed about places and languages, for
the DM to check; ✅ on a line or on a group is one Undo, 🚫 is one Undo and a place's reading is
never read in again, and a language question waiting in Questions for you is never asked twice.
Invented names. Needs Postgres."""

from __future__ import annotations

from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

DM_SOURCE = "dm"


class WorldNotes(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.dale = await self.add("Frostvale", type="place", status="confirmed")
        await self.memory.set_place_level(GUILD_A, self.c, self.dale, "region", source="dm")
        self.brewner = await self.add("Brewner", type="npc", status="confirmed")

    async def read_place(self, value: str = "arctic", session_at: int | None = None) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.TERRAIN, value, session_at=session_at)],
            source="background",
        )  # fmt: skip

    async def read_language(self, name: str = "Giant") -> None:
        written = await self.memory.add_story_language(
            GUILD_A, self.c, self.brewner, name, source="background"
        )
        assert written.value is not None

    async def notes(self) -> list[wn.Note]:
        return await self.memory.world_notes(GUILD_A, self.c)

    async def test_the_list_holds_dmbots_reads_of_places_and_languages(self) -> None:
        await self.read_place(session_at=1_699_000_000)
        await self.read_language()
        got = [(n.kind, n.name, n.layer, n.value, n.session_at) for n in await self.notes()]
        self.assertEqual(
            got,
            [
                (wn.PLACE, "Frostvale", mf.TERRAIN, "arctic", 1_699_000_000),
                (wn.LANGUAGE, "Brewner", "", "Giant", None),
            ],
        )

    async def test_the_dms_own_word_is_no_note(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.dale, mf.TERRAIN, "forest", source=DM_SOURCE
        )
        await self.memory.add_story_language(GUILD_A, self.c, self.brewner, "Elvish", source="dm")
        self.assertEqual(await self.notes(), [])

    async def test_keep_on_a_line_is_one_undo(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        written = await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")
        self.assertEqual(written.value, 1)
        self.assertEqual(await self.notes(), [])
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual([n.id for n in await self.notes()], [note.id])

    async def test_keep_all_for_a_group_is_one_undo_and_leaves_the_other_group(self) -> None:
        await self.read_place()
        await self.read_language("Giant")
        await self.read_language("Dwarvish")
        shown = await self.notes()
        ids = wn.of_kind(shown, wn.LANGUAGE)
        written = await self.memory.keep_world_notes(GUILD_A, self.c, ids, source="dm")
        self.assertEqual(written.value, 2)
        self.assertEqual([n.kind for n in await self.notes()], [wn.PLACE])
        langs = await self.memory.story_languages(GUILD_A, self.c, self.brewner)
        self.assertEqual({lang.given_by for lang in langs}, {"dm"})
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(len(await self.notes()), 3)

    async def test_not_right_on_a_place_is_one_undo_and_never_read_again(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        written = await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        self.assertEqual(await self.notes(), [])
        await self.read_place()  # (the same reading again: the DM said no to it)
        self.assertEqual(await self.notes(), [])
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual([n.id for n in await self.notes()], [note.id])

    async def test_not_right_on_a_language_takes_it_away_with_undo(self) -> None:
        await self.read_language()
        (note,) = await self.notes()
        written = await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        self.assertEqual(await self.memory.story_languages(GUILD_A, self.c, self.brewner), [])
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(len(await self.notes()), 1)

    async def test_a_second_press_says_it_is_gone(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_world_notes(GUILD_A, self.c, ["nonsense"], source="dm")

    async def test_only_the_dm_checks_them(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="background")
        with self.assertRaises(MemoryRuleError):
            await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="background")

    async def test_a_turned_down_entry_and_a_character_with_a_sheet_have_none(self) -> None:
        await self.read_place()
        await self.read_language()
        await self.memory.set_entity_status(GUILD_A, self.c, self.dale, "rejected", source="dm")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet,"
                " source, fetched_at, player_id) VALUES (%s, %s, %s,"
                " 'https://www.dndbeyond.com/characters/1', '{}'::jsonb, 'dndbeyond', 1, %s)",
                (GUILD_A, self.c, self.brewner, DM),
            )
        self.assertEqual(await self.notes(), [])

    async def offer_language(self, name: str = "Giant", state: str = "waiting") -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_trait_offers (guild_id, campaign_id, entity_id, kind, name,"
                " name_key, state, created_at) VALUES (%s, %s, %s, 'language', %s, %s, %s, 1)",
                (GUILD_A, self.c, self.brewner, name, name.casefold(), state),
            )

    async def test_a_language_question_stays_in_questions_for_you(self) -> None:
        # (#4285, Supervisor's ruling: a language question is asked there, never here too)
        from dmbot.memory.trait_offers import TraitOfferStore

        offers = TraitOfferStore(self.db)
        await self.offer_language()
        await self.read_language("Giant")  # (DMbot's own read never hides the question)
        self.assertEqual(await offers.count(GUILD_A, self.c), 1)
        self.assertEqual(await self.notes(), [])

    async def test_a_language_answered_no_there_is_never_a_note(self) -> None:
        await self.offer_language(state="never")
        await self.read_language("Giant")
        self.assertEqual(await self.notes(), [])

    async def test_not_right_never_brings_a_question_back(self) -> None:
        from dmbot.memory.trait_offers import TraitOfferStore

        offers = TraitOfferStore(self.db)
        await self.read_language("Dwarvish")
        (note,) = await self.notes()
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        self.assertEqual((await offers.count(GUILD_A, self.c), await self.notes()), (0, []))

    async def test_a_ruler_the_dm_turned_down_is_no_note(self) -> None:
        council = await self.add("Council of Speakers", type="faction", status="confirmed")
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CONTROL, council, how="rules")],
            source="background",
        )  # fmt: skip
        (note,) = await self.notes()
        self.assertEqual((note.layer, note.value, note.how), (mf.CONTROL, council, "rules"))
        await self.memory.set_entity_status(GUILD_A, self.c, council, "rejected", source="dm")
        self.assertEqual(await self.notes(), [])
        with self.assertRaises(MemoryRuleError):  # (a list drawn before: never kept now)
            await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")

    async def test_a_kept_reading_is_the_dms_word_on_the_card(self) -> None:
        from dmbot.memory import gate

        await self.read_place()
        (note,) = await self.notes()
        await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")
        facts = await self.memory.map_facts(GUILD_A, self.c, [self.dale], gate.Viewer.the_dm())
        (fact,) = facts[self.dale]
        self.assertEqual((fact.status, fact.source, fact.table_knows), ("confirmed", "dm", False))

    async def test_hand_made_ids_are_refused(self) -> None:
        await self.read_place()
        for bad in ("place:", "bogus:x", "x", "place:not-a-row"):
            with self.subTest(bad=bad), self.assertRaises(MemoryRuleError):
                await self.memory.turn_down_world_note(GUILD_A, self.c, bad, source="dm")
        given = await self.memory.add_story_language(
            GUILD_A, self.c, self.brewner, "Elvish", source="dm"
        )
        assert given.value is not None
        with self.assertRaises(MemoryRuleError):  # (the DM's own word is no note)
            await self.memory.keep_world_notes(
                GUILD_A, self.c, [wn.note_id(wn.LANGUAGE, given.value.id)], source="dm"
            )

    async def test_another_campaign_or_server_cant_see_or_touch_them(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        for guild, name in ((GUILD_A, "Elsewhere"), (GUILD_B, "Far away")):
            other = (await self.campaigns.create(guild, name, DM)).id
            self.assertEqual(await self.memory.world_notes(guild, other), [])
            with self.assertRaises(MemoryRuleError):
                await self.memory.keep_world_notes(guild, other, [note.id], source="dm")
            with self.assertRaises(MemoryRuleError):
                await self.memory.turn_down_world_note(guild, other, note.id, source="dm")
        self.assertEqual([n.id for n in await self.notes()], [note.id])
