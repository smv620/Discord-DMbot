"""The languages a character knows from the story (#4229 slice 1): beside the sheets, never in the
graph. A character with a sheet keeps its languages there; "who understands this?" reads both."""

from __future__ import annotations

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import sheets
from dmbot.memory.models import DM, Known, MemoryRuleError
from dmbot.memory.sheet_store import SheetStore
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest
from tests.test_sheets import answer

PLAYER = 9


class StoryLanguages(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.npc = await self.add("Brewner")
        self.sheets = SheetStore(self.db)

    async def with_a_sheet(self, *languages: str) -> str:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        data = sheets.parse(answer())
        data["languages"] = list(languages)
        await self.sheets.save(GUILD_A, self.c, pc, data, 5, player=PLAYER)
        return pc

    async def test_a_read_then_the_dms_word_keeps_it_once_and_checked(self) -> None:
        read = await self.memory.add_story_language(
            GUILD_A, self.c, self.npc, "Elvish", source="background"
        )
        assert read.value is not None
        self.assertEqual((read.value.given_by, read.value.srd), ("read", True))
        again = await self.memory.add_story_language(GUILD_A, self.c, self.npc, "elvish", source=DM)
        assert again.value is not None
        self.assertEqual((again.value.id, again.value.given_by), (read.value.id, DM))
        (only,) = await self.memory.story_languages(GUILD_A, self.c, self.npc)
        self.assertEqual(only.name, "Elvish")

    async def test_a_campaigns_own_tongue_is_kept_and_not_marked_srd(self) -> None:
        own = await self.memory.add_story_language(
            GUILD_A, self.c, self.npc, "the old tongue of Netheril", source=DM
        )
        assert own.value is not None
        self.assertFalse(own.value.srd)

    async def test_only_a_character_knows_languages(self) -> None:
        place = await self.add("Targos", type="place")
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_story_language(GUILD_A, self.c, place, "Elvish", source=DM)

    async def test_a_character_with_a_sheet_keeps_its_languages_there(self) -> None:
        pc = await self.with_a_sheet("Common")
        skipped = await self.memory.add_story_language(
            GUILD_A, self.c, pc, "Elvish", source="background"
        )
        self.assertIsNone(skipped.value)
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_story_language(GUILD_A, self.c, pc, "Elvish", source=DM)
        self.assertEqual(await self.memory.story_languages(GUILD_A, self.c, pc), [])

    async def test_only_the_dm_takes_one_away_and_undo_brings_it_back(self) -> None:
        added = await self.memory.add_story_language(
            GUILD_A, self.c, self.npc, "Dwarvish", source=DM
        )
        assert added.value is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.remove_story_language(
                GUILD_A, self.c, added.value.id, source="background"
            )
        gone = await self.memory.remove_story_language(GUILD_A, self.c, added.value.id, source=DM)
        self.assertEqual(await self.memory.story_languages(GUILD_A, self.c, self.npc), [])
        assert gone.batch is not None
        await self.memory.undo(GUILD_A, self.c, gone.batch)
        (back,) = await self.memory.story_languages(GUILD_A, self.c, self.npc)
        self.assertEqual(back.name, "Dwarvish")

    async def test_who_understands_reads_the_sheet_first_and_the_story_beside_it(self) -> None:
        pc = await self.with_a_sheet("Common", "Dwarvish")
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        await self.memory.add_story_language(
            GUILD_A, self.c, self.npc, "Sylvan", source="background"
        )
        nobody = await self.add("Grik")
        known = await self.memory.languages_known(GUILD_A, self.c, [pc, self.npc, nobody])
        self.assertEqual(known[pc], [Known("Common", "sheet"), Known("Dwarvish", "sheet")])
        self.assertEqual(known[self.npc], [Known("Elvish", DM), Known("Sylvan", "read")])
        self.assertNotIn(nobody, known)
        self.assertEqual(await self.memory.languages_known(GUILD_A, self.c, []), {})

    async def test_another_campaign_sees_none_of_them(self) -> None:
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM_USER)).id
        self.assertEqual(await self.memory.languages_known(GUILD_A, other, [self.npc]), {})

    async def test_a_merge_carries_them_and_the_dms_word_wins(self) -> None:
        twin = await self.add("Brewner the Brewer")
        await self.memory.add_story_language(
            GUILD_A, self.c, self.npc, "Dwarvish", source="background"
        )
        await self.memory.add_story_language(GUILD_A, self.c, twin, "Dwarvish", source=DM)
        await self.memory.add_story_language(GUILD_A, self.c, twin, "Giant", source=DM)
        await self.memory.merge(GUILD_A, self.c, self.npc, twin, source=DM, dm_said_same=True)
        kept = await self.memory.story_languages(GUILD_A, self.c, self.npc)
        self.assertEqual(
            sorted((x.name, x.given_by) for x in kept), [("Dwarvish", DM), ("Giant", DM)]
        )

    async def test_they_survive_a_backup(self) -> None:
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM_USER)
        entities = await self.memory.entities(GUILD_B, restored.id)
        (brewner,) = [e for e in entities if e.name == "Brewner"]
        (copy,) = await self.memory.story_languages(GUILD_B, restored.id, brewner.id)
        self.assertEqual((copy.name, copy.given_by, copy.srd), ("Elvish", DM, True))

    async def test_a_damaged_backup_row_is_refused(self) -> None:
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        for damage in ({"name_key": "not elvish"}, {"given_by": "player"}, {"srd": "yes"}):
            with self.subTest(damage=damage):
                data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
                assert isinstance(data, dict)
                rows = [r for r in data["sections"]["memory"] if r["table"] == "story_language"]
                self.assertEqual(len(rows), 1)
                rows[0].update(damage)
                with self.assertRaisesRegex(CampaignError, "damaged"):
                    await self.campaigns.import_backup(GUILD_B, data, DM_USER)

    async def test_a_sheet_added_later_hides_the_story_rows(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        await self.memory.add_story_language(GUILD_A, self.c, pc, "Elvish", source=DM)
        data = sheets.parse(answer())
        data["languages"] = ["Common"]
        await self.sheets.save(GUILD_A, self.c, pc, data, 5, player=PLAYER)
        known = await self.memory.languages_known(GUILD_A, self.c, [pc])
        self.assertEqual(known[pc], [Known("Common", "sheet")])

    async def test_a_character_made_a_place_loses_them_and_its_backup_still_loads(self) -> None:
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        changed = await self.memory.set_entity_type(GUILD_A, self.c, self.npc, "place", source=DM)
        self.assertEqual(await self.memory.story_languages(GUILD_A, self.c, self.npc), [])
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM_USER)
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch)  # a character again, and its tongue
        (back,) = await self.memory.story_languages(GUILD_A, self.c, self.npc)
        self.assertEqual(back.name, "Elvish")

    async def test_a_merge_into_a_place_or_a_sheet_leaves_them_behind(self) -> None:
        await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        pc = await self.with_a_sheet("Common")
        await self.memory.merge(GUILD_A, self.c, pc, self.npc, source=DM, dm_said_same=True)
        known = await self.memory.languages_known(GUILD_A, self.c, [pc])
        self.assertEqual(known[pc], [Known("Common", "sheet")])

    async def test_undoing_the_entry_that_has_them_is_refused_not_lost(self) -> None:
        made = await self.memory.add_entity(
            GUILD_A, self.c, type="npc", name="Vexnir", source=DM, status="confirmed"
        )
        await self.memory.add_story_language(
            GUILD_A, self.c, made.value.id, "Undercommon", source=DM
        )
        assert made.batch is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD_A, self.c, made.batch)
        (kept,) = await self.memory.story_languages(GUILD_A, self.c, made.value.id)
        self.assertEqual(kept.name, "Undercommon")

    async def test_a_name_whose_key_grows_too_long_is_refused_plainly(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "too long"):
            await self.memory.add_story_language(GUILD_A, self.c, self.npc, "ß" * 40, source=DM)

    async def test_removing_one_already_gone_says_so(self) -> None:
        added = await self.memory.add_story_language(GUILD_A, self.c, self.npc, "Elvish", source=DM)
        assert added.value is not None
        await self.memory.remove_story_language(GUILD_A, self.c, added.value.id, source=DM)
        with self.assertRaisesRegex(MemoryRuleError, "gone"):
            await self.memory.remove_story_language(GUILD_A, self.c, added.value.id, source=DM)
