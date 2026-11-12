"""A place's land, climate and who rules it (#4256, MapBot layers slice 2): the word lists and the
readers' set phrases (pure), the DM's word and DMbot's read in the store, reading up the place tree,
the players' rule, merges, kind changes and backups. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

GROUPS = ("Council of Speakers", "Arcane Brotherhood")


class TheWords(unittest.TestCase):
    def test_control_needs_a_group_this_campaign_has(self) -> None:
        text = "Bryn Shander, ruled by the Council of Speakers, trades in scrimshaw."
        self.assertEqual(
            mf.read_facts("Bryn Shander", text, GROUPS),
            [mf.Found(mf.CONTROL, "Council of Speakers", "rules")],
        )
        self.assertEqual(mf.read_facts("Bryn Shander", "Bryn Shander, ruled by fear.", GROUPS), [])
        occupied = mf.read_facts("Luskan", "Luskan, occupied by Arcane Brotherhood.", GROUPS)
        self.assertEqual(occupied, [mf.Found(mf.CONTROL, "Arcane Brotherhood", "occupies")])

    def test_a_word_before_of_or_after_the_comma(self) -> None:
        self.assertEqual(
            mf.read_facts("Icewind Dale", "They crossed the frozen north of Icewind Dale.", ()),
            [mf.Found(mf.CLIMATE, "arctic")],
        )
        self.assertEqual(
            mf.read_facts("Calimshan", "the desert kingdom of Calimshan", ()),
            [mf.Found(mf.TERRAIN, "desert")],
        )
        self.assertEqual(
            mf.read_facts("Chult", "Chult, a sweltering land of jungle.", ()),
            [mf.Found(mf.CLIMATE, "hot")],
        )

    def test_anything_else_says_nothing(self) -> None:
        for text in (
            "Icewind Dale is frozen.",  # not one of the set shapes
            "The frozen road. North of Icewind Dale.",  # across a full stop
            "the north of Icewind Dale",  # no list word
            "Snow fell on Targos.",
            # (the reviewer on #4256: a phrase about something else in the sentence)
            "Targos traded with Luskan, ruled by the Council of Speakers.",
            "Targos sent soldiers to the fort held by the Arcane Brotherhood.",
            "a wooded hill east of Targos",
            "A sandy beach south of Targos",
        ):
            with self.subTest(text=text):
                name = "Targos" if "Targos" in text else "Icewind Dale"
                self.assertEqual(mf.read_facts(name, text, GROUPS), [])

    def test_which_places_may_carry_which(self) -> None:
        self.assertTrue(mf.allowed_on(mf.TERRAIN, form=None, level="settlement"))
        self.assertFalse(mf.allowed_on(mf.TERRAIN, form="water", level=None))
        self.assertTrue(mf.allowed_on(mf.CLIMATE, form=None, level="region"))
        self.assertTrue(mf.allowed_on(mf.CLIMATE, form=None, level=None))
        self.assertFalse(mf.allowed_on(mf.CLIMATE, form=None, level="settlement"))
        self.assertTrue(mf.allowed_on(mf.CONTROL, form="route", level="building"))

    def test_when_a_new_value_meets_an_old_one(self) -> None:
        self.assertEqual(mf.meet(mf.TERRAIN, mf.FROM_TEXT, mf.READ, mf.FROM_DM, False), mf.CORRECT)
        self.assertEqual(mf.meet(mf.TERRAIN, mf.FROM_TEXT, mf.READ, mf.FROM_TEXT, False), mf.NOTE)
        self.assertEqual(mf.meet(mf.TERRAIN, mf.FROM_DM, mf.CONFIRMED, mf.FROM_TEXT, True), mf.NOTE)
        self.assertEqual(mf.meet(mf.CLIMATE, mf.FROM_TEXT, mf.READ, mf.FROM_TEXT, True), mf.CLOSE)

    def test_reading_up_takes_the_nearest_and_stops_at_a_plane(self) -> None:
        def fact(place: str, layer: str, value: str) -> mf.Fact:
            return mf.Fact(f"{place}{layer}", place, layer, value, "", "", "", mf.CONFIRMED,
                           mf.FROM_DM, True)  # fmt: skip

        own = {
            "dale": [fact("dale", mf.CLIMATE, "arctic")],
            "towns": [fact("towns", mf.TERRAIN, "hill")],
            "plane": [fact("plane", mf.CLIMATE, "temperate")],
        }
        inside = {"bryn": ["towns"], "towns": ["dale"], "dale": ["plane"], "pocket": ["plane"]}
        levels = {"plane": "plane", "dale": "region"}
        held = mf.read_up("bryn", mf.CLIMATE, own, inside, levels)
        assert held is not None
        self.assertEqual((held.from_id, held.facts[0].value), ("dale", "arctic"))
        land = mf.read_up("bryn", mf.TERRAIN, own, inside, levels)
        assert land is not None
        self.assertEqual(land.from_id, "towns")
        self.assertIsNone(mf.read_up("pocket", mf.CLIMATE, own, inside, levels))  # (the plane's)
        loop = {"a": ["b"], "b": ["a"]}
        self.assertIsNone(mf.read_up("a", mf.CLIMATE, {}, loop, {}))


class TheStore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.dale = await self.add("Icewind Dale", type="place", status=CONFIRMED)
        self.towns = await self.add("Ten-Towns", type="place", status=CONFIRMED)
        self.bryn = await self.add("Bryn Shander", type="place", status=CONFIRMED)
        self.council = await self.add("Council of Speakers", type="faction", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, self.dale, "region", source=BY_DM)
        await self.memory.set_place_level(GUILD_A, self.c, self.bryn, "settlement", source=BY_DM)
        for inner, outer in ((self.bryn, self.towns), (self.towns, self.dale)):
            await self.memory.add_relation(
                GUILD_A, self.c, inner, "located_in", outer, source=BY_DM, confidence=1.0,
                status=CONFIRMED,
            )  # fmt: skip

    async def facts(self, place: str, viewer: gate.Viewer | None = None) -> list[mf.Fact]:
        got = await self.memory.map_facts(GUILD_A, self.c, [place], viewer or gate.Viewer.the_dm())
        return got.get(place, [])

    async def test_the_dm_sets_changes_and_clears_one_undo_each(self) -> None:
        set_ = await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.TERRAIN, "hill", how="mostly", source=BY_DM
        )
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.TERRAIN, "urban", source=BY_DM
        )
        (now,) = await self.facts(self.bryn)
        self.assertEqual((now.value, now.status, now.source), ("urban", mf.CONFIRMED, mf.FROM_DM))
        await self.memory.set_map_fact(GUILD_A, self.c, self.bryn, mf.TERRAIN, None, source=BY_DM)
        self.assertEqual(await self.facts(self.bryn), [])
        assert set_.batch is not None
        with self.assertRaises(MemoryRuleError):  # (changed since: undo the later one first)
            await self.memory.undo(GUILD_A, self.c, set_.batch)

    async def test_what_each_place_may_carry(self) -> None:
        lake = await self.add("Maer Dualdon", type="place", status=CONFIRMED)
        await self.memory.set_place_form(GUILD_A, self.c, lake, "water", source=BY_DM)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_map_fact(GUILD_A, self.c, lake, mf.TERRAIN, "hill", source=BY_DM)
        with self.assertRaises(MemoryRuleError):  # (a town takes its climate from its region)
            await self.memory.set_map_fact(
                GUILD_A, self.c, self.bryn, mf.CLIMATE, "cold", source=BY_DM
            )
        with self.assertRaises(MemoryRuleError):  # (only a group rules)
            await self.memory.set_map_fact(
                GUILD_A, self.c, self.bryn, mf.CONTROL, self.dale, how="rules", source=BY_DM
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_map_fact(
                GUILD_A, self.c, self.bryn, mf.TERRAIN, "lava", source=BY_DM
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_map_fact(
                GUILD_A, self.c, self.bryn, mf.TERRAIN, "hill", source="background"
            )

    async def test_a_place_made_water_loses_its_land(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        await self.memory.set_place_form(GUILD_A, self.c, self.towns, "water", source=BY_DM)
        self.assertEqual(await self.facts(self.towns), [])

    async def test_a_read_waits_and_a_dm_row_stands(self) -> None:
        read = await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CLIMATE, "arctic")], source="background"
        )
        self.assertEqual(read.value["written"], 1)
        (row,) = await self.facts(self.dale)
        self.assertEqual((row.status, row.source, row.table_knows), (mf.READ, mf.FROM_TEXT, False))
        await self.memory.set_map_fact(GUILD_A, self.c, self.dale, mf.CLIMATE, "cold", source=BY_DM)
        again = await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CLIMATE, "hot")], source="background"
        )
        self.assertEqual(again.value["noted"], 1)  # (for World notes, #4285: nothing changes)
        (kept,) = await self.facts(self.dale)
        self.assertEqual((kept.value, kept.status), ("cold", mf.CONFIRMED))

    async def test_a_second_ruler_is_noted_never_swapped(self) -> None:
        other = await self.add("Arcane Brotherhood", type="faction", status=CONFIRMED)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        got = await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.bryn, mf.CONTROL, other, "rules")], source="background"
        )
        self.assertEqual(got.value["noted"], 1)
        (only,) = await self.facts(self.bryn)
        self.assertEqual(only.value, self.council)

    async def test_read_up_the_tree_and_never_stored(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.dale, mf.CLIMATE, "arctic", source=BY_DM
        )
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        held = await self.memory.map_facts_up(GUILD_A, self.c, self.bryn, gate.Viewer.the_dm())
        self.assertEqual(held[mf.CLIMATE].from_id, self.dale)
        self.assertEqual(held[mf.TERRAIN].from_id, self.towns)
        self.assertEqual(await self.facts(self.bryn), [])  # (nothing written on Bryn Shander)

    async def test_players_see_only_what_the_dm_kept_and_showed(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status=CONFIRMED)
        player = gate.Viewer.player([pc])
        for place in (self.dale, self.council):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, gate.ENTRY, place, [pc], source=BY_DM
            )
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.TERRAIN, "arctic", session_at=5)],
            source="background",
        )  # fmt: skip
        self.assertEqual(await self.facts(self.dale, player), [])  # (DMbot's read: the DM's)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.dale, mf.TERRAIN, "arctic", source=BY_DM
        )
        (shown,) = await self.facts(self.dale, player)  # (said at the table, now kept)
        self.assertEqual(shown.value, "arctic")
        await self.memory.show_map_facts(GUILD_A, self.c, self.dale, False, source=BY_DM)
        self.assertEqual(await self.facts(self.dale, player), [])
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        await self.memory.show_map_facts(GUILD_A, self.c, self.bryn, True, source=BY_DM)
        self.assertEqual(len(await self.facts(self.bryn, player)), 1)
        lair = await self.add("Hidden Keep", type="place", status=CONFIRMED)
        await self.memory.set_hidden_from_players(  # (a place only the DM knows of)
            GUILD_A, self.c, lair, True, source=BY_DM
        )
        await self.memory.set_map_fact(
            GUILD_A, self.c, lair, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        await self.memory.show_map_facts(GUILD_A, self.c, lair, True, source=BY_DM)
        self.assertEqual(await self.facts(lair, player), [])

    async def test_a_merge_moves_them_and_keeps_the_dms_word(self) -> None:
        twin = await self.add("Bryn Shander Town", type="place", status=CONFIRMED)
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.bryn, mf.TERRAIN, "hill")], source="background"
        )
        await self.memory.set_map_fact(GUILD_A, self.c, twin, mf.TERRAIN, "hill", source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, self.bryn, twin, source=BY_DM, dm_said_same=True)
        (kept,) = await self.facts(self.bryn)
        self.assertEqual((kept.value, kept.status), ("hill", mf.CONFIRMED))

    async def test_a_kind_change_drops_them_and_undo_brings_them_back(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        changed = await self.memory.set_entity_type(
            GUILD_A, self.c, self.council, "npc", source=BY_DM
        )
        self.assertEqual(await self.facts(self.bryn), [])
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch)
        (back,) = await self.facts(self.bryn)
        self.assertEqual(back.value, self.council)

    async def test_another_campaign_sees_none(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        got = await self.memory.map_facts(GUILD_A, other, [self.towns], gate.Viewer.the_dm())
        self.assertEqual(got, {})

    async def test_a_backup_carries_them_and_a_damaged_row_is_refused(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual([r["how"] for r in copy["memory_map_facts"]], ["rules"])
        for damage in ({"layer": "weather"}, {"how": "conquers"}, {"value": "hill"},
                       {"faction_id": None}, {"status": "maybe"}):  # fmt: skip
            with self.subTest(damage=damage):
                data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
                assert isinstance(data, dict)
                rows = [r for r in data["sections"]["memory"] if r["table"] == "map_fact"]
                self.assertEqual(len(rows), 1)
                rows[0].update(damage)
                with self.assertRaisesRegex(CampaignError, "damaged"):
                    await self.campaigns.import_backup(GUILD_B, data, DM)


class AfterTheReview(TheStore):
    """The reviewer's cases on #4256: backups that still load after a place changes, one 🙈 / 👁️
    for the whole place, a DM's no that sticks, and the ruler's neighbours."""

    async def round_trip(self) -> None:
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)

    async def test_land_then_water_still_backs_up_and_undo_brings_the_land_back(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        made = await self.memory.set_place_form(GUILD_A, self.c, self.towns, "water", source=BY_DM)
        self.assertEqual(await self.facts(self.towns), [])
        await self.round_trip()
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch)
        (back,) = await self.facts(self.towns)
        self.assertEqual(back.value, "hill")

    async def test_a_region_made_a_town_loses_its_climate_and_still_backs_up(self) -> None:
        await self.memory.set_map_fact(GUILD_A, self.c, self.dale, mf.CLIMATE, "cold", source=BY_DM)
        await self.memory.set_place_level(GUILD_A, self.c, self.dale, "settlement", source=BY_DM)
        self.assertEqual(await self.facts(self.dale), [])
        await self.round_trip()

    async def test_a_place_merged_into_a_person_leaves_its_facts_behind(self) -> None:
        mira = await self.add("Mira", type="npc", status=CONFIRMED)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        await self.memory.merge(GUILD_A, self.c, mira, self.towns, source=BY_DM, dm_said_same=True)
        self.assertEqual(await self.facts(mira), [])
        await self.round_trip()

    async def test_two_lands_from_a_merge_are_both_kept(self) -> None:
        twin = await self.add("Ten Towns", type="place", status=CONFIRMED)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.towns, mf.TERRAIN, "hill", source=BY_DM
        )
        await self.memory.set_map_fact(GUILD_A, self.c, twin, mf.TERRAIN, "grassland", source=BY_DM)
        await self.memory.merge(GUILD_A, self.c, self.towns, twin, source=BY_DM, dm_said_same=True)
        self.assertEqual(
            sorted(f.value for f in await self.facts(self.towns)), ["grassland", "hill"]
        )
        await self.round_trip()

    async def test_shown_once_shows_what_the_dm_sets_next(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status=CONFIRMED)
        player = gate.Viewer.player([pc])
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.dale, mf.TERRAIN, "arctic", source=BY_DM
        )
        await self.memory.show_map_facts(GUILD_A, self.c, self.dale, True, source=BY_DM)
        await self.memory.set_map_fact(GUILD_A, self.c, self.dale, mf.CLIMATE, "cold", source=BY_DM)
        seen = {f.layer for f in await self.facts(self.dale, player)}
        self.assertEqual(seen, {mf.TERRAIN, mf.CLIMATE})

    async def test_a_player_reads_up_only_through_places_they_know(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status=CONFIRMED)
        await self.memory.set_map_fact(GUILD_A, self.c, self.dale, mf.CLIMATE, "cold", source=BY_DM)
        await self.memory.show_map_facts(GUILD_A, self.c, self.dale, True, source=BY_DM)
        held = await self.memory.map_facts_up(GUILD_A, self.c, self.bryn, gate.Viewer.player([pc]))
        self.assertEqual(held[mf.CLIMATE].from_id, self.dale)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.towns, True, source=BY_DM)
        hidden = await self.memory.map_facts_up(
            GUILD_A, self.c, self.bryn, gate.Viewer.player([pc]), shown={self.bryn, self.dale}
        )
        self.assertNotIn(mf.CLIMATE, hidden)  # (the way up goes through Ten-Towns, unknown to them)

    async def test_a_dms_no_is_never_read_again(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CLIMATE, "hot")], source="background"
        )
        await self.memory.set_map_fact(GUILD_A, self.c, self.dale, mf.CLIMATE, None, source=BY_DM)
        again = await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CLIMATE, "hot")], source="background"
        )
        self.assertEqual(again.value["rejected_before"], 1)
        self.assertEqual(await self.facts(self.dale), [])

    async def test_a_new_ruler_leaves_another_groups_claim_beside_it(self) -> None:
        other = await self.add("Arcane Brotherhood", type="faction", status=CONFIRMED)
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.bryn, mf.CONTROL, other, "claims")], source="background"
        )
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.CONTROL, self.council, how="rules", source=BY_DM
        )
        got = {(f.value, f.how) for f in await self.facts(self.bryn)}
        self.assertEqual(got, {(self.council, "rules"), (other, "claims")})

    async def test_keep_what_dmbot_read_is_one_undo(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.dale, mf.CLIMATE, "arctic")], source="background"
        )
        kept = await self.memory.keep_map_facts(GUILD_A, self.c, self.dale, source=BY_DM)
        (row,) = await self.facts(self.dale)
        self.assertEqual((row.status, row.source), (mf.CONFIRMED, mf.FROM_DM))
        assert kept.batch is not None
        await self.memory.undo(GUILD_A, self.c, kept.batch)
        (back,) = await self.facts(self.dale)
        self.assertEqual(back.status, mf.READ)

    async def test_other_needs_its_words_in_plain_words(self) -> None:
        for words in ("", "x" * 61):
            with self.subTest(words=len(words)), self.assertRaises(MemoryRuleError):
                async with self.memory._write(GUILD_A, self.c, BY_DM) as w:
                    from dmbot.memory import map_fact_store
                    from dmbot.memory.store import _load_ontology

                    onto = await _load_ontology(w)
                    await map_fact_store.set_by_dm(
                        w, onto, self.towns, mf.TERRAIN, mf.OTHER, words=words
                    )

    async def test_a_damaged_open_row_is_refused(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.bryn, mf.TERRAIN, "hill", source=BY_DM
        )
        for damage in ({"entity_id": self.council}, {"layer": "climate", "value": "cold"}):
            with self.subTest(damage=damage):
                data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
                assert isinstance(data, dict)
                (row,) = [r for r in data["sections"]["memory"] if r["table"] == "map_fact"]
                row.update(damage)  # (land on a group; a climate on a place too small for one)
                with self.assertRaisesRegex(CampaignError, "damaged"):
                    await self.campaigns.import_backup(GUILD_B, data, DM)
