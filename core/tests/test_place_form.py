"""A place's form (#4173 slice 6a): an area (none), water or a route. Read from the place's own
name, set by the DM in one tap, carried by merges and backups, and only ever on a place. Invented
names."""

from __future__ import annotations

import unittest
from typing import Any

import psycopg

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import place_levels as pl
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import CONFIRMED, MemoryRuleError, NewName
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import ONLY_PLACES
from dmbot.ui import names as ui
from dmbot.ui import place_size
from tests.test_memory_names import DM as NAMES_DM
from tests.test_memory_names import GUILD as NAMES_GUILD
from tests.test_memory_names import MANAGER, NamesTest
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


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.maer = await ui.save_name(self.memory, self.campaign, "Maer Dualdon", "place", [], [])

    async def screen(self, user: int = NAMES_DM) -> tuple[str, Any]:
        it = self.it(user)
        await place_size.show_sizes(it, self.campaign.id, self.maer.id)
        if not it.response.edited:
            return str(it.response.sent[-1][0]), None
        text, view = it.response.edited[-1]
        return str(text), view

    async def pick(self, view: Any, value: str, user: int = NAMES_DM) -> Any:
        view.form_pick._values = [value]  # (as Discord sets it on a pick)
        it = self.it(user)
        await view._form_picked(it)
        return it

    async def test_the_dm_marks_it_water_and_the_card_says_so(self) -> None:
        text, view = await self.screen()
        self.assertIn("land, water or a road", text)
        self.assertEqual([o.value for o in view.form_pick.options if o.default], [place_size.AREA])
        it = await self.pick(view, pl.WATER)
        card = str(it.response.edited[-1][0])
        self.assertIn("🧭 Noted: **Maer Dualdon** is water.", card)
        self.assertIn("🧭 **Kind of place:** water", card)
        self.assertNotIn("DMbot read this", card)  # the DM's own word
        found = await self.memory.entity(NAMES_GUILD, self.campaign.id, self.maer.id)
        assert found is not None
        self.assertEqual((found.form, found.form_from), (pl.WATER, BY_DM))

    async def test_land_takes_the_form_away(self) -> None:
        _text, view = await self.screen()
        await self.pick(view, pl.ROUTE)
        _text, view = await self.screen()
        await self.pick(view, place_size.AREA)
        found = await self.memory.entity(NAMES_GUILD, self.campaign.id, self.maer.id)
        assert found is not None
        self.assertIsNone(found.form)

    async def test_a_manager_who_is_not_a_dm_is_refused(self) -> None:
        _text, view = await self.screen()
        it = await self.pick(view, pl.WATER, user=MANAGER)
        self.assertEqual(it.response.sent[-1][0], place_size.ONLY_DM)
        found = await self.memory.entity(NAMES_GUILD, self.campaign.id, self.maer.id)
        assert found is not None
        self.assertIsNone(found.form)


class TheLine(unittest.TestCase):
    def test_land_has_no_line_and_a_read_one_says_so(self) -> None:
        self.assertEqual(place_size.form_line(None), "")
        self.assertEqual(place_size.form_line(pl.ROUTE), "\n🧭 **Kind of place:** a road or route")
        self.assertIn("DMbot read this", place_size.form_line(pl.WATER, read=True))


class TheRules(MemoryTest):
    async def test_the_table_refuses_a_bad_form(self) -> None:
        maer = await self.add("Maer Dualdon", type="place", status=CONFIRMED)
        for bad in ("form = 'water', form_from = NULL", "form = 'swamp', form_from = 'dm'",
                    "form = NULL, form_from = 'read'"):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaises(psycopg.errors.CheckViolation):
                async with self.db.guild(GUILD_A) as conn:
                    await conn.execute(f"UPDATE memory_entities SET {bad} WHERE id = %s", (maer,))

    async def test_undo_of_a_kind_change_is_refused_once_the_entry_changed_again(self) -> None:
        maer = await self.add("Maer Dualdon", type="place", status=CONFIRMED)
        await self.memory.set_place_form(GUILD_A, self.c, maer, pl.WATER, source=BY_DM)
        changed = await self.memory.set_entity_type(GUILD_A, self.c, maer, "npc", source=BY_DM)
        await self.memory.rename_entity(GUILD_A, self.c, maer, "Maera Dualdon", source=BY_DM)
        assert changed.batch is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD_A, self.c, changed.batch)
        found = await self.memory.entity(GUILD_A, self.c, maer)
        assert found is not None
        self.assertEqual((found.name, found.type, found.form), ("Maera Dualdon", "character", None))
