"""World notes (#4285 slice 1): the one list of what DMbot guessed about places and languages, for
the DM to check; ✅ on a line or on a group is one Undo, 🚫 is one Undo and a place's reading is
never read in again, and a language question waiting in Questions for you is never asked twice.
Invented names. Needs Postgres."""

from __future__ import annotations

from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, MemoryTest


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
        kept = await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")
        self.assertEqual(kept.value, 0)
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_world_notes(GUILD_A, self.c, ["nonsense"], source="dm")

    async def test_only_the_dm_checks_them(self) -> None:
        await self.read_place()
        (note,) = await self.notes()
        for call in (
            self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="background"),
            self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="background"),
        ):
            with self.assertRaises(MemoryRuleError):
                await call

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

    async def test_a_waiting_language_question_is_never_asked_twice(self) -> None:
        # (#4285, Supervisor: language questions stay in Questions for you; a story row holding the
        # same language hides the question there, so it is asked in one place only)
        from dmbot.memory.trait_offers import TraitOfferStore

        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_trait_offers (guild_id, campaign_id, entity_id, kind, name,"
                " name_key, state, created_at) VALUES (%s, %s, %s, 'language', 'Giant', 'giant',"
                " 'waiting', 1)",
                (GUILD_A, self.c, self.brewner),
            )
        offers = TraitOfferStore(self.db)
        self.assertEqual(await offers.count(GUILD_A, self.c), 1)  # (asked there, not here)
        self.assertEqual(await self.notes(), [])
        await self.read_language("Giant")
        asked = await offers.count(GUILD_A, self.c) + len(await self.notes())
        self.assertEqual(asked, 1)  # (now here only)

    async def test_another_campaign_sees_none_of_them(self) -> None:
        await self.read_place()
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        self.assertEqual(await self.memory.world_notes(GUILD_A, other), [])


DM_SOURCE = "dm"
