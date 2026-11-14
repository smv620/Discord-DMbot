"""The campaign's setting (#4173 slice 2a): one place where the whole story is, a smaller place with
no stated parent counted as inside it, the DM's one tap (one change, one Undo), merges, kind and
size changes, backups, and the website's action and door. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.checks import Nesting
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import SETTING_PLACE, SETTING_SIZE
from dmbot.ui import place_size
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest
from tests.test_web_api import ALICE
from tests.test_web_name_actions import Acting

LEVELS = {
    "faerun": "world",
    "eberron": "world",
    "dale": "region",
    "targos": "settlement",
    "sharn": "settlement",
    "inn": "building",
}


class TheRule(unittest.TestCase):
    def test_a_smaller_place_with_no_parent_is_inside_the_setting(self) -> None:
        nest = Nesting([], LEVELS, "faerun")
        self.assertEqual(nest.above("targos"), {"faerun"})
        self.assertTrue(nest.would_loop("faerun", "targos"))  # (Faerûn can't be in Targos now)

    def test_its_stated_parents_come_first_and_the_setting_above_them(self) -> None:
        nest = Nesting([("targos", "dale"), ("faerun", "plane")], {**LEVELS, "plane": "plane"},
                       "faerun")  # fmt: skip
        self.assertEqual(nest.above("targos"), {"dale", "faerun", "plane"})

    def test_a_place_of_unknown_size_is_inside_through_a_smaller_parent(self) -> None:
        levels = {k: v for k, v in LEVELS.items() if k != "inn"}
        nest = Nesting([("inn", "targos")], levels, "faerun")
        self.assertEqual(nest.above("inn"), {"targos", "faerun"})

    def test_something_saying_otherwise_wins(self) -> None:
        nest = Nesting([("sharn", "eberron")], LEVELS, "faerun")  # another world
        self.assertEqual(nest.above("sharn"), {"eberron"})
        self.assertEqual(nest.above("eberron"), set())  # as big as the setting: not inside it
        self.assertEqual(nest.above("faerun"), set())

    def test_a_place_nobody_sized_is_left_alone(self) -> None:
        nest = Nesting([], {"faerun": "world"}, "faerun")
        self.assertEqual(nest.above("somewhere"), set())

    def test_a_place_the_setting_sits_in_is_never_a_rival(self) -> None:
        # (reviewer: Faerûn in Toril, the setting Toril: Targos is in Faerûn's world, not another)
        levels = {**LEVELS, "toril": "world"}
        nest = Nesting([("faerun", "toril")], levels, "toril")
        self.assertTrue(nest.related("faerun", "targos"))
        self.assertTrue(Nesting([("faerun", "toril")], levels).related("faerun", "targos"))

    def test_the_setting_stated_inside_a_place_never_puts_that_place_in_itself(self) -> None:
        nest = Nesting([("faerun", "dale")], LEVELS, "faerun")  # (bad data: a world in a region)
        self.assertNotIn("dale", nest.above("dale"))

    def test_no_setting_or_one_with_no_size_changes_nothing(self) -> None:
        self.assertEqual(Nesting([], LEVELS).above("targos"), set())
        self.assertEqual(Nesting([], {"targos": "settlement"}, "faerun").above("targos"), set())


class TheWords(unittest.TestCase):
    def test_the_card_line_says_what_it_does(self) -> None:
        self.assertIn("Campaign setting", place_size.setting_line(True))
        self.assertEqual(place_size.setting_line(False), "")


class TheStore(MemoryTest):
    async def place(self, name: str, level: str | None = None) -> str:
        made = await self.add(name, type="place", status=CONFIRMED)
        if level is not None:
            await self.memory.set_place_level(GUILD_A, self.c, made, level, source=BY_DM)
        return made

    async def setting_id(self, guild: int = GUILD_A, campaign: str = "") -> str | None:
        found = await self.memory.setting(guild, campaign or self.c)
        return None if found is None else found.id

    async def test_the_dm_sets_changes_and_clears_it(self) -> None:
        faerun, plane = await self.place("Faerûn", "world"), await self.place("Prime", "plane")
        await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        self.assertEqual(await self.setting_id(), faerun)
        await self.memory.set_setting(GUILD_A, self.c, plane, source=BY_DM)
        self.assertEqual(await self.setting_id(), plane)  # one at most: Faerûn let go
        await self.memory.set_setting(GUILD_A, self.c, None, source=BY_DM)
        self.assertIsNone(await self.setting_id())

    async def test_one_change_so_one_undo_and_the_same_again_writes_nothing(self) -> None:
        faerun = await self.place("Faerûn", "world")
        written = await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        before = await self.memory.version(GUILD_A, self.c)
        again = await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        self.assertIsNone(again.batch)
        self.assertEqual(await self.memory.version(GUILD_A, self.c), before)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertIsNone(await self.setting_id())

    async def test_only_a_confirmed_place_a_region_or_bigger_and_only_the_dm(self) -> None:
        town = await self.place("Targos", "settlement")
        with self.assertRaisesRegex(MemoryRuleError, SETTING_SIZE):
            await self.memory.set_setting(GUILD_A, self.c, town, source=BY_DM)
        unsized = await self.place("Somewhere")
        with self.assertRaisesRegex(MemoryRuleError, SETTING_SIZE):
            await self.memory.set_setting(GUILD_A, self.c, unsized, source=BY_DM)
        mira = await self.add("Mira", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, SETTING_PLACE):
            await self.memory.set_setting(GUILD_A, self.c, mira, source=BY_DM)
        faerun = await self.place("Faerûn", "world")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_setting(GUILD_A, self.c, faerun, source="cleaner")
        self.assertIsNone(await self.setting_id())

    async def test_made_too_small_or_not_a_place_it_stops_being_the_setting(self) -> None:
        faerun = await self.place("Faerûn", "world")
        await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        await self.memory.set_place_level(GUILD_A, self.c, faerun, "region", source=BY_DM)
        self.assertEqual(await self.setting_id(), faerun)  # a region is big enough
        await self.memory.set_place_level(GUILD_A, self.c, faerun, "settlement", source=BY_DM)
        self.assertIsNone(await self.setting_id())
        dale = await self.place("Icewind Dale", "region")
        await self.memory.set_setting(GUILD_A, self.c, dale, source=BY_DM)
        await self.memory.set_entity_type(GUILD_A, self.c, dale, "faction", source=BY_DM)
        self.assertIsNone(await self.setting_id())

    async def test_a_merge_keeps_it_on_the_kept_place(self) -> None:
        keep, gone = await self.place("Faerûn", "world"), await self.place("Faerun", "world")
        await self.memory.set_setting(GUILD_A, self.c, gone, source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source=BY_DM, dm_said_same=True)
        self.assertEqual(await self.setting_id(), keep)

    async def seen_in(self, who: str, *places: str) -> set[str]:
        for place in places:  # the DM's own moves, one after the other
            await self.relate(
                who, "located_in", place, status=CONFIRMED, detail="was last seen in",
                by_hand=True,
            )  # fmt: skip
        return {
            r.object_id
            for r in await self.memory.relations(GUILD_A, self.c, entity_id=who)
            if r.predicate == "located_in" and r.subject_id == who and r.to_session_at is None
        }

    async def test_the_nesting_reads_it(self) -> None:
        # (only the setting changes this: a town with no stated parent is in Faerûn, so a move
        # there from Eberron, another world, ends Eberron)
        faerun = await self.place("Faerûn", "world")
        eberron = await self.place("Eberron", "world")
        town = await self.place("Targos", "settlement")
        mira, wit = (
            await self.add("Mira", status=CONFIRMED),
            await self.add("Wit", status=CONFIRMED),
        )
        self.assertEqual(await self.seen_in(wit, eberron, town), {eberron, town})  # no setting
        await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        self.assertEqual(await self.seen_in(mira, eberron, town), {town})

    async def test_a_merge_never_leaves_a_small_place_the_setting(self) -> None:
        keep = await self.place("Faerun Town", "settlement")
        gone = await self.place("Faerûn", "world")
        await self.memory.set_setting(GUILD_A, self.c, gone, source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source=BY_DM, dm_said_same=True)
        self.assertIsNone(await self.setting_id())

    async def test_remove_on_a_place_that_is_not_the_setting_writes_nothing(self) -> None:
        faerun, eberron = await self.place("Faerûn", "world"), await self.place("Eberron", "world")
        await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        stale = await self.memory.set_setting(GUILD_A, self.c, None, source=BY_DM, only_if=eberron)
        self.assertIsNone(stale.batch)
        self.assertEqual(await self.setting_id(), faerun)

    async def test_a_backup_carries_it_and_two_settings_are_damaged(self) -> None:
        faerun = await self.place("Faerûn", "world")
        await self.memory.set_setting(GUILD_A, self.c, faerun, source=BY_DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        self.assertIsNotNone(await self.setting_id(GUILD_B, restored.id))
        await self.place("Toril", "world")
        await self.add("Mira", status=CONFIRMED)
        data = await self.campaigns.export(GUILD_A, self.c)
        for marked in ({"Faerûn", "Toril"}, {"Mira"}):  # two settings; a setting that's no place
            damaged = [
                {**r, "setting": r.get("name") in marked} if r["table"] == "entity" else r
                for r in data["sections"]["memory"]
            ]
            with self.assertRaises(CampaignError, msg=str(marked)):
                await self.campaigns.import_backup(
                    GUILD_B, {**data, "sections": {**data["sections"], "memory": damaged}}, DM
                )


class TheWebsite(Acting):
    async def setting_id(self) -> str | None:
        found = await self.memory.setting(self.g, self.one.id)
        return None if found is None else found.id

    async def listed(self, entity: str) -> Any:
        return next(n for n in (await self.page())["names"] if n["id"] == entity)

    async def door(self, entity: str, text: str = "", flag: bool | None = True) -> Any:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, entity, "place-setting", "", "", text, flag),
            )
            got = await cur.fetchone()
        assert got is not None
        return got["r"]

    async def test_a_dm_makes_it_the_setting_and_clears_it(self) -> None:
        await self.memory.set_place_level(self.g, self.one.id, self.keep, "region", source=BY_DM)
        await self.queue(self.keep, action="place-setting", setting=True)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.setting_id(), self.keep)
        self.assertTrue((await self.listed(self.keep))["setting"])
        await self.queue(self.keep, action="place-setting", setting=False)
        await self.apply_all()
        self.assertIsNone(await self.setting_id())
        self.assertFalse((await self.listed(self.keep))["setting"])

    async def test_not_the_setting_on_another_place_clears_nothing(self) -> None:
        await self.memory.set_place_level(self.g, self.one.id, self.keep, "region", source=BY_DM)
        await self.memory.set_setting(self.g, self.one.id, self.keep, source=BY_DM)
        other = await self.memory.add_entity(
            self.g, self.one.id, type="place", name="Elsewhere", source=BY_DM, status=CONFIRMED
        )
        await self.queue(other.value.id, action="place-setting", setting=False)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.setting_id(), self.keep)

    async def test_a_place_too_small_or_a_thing_is_refused(self) -> None:
        await self.queue(self.keep, action="place-setting", setting=True)  # no size yet
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])
        await self.queue(self.sword, action="place-setting", setting=True)
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])
        self.assertIsNone(await self.setting_id())

    async def test_the_body_needs_a_yes_or_no(self) -> None:
        for bad in (None, "yes", 1):
            refused = await self.refused_action(self.keep, action="place-setting", setting=bad)
            self.assertEqual(refused, "bad_action", bad)

    async def test_the_door_wants_a_flag_and_no_text(self) -> None:
        self.assertEqual((await self.door(self.keep))["status"], "ok")
        self.assertEqual((await self.door(self.keep, flag=False))["status"], "ok")
        self.assertEqual((await self.door(self.keep, flag=None))["status"], "bad_text")
        self.assertEqual((await self.door(self.keep, text="world"))["status"], "bad_text")

    async def test_the_bot_refuses_a_non_dm_sender(self) -> None:
        await self.memory.set_place_level(self.g, self.one.id, self.keep, "region", source=BY_DM)
        await self.queue(self.keep, action="place-setting", setting=True)
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertIsNone(await self.setting_id())
