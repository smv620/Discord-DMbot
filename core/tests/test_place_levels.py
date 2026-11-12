"""How big a place is (#4173 slice 1): the words that give a size, the DM's one tap (one change,
one Undo, places only), merges and backups carrying it, and the rule it changes: someone in a
building and in a town at once is in one place, not two. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import place_levels as pl
from dmbot.memory.checks import TOO_MANY, Nesting, check_relation
from dmbot.memory.models import CONFIRMED, MemoryRuleError, NewName
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import ONLY_PLACES
from tests.test_memory_states import ONTO, fact
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest


class TheWords(unittest.TestCase):
    def test_the_name_itself_says_it(self) -> None:
        self.assertEqual(pl.read_level("Market Square"), "street")
        self.assertEqual(pl.read_level("The Yawning Portal Inn"), "building")
        self.assertEqual(pl.read_level("Icewind Dale"), "region")
        self.assertEqual(pl.read_level("The Common Room"), "room")  # the longer phrase first

    def test_names_that_say_no_size(self) -> None:
        # (Supervisor on #4186: two Frostmaiden places the name words got wrong)
        self.assertIsNone(pl.read_level("Spine of the World"))  # only the part before "of"
        self.assertIsNone(pl.read_level("Ten-Towns"))  # only through a plural s
        self.assertIsNone(pl.read_level("Sea of Moving Ice"))

    def test_names_that_keep_their_size(self) -> None:
        for name, level in (
            ("Tower of Kord", "building"),
            ("Temple of the Morninglord", "building"),
            ("Kingdom of Many Towns", "region"),
            ("Lands of Intrigue", "region"),  # a word that is itself on the list
            ("The Docks", "district"),
            ("Stables", "building"),
            ("Icewind Dale", "region"),
            ("Market Square", "street"),
        ):
            self.assertEqual(pl.read_level(name), level, name)

    def test_the_last_level_word_in_a_name_wins(self) -> None:
        self.assertEqual(pl.read_level("Castle Street"), "street")
        self.assertEqual(pl.read_level("Street Castle"), "building")

    def test_the_words_beside_the_name_say_it(self) -> None:
        self.assertEqual(pl.read_level("Targos", "They rode to the town of Targos."), "settlement")
        self.assertEqual(
            pl.read_level("Targos", "Targos, a fishing town, lies east."), "settlement"
        )
        self.assertEqual(
            pl.read_level("Three Flags Sailing", "a tavern called the Three Flags Sailing"),
            "building",
        )
        self.assertEqual(
            pl.read_level("Lonelywood", "the woodland village of Lonelywood"), "settlement"
        )

    def test_only_the_set_shapes_say_it(self) -> None:
        self.assertEqual(pl.read_level("Targos", "Targos, a town of fishers."), "settlement")
        self.assertEqual(
            pl.read_level("Kord's Rest", "the old tower called Kord's Rest"), "building"
        )
        # a level word beside a name in any other shape says nothing (reviewer, #4173)
        for name, text in (
            ("Targos", "They took the road to Targos at dawn."),
            ("Targos", "The guards keep Targos safe."),
            ("Targos", "They reached Targos. The study of maps"),
            ("Waterdeep", "Lords of the land rule Waterdeep"),
            ("Targos", "In this world Targos stands alone"),
            ("Brindle's", "In the city they found the tavern Brindle's."),
        ):
            self.assertIsNone(pl.read_level(name, text), text)

    def test_a_name_is_matched_whole(self) -> None:
        self.assertIsNone(pl.read_level("Targ", "Targos, a fishing town."))

    def test_no_word_means_no_level(self) -> None:
        self.assertIsNone(pl.read_level("Targos"))
        self.assertIsNone(pl.read_level("Targos", "They rode on to Targos at dawn."))
        self.assertIsNone(pl.read_level("Targos", "Nothing about it here."))
        self.assertIsNone(pl.read_level("", "the town of"))

    def test_a_word_too_far_away_says_nothing(self) -> None:
        text = "The town was quiet that night and far away in the north lay Targos."
        self.assertIsNone(pl.read_level("Targos", text))
        self.assertIsNone(pl.read_level("Targos", "the town of. Targos"))  # not across a stop

    def test_harbour_is_left_out(self) -> None:
        self.assertIsNone(pl.read_level("Minstral Harbor"))

    def test_the_texts_are_tried_in_turn(self) -> None:
        self.assertEqual(pl.read_level("Targos", "no word", "the city of Targos"), "settlement")

    def test_sizes(self) -> None:
        self.assertTrue(pl.bigger("settlement", "building"))
        self.assertFalse(pl.bigger("room", "building"))
        self.assertTrue(pl.different("settlement", "building"))
        self.assertFalse(pl.different("building", "building"))
        self.assertFalse(pl.different("building", None))
        self.assertFalse(pl.different(None, None))
        self.assertEqual(set(pl.LABELS), set(pl.LEVELS))


class TheRule(unittest.TestCase):
    def check(self, levels: dict[str, str]) -> list[str]:
        nest = Nesting((), levels)
        in_bank = fact(1, "mira", "located_in", "bank")
        in_town = fact(2, "mira", "located_in", "town")
        return [
            p.kind
            for p in check_relation(ONTO, in_town, "character", "place", [in_bank], nesting=nest)
        ]

    def test_a_building_and_a_town_are_never_rivals(self) -> None:
        self.assertEqual(self.check({"bank": "building", "town": "settlement"}), [])

    def test_two_buildings_are_rivals(self) -> None:
        self.assertEqual(self.check({"bank": "building", "town": "building"}), [TOO_MANY])

    def test_a_size_nobody_knows_keeps_the_old_rule(self) -> None:
        self.assertEqual(self.check({"bank": "building"}), [TOO_MANY])
        self.assertEqual(self.check({}), [TOO_MANY])

    def test_a_building_known_to_be_in_another_town_is_a_rival(self) -> None:
        levels = {"bank": "building", "town": "settlement", "other": "settlement"}
        nest = Nesting([("bank", "other")], levels)
        self.assertFalse(nest.related("bank", "town"))
        self.assertTrue(nest.related("bank", "other"))
        self.assertTrue(Nesting([("bank", "town")], levels).related("bank", "town"))
        # a district elsewhere doesn't make it a rival of a town: only a place of the town's size
        nest = Nesting([("bank", "docks")], {**levels, "docks": "district"})
        self.assertTrue(nest.related("bank", "town"))


SEEN = "was last seen in"


class TheStore(MemoryTest):
    async def place(self, name: str) -> str:
        return await self.add(name, type="place", status=CONFIRMED)

    async def level(self, entity_id: str, guild: int = GUILD_A, campaign: str = "") -> str | None:
        found = await self.memory.entity(guild, campaign or self.c, entity_id)
        assert found is not None
        return found.place_level

    async def test_the_dm_sets_changes_and_clears_it(self) -> None:
        targos = await self.place("Targos")
        self.assertIsNone(await self.level(targos))
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        self.assertEqual(await self.level(targos), "settlement")
        await self.memory.set_place_level(GUILD_A, self.c, targos, "region", source=BY_DM)
        self.assertEqual(await self.level(targos), "region")
        await self.memory.set_place_level(GUILD_A, self.c, targos, None, source=BY_DM)
        self.assertIsNone(await self.level(targos))

    async def test_one_change_so_one_undo(self) -> None:
        targos = await self.place("Targos")
        written = await self.memory.set_place_level(
            GUILD_A, self.c, targos, "settlement", source=BY_DM
        )
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertIsNone(await self.level(targos))

    async def test_the_same_size_again_writes_nothing(self) -> None:
        targos = await self.place("Targos")
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        before = await self.memory.version(GUILD_A, self.c)
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        self.assertEqual(await self.memory.version(GUILD_A, self.c), before)

    async def test_only_a_place_only_a_size_word_only_the_dm(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, ONLY_PLACES):
            await self.memory.set_place_level(GUILD_A, self.c, mira, "building", source=BY_DM)
        targos = await self.place("Targos")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_place_level(GUILD_A, self.c, targos, "huge", source=BY_DM)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_place_level(GUILD_A, self.c, targos, "building", source="cleaner")
        self.assertIsNone(await self.level(targos))

    async def test_a_new_name_keeps_the_read_size_only_for_a_place(self) -> None:
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("Targos", "place", CONFIRMED, place_level="settlement"),
                NewName("Mira", "character", CONFIRMED, place_level="building"),
            ],
            source=BY_DM,
        )
        targos, mira = written.value
        assert targos is not None and mira is not None
        self.assertEqual(await self.level(targos), "settlement")
        self.assertIsNone(await self.level(mira))
        read = await self.memory.entity(GUILD_A, self.c, targos)
        assert read is not None
        self.assertEqual(read.place_level_from, "read")  # DMbot's read, shown as one
        self.assertTrue(read.place_level_read)
        # the DM saying the same size makes it theirs: a change, and no longer a read
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        said = await self.memory.entity(GUILD_A, self.c, targos)
        assert said is not None
        self.assertEqual((said.place_level, said.place_level_from), ("settlement", "dm"))
        self.assertFalse(said.place_level_read)
        cleared = await self.memory.set_place_level(GUILD_A, self.c, targos, None, source=BY_DM)
        self.assertIsNone(cleared.value.place_level_from)

    async def test_a_merge_keeps_the_size_either_had(self) -> None:
        keep, gone = await self.place("Targos"), await self.place("Targos Town")
        await self.memory.set_place_level(GUILD_A, self.c, gone, "settlement", source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source=BY_DM, dm_said_same=True)
        self.assertEqual(await self.level(keep), "settlement")

    async def test_a_merge_takes_the_dms_size_over_dmbots_read(self) -> None:
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [NewName("Bryn Shander", "place", CONFIRMED, place_level="district")],
            source=BY_DM,
        )
        read = written.value[0]
        said = await self.place("Bryn Shander Town")
        assert read is not None
        await self.memory.set_place_level(GUILD_A, self.c, said, "settlement", source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, read, said, source=BY_DM, dm_said_same=True)
        kept = await self.memory.entity(GUILD_A, self.c, read)
        assert kept is not None
        self.assertEqual((kept.place_level, kept.place_level_from), ("settlement", "dm"))

    async def test_a_place_made_a_person_loses_it_and_undo_brings_it_back(self) -> None:
        # (one batch changes the row twice, the kind and then the size: Undo takes back both)
        targos = await self.place("Targos")
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        changed = await self.memory.set_entity_type(GUILD_A, self.c, targos, "npc", source=BY_DM)
        self.assertIsNone(await self.level(targos))
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch)
        self.assertEqual(await self.level(targos), "settlement")

    async def test_a_backup_carries_it(self) -> None:
        targos = await self.place("Targos")
        await self.memory.set_place_level(GUILD_A, self.c, targos, "settlement", source=BY_DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual([r["place_level"] for r in copy["memory_entities"]], ["settlement"])

    async def test_a_backup_with_a_size_on_a_person_is_refused(self) -> None:
        await self.add("Mira", status=CONFIRMED)
        data = await self.campaigns.export(GUILD_A, self.c)
        damaged = [
            {**r, "place_level": "building", "place_level_from": "dm"}
            if r["table"] == "entity"
            else r
            for r in data["sections"]["memory"]
        ]
        data = {**data, "sections": {**data["sections"], "memory": damaged}}
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_someone_in_the_bank_and_in_the_town_is_in_one_place(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        bank = await self.place("The Copper Bank")
        town = await self.place("Waterdeep")
        await self.memory.set_place_level(GUILD_A, self.c, bank, "building", source=BY_DM)
        await self.memory.set_place_level(GUILD_A, self.c, town, "settlement", source=BY_DM)
        await self.relate(mira, "located_in", bank, status=CONFIRMED)
        written = await self.relate(mira, "located_in", town, source="cleaner", confidence=0.7)
        self.assertEqual([f.kind for f in written.value[1]], [])
        live = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, entity_id=mira)
            if r.predicate == "located_in" and r.to_session_at is None
        ]
        self.assertEqual(len(live), 2)  # the bank is not ended for the town

    async def test_a_new_kind_that_is_not_a_place_drops_the_size(self) -> None:
        bank = await self.place("The Copper Bank")
        await self.memory.set_place_level(GUILD_A, self.c, bank, "building", source=BY_DM)
        await self.memory.set_entity_type(GUILD_A, self.c, bank, "faction", source=BY_DM)
        found = await self.memory.entity(GUILD_A, self.c, bank)
        assert found is not None
        self.assertEqual((found.place_level, found.place_level_from), (None, None))
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)  # not refused

    async def live_places(self, who: str) -> set[str]:
        return {
            r.object_id
            for r in await self.memory.relations(GUILD_A, self.c, entity_id=who)
            if r.predicate == "located_in" and r.subject_id == who and r.to_session_at is None
        }

    async def sized(self, name: str, level: str) -> str:
        place = await self.place(name)
        await self.memory.set_place_level(GUILD_A, self.c, place, level, source=BY_DM)
        return place

    async def test_a_move_into_a_building_keeps_the_town(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        town = await self.sized("Waterdeep", "settlement")
        bakery = await self.sized("Hilda's Bakery", "building")
        for place in (town, bakery):  # the DM's own, one after the other: the newest stands
            await self.relate(
                mira, "located_in", place, status=CONFIRMED, detail=SEEN, by_hand=True
            )
        self.assertEqual(await self.live_places(mira), {town, bakery})

    async def test_a_move_into_a_building_in_another_town_ends_the_town(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        town = await self.sized("Waterdeep", "settlement")
        other = await self.sized("Neverwinter", "settlement")
        bakery = await self.sized("Hilda's Bakery", "building")
        await self.relate(bakery, "located_in", other, status=CONFIRMED)
        for place in (town, bakery):  # the DM's own, one after the other: the newest stands
            await self.relate(
                mira, "located_in", place, status=CONFIRMED, detail=SEEN, by_hand=True
            )
        self.assertNotIn(town, await self.live_places(mira))
        self.assertIn(bakery, await self.live_places(mira))

    async def test_two_buildings_are_still_two_places(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        bank, bakery = await self.place("The Copper Bank"), await self.place("Hilda's Bakery")
        for p in (bank, bakery):
            await self.memory.set_place_level(GUILD_A, self.c, p, "building", source=BY_DM)
        await self.relate(mira, "located_in", bank, status=CONFIRMED)
        written = await self.relate(mira, "located_in", bakery, source="cleaner", confidence=0.7)
        self.assertEqual([f.kind for f in written.value[1]], [TOO_MANY])


class TheMenu(unittest.TestCase):
    def test_the_card_line_says_the_size_in_plain_words(self) -> None:
        from dmbot.ui import place_size

        self.assertEqual(place_size.size_line("settlement"), "\n📏 **Size:** a town or city")
        self.assertEqual(
            place_size.size_line("settlement", read=True),
            "\n📏 **Size:** a town or city (DMbot read this; tap 📏 Size to change)",
        )
        self.assertEqual(place_size.size_line(None), "")

    def test_the_question_says_who_gave_the_size(self) -> None:
        from dmbot.ui import place_size

        self.assertIn(
            "DMbot read: a building. Is that right?", place_size.ask_text("X", "building", True)
        )
        self.assertIn("You said: a building.", place_size.ask_text("X", "building"))
        self.assertIn("No size yet.", place_size.ask_text("X", None))

    def test_every_size_then_not_sure_with_the_current_one_chosen(self) -> None:
        from dmbot.ui import place_size

        options = place_size.options("building")
        self.assertEqual([o.value for o in options], [*pl.LEVELS, place_size.NOT_SURE])
        self.assertEqual([o.value for o in options if o.default], ["building"])
        self.assertEqual(
            [o.value for o in place_size.options(None) if o.default], [place_size.NOT_SURE]
        )
        self.assertNotIn(place_size.NOT_SURE, pl.LEVELS)
