"""A place's form (#4173 slice 6a): an area (none), water or a route. Read from the place's own
name, set by the DM in one tap, carried by merges and backups, and only ever on a place. Invented
names."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import place_levels as pl
from dmbot.memory.models import CONFIRMED, MemoryRuleError, NewName
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import ONLY_PLACES
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest


class TheName(unittest.TestCase):
    def test_water_and_route_words_in_the_name(self) -> None:
        for name in ("Maer Dualdon", "Lac Dinneshere", "the Sea of Moving Ice", "Bay of Chult",
                     "Redrun River", "the Trackless Sea"):  # fmt: skip
            with self.subTest(name=name):
                self.assertEqual(pl.read_form(name), pl.WATER)
        for name in ("the Northern Trail", "the Ten-Towns Trade Route", "the King's Highway"):
            with self.subTest(name=name):
                self.assertEqual(pl.read_form(name), pl.ROUTE)

    def test_names_that_say_no_form(self) -> None:
        for name in (
            "Targos",
            "Lake Town",  # a name that says a size says no form
            "the Black Road",  # a street still: a town has roads
            "Lord of the Lake",  # only the part before "of" counts
            "Seaside",  # a word is matched whole
        ):
            with self.subTest(name=name):
                self.assertIsNone(pl.read_form(name))


class TheStore(MemoryTest):
    async def place(self, name: str) -> str:
        return await self.add(name, type="place", status=CONFIRMED)

    async def form(self, entity_id: str) -> tuple[str | None, str | None]:
        found = await self.memory.entity(GUILD_A, self.c, entity_id)
        assert found is not None
        return found.form, found.form_from

    async def test_the_dm_sets_changes_and_clears_it(self) -> None:
        maer = await self.place("Maer Dualdon")
        self.assertEqual(await self.form(maer), (None, None))
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        self.assertEqual(await self.form(maer), (pl.WATER, BY_DM))
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.ROUTE, source=BY_DM)
        self.assertEqual(await self.form(maer), (pl.ROUTE, BY_DM))
        await self.memory.set_place_form(GUILD_A, self.c, maer, None, source=BY_DM)
        self.assertEqual(await self.form(maer), (None, None))

    async def test_one_change_so_one_undo_and_the_same_again_writes_nothing(self) -> None:
        maer = await self.place("Maer Dualdon")
        written = await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        before = await self.memory.version(GUILD_A, self.c)
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        self.assertEqual(await self.memory.version(GUILD_A, self.c), before)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.form(maer), (None, None))

    async def test_only_a_place_only_a_form_word_only_the_dm(self) -> None:
        mira = await self.add("Mira", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, ONLY_PLACES):
            await self.memory.set_place_form(GUILD_A, self.c, mira, pl.WATER, source=BY_DM)
        maer = await self.place("Maer Dualdon")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_place_form(GUILD_A, self.c, maer, "swamp", source=BY_DM)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source="cleaner")
        self.assertEqual(await self.form(maer), (None, None))

    async def test_a_new_name_keeps_the_read_form_only_for_a_place(self) -> None:
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("Maer Dualdon", "place", CONFIRMED, form=pl.WATER),
                NewName("Mira", "character", CONFIRMED, form=pl.WATER),
            ],
            source=BY_DM,
        )
        maer, mira = written.value
        assert maer is not None and mira is not None
        self.assertEqual(await self.form(maer), (pl.WATER, pl.READ))
        self.assertEqual(await self.form(mira), (None, None))
        read = await self.memory.entity(GUILD_A, self.c, maer)
        assert read is not None
        self.assertTrue(read.form_read)
        # the DM saying the same form makes it theirs: no longer a read
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        self.assertEqual(await self.form(maer), (pl.WATER, BY_DM))

    async def test_a_merge_takes_the_dms_form_over_dmbots_read(self) -> None:
        written = await self.memory.add_names(
            GUILD_A, self.c, [NewName("Maer Dualdon", "place", CONFIRMED, form=pl.WATER)],
            source=BY_DM,
        )  # fmt: skip
        read = written.value[0]
        said = await self.place("Maer Dualdon Road")
        assert read is not None
        await self.memory.set_place_form(GUILD_A, self.c, said, pl.ROUTE, source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, read, said, source=BY_DM, dm_said_same=True)
        self.assertEqual(await self.form(read), (pl.ROUTE, BY_DM))

    async def test_a_place_made_a_person_loses_it_and_undo_brings_it_back(self) -> None:
        maer = await self.place("Maer Dualdon")
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        changed = await self.memory.set_entity_type(GUILD_A, self.c, maer, "npc", source=BY_DM)
        self.assertEqual(await self.form(maer), (None, None))
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch)
        self.assertEqual(await self.form(maer), (pl.WATER, BY_DM))

    async def test_a_backup_carries_it(self) -> None:
        maer = await self.place("Maer Dualdon")
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual(
            [(r["form"], r["form_from"]) for r in copy["memory_entities"]], [(pl.WATER, BY_DM)]
        )

    async def test_a_damaged_backup_is_refused(self) -> None:
        await self.add("Mira", status=CONFIRMED)
        await self.place("Maer Dualdon")
        for damage, on in (
            ({"form": pl.WATER, "form_from": BY_DM}, "Mira"),  # a person with a form
            ({"form": pl.WATER, "form_from": None}, "Maer Dualdon"),  # a form with no "from"
            ({"form": "swamp", "form_from": BY_DM}, "Maer Dualdon"),  # a form off the list
        ):
            with self.subTest(damage=damage):
                data = await self.campaigns.export(GUILD_A, self.c)
                damaged = [
                    {**r, **damage} if r["table"] == "entity" and r["name"] == on else r
                    for r in data["sections"]["memory"]
                ]
                data = {**data, "sections": {**data["sections"], "memory": damaged}}
                with self.assertRaises(CampaignError):
                    await self.campaigns.import_backup(GUILD_B, data, DM)
