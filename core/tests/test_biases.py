"""Biases (#4229 slice 4; Supervisor's ruling on #4229, 2026-11-21): a place's, group's or people's
feeling toward a lineage or a named culture, read from set phrases in the lore (no AI), waiting in
World notes as DMbot's read until the DM keeps, changes or turns it down, each one Undo. A read one
counts for nothing, a turned-down one is never read in again, and a lineage is one DMbot knows.
Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import bias_read, biases, standing
from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest

DM_SOURCE = "dm"
HALL, LEAGUE, CULT = "h" * 32, "l" * 32, "c" * 32
HOLDERS = {"Mithral Keep": HALL, "the Silver League": LEAGUE}


class TheReader(unittest.TestCase):
    def read(self, text: str, cultures: dict[str, str] | None = None) -> list[bias_read.Found]:
        return bias_read.read(text, HOLDERS, cultures or {})

    def test_set_phrases_toward_a_lineage(self) -> None:
        got = self.read(
            "Mithral Keep distrusts elves. The Silver League openly welcomes halflings and gnomes."
        )
        self.assertEqual(
            [(f.holder_id, f.step, f.lineage) for f in got],
            [
                (HALL, standing.UNFRIENDLY, "elf"),
                (LEAGUE, standing.FRIENDLY, "halfling"),
                (LEAGUE, standing.FRIENDLY, "gnome"),  # (the rest of a list, #3545 item 2)
            ],
        )

    def test_a_list_reads_only_peoples_a_clause_ends_after(self) -> None:
        got = self.read("Mithral Keep distrusts elves and dwarves trust nobody there.")
        self.assertEqual([f.lineage for f in got], ["elf"])
        got = self.read("Mithral Keep hates orcs, goblins and elves.")  # (goblins: no lineage)
        self.assertEqual([f.lineage for f in got], ["orc"])

    def test_the_steps_own_words(self) -> None:  # (Supervisor's ruling C3 on #4982)
        got = self.read(
            "Mithral Keep is friendly toward dwarves. The Silver League is hostile to orcs."
        )
        self.assertEqual([(f.step, f.lineage) for f in got],
                         [(standing.FRIENDLY, "dwarf"), (standing.HOSTILE, "orc")])  # fmt: skip

    def test_the_perfect_tense_but_never_a_bare_past(self) -> None:  # (ruling C2 on #4982)
        got = self.read("Mithral Keep has long despised elves.")
        self.assertEqual([(f.step, f.lineage) for f in got], [(standing.HOSTILE, "elf")])
        self.assertEqual(self.read("Mithral Keep despised elves."), [])  # (it may have ended)

    def test_most_folk_in_a_holder(self) -> None:  # (Supervisor's ruling C1 on #4982)
        got = self.read("Most folk in Mithral Keep distrust gnomes.")
        self.assertEqual([(f.holder_id, f.lineage) for f in got], [(HALL, "gnome")])

    def test_the_people_of_a_holder(self) -> None:
        got = self.read("The people of Mithral Keep revere humans.")
        self.assertEqual([(f.holder_id, f.step, f.lineage) for f in got],
                         [(HALL, standing.DEVOTED, "human")])  # fmt: skip

    def test_toward_a_named_culture(self) -> None:
        got = self.read("Mithral Keep despises the Varnish folk.", {"Varnish": CULT})
        self.assertEqual([(f.step, f.culture_id, f.lineage) for f in got],
                         [(standing.HOSTILE, CULT, "")])  # fmt: skip

    def test_what_makes_nothing(self) -> None:
        for text in (
            "Mithral Keep does not trust elves.",  # (negated: the step would be a guess)
            "Brin distrusts elves.",  # (a person: a people's standing never flows to one)
            "Mithral Keep trades with elves.",  # (no feeling verb)
            "Mithral Keep distrusts the Silver League.",  # (no lineage or culture)
            "Mithral Keep is old. Elves are distrusted.",  # (not one sentence, not the shape)
            "Travelers to Mithral Keep fear orcs.",  # (the travelers' feeling, not the Keep's)
            "Nobody believes Mithral Keep hates elves.",  # (a doubted one)
            "Does Mithral Keep trust elves?",  # (a question)
            "If Mithral Keep distrusts elves, the deal fails.",  # (a supposition)
            "Mithral Keep likes dwarven ale.",  # (a drink, not a people)
            "Mithral Keep trusts human traders.",  # (traders, not every human)
        ):
            with self.subTest(text):
                self.assertEqual(self.read(text), [])

    def test_one_per_holder_and_target_the_first_given(self) -> None:
        got = self.read("Mithral Keep hates dwarves. Mithral Keep trusts dwarves.")
        self.assertEqual([f.step for f in got], [standing.HOSTILE])

    def test_every_lineage_key_is_one_dmbot_knows(self) -> None:
        with self.assertRaises(ValueError):
            biases.check_lineage("duergar")  # (Supervisor: checked against the known keys)
        biases.check_lineage("dwarf")


class TheStore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hall = await self.add("Mithral Keep", type="place", status="confirmed")
        self.town = await self.add("Frostvale", type="place", status="confirmed")

    async def read(self, step: str = standing.UNFRIENDLY, **target: str) -> int:
        target = target or {"lineage": "elf"}
        found = [bias_read.Found(self.hall, step, **target)]
        written = await self.memory.add_read_biases(GUILD_A, self.c, found, source="background")
        return written.value

    async def notes(self) -> list[wn.Note]:
        return [n for n in await self.memory.world_notes(GUILD_A, self.c) if n.kind == wn.BIAS]

    async def test_a_read_bias_waits_in_world_notes_and_counts_for_nothing(self) -> None:
        self.assertEqual(await self.read(), 1)
        (note,) = await self.notes()
        self.assertEqual((note.name, note.value, note.how), ("Mithral Keep", "elves", "Unfriendly"))
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.attitudes, {})

    async def test_keep_change_and_turn_down_each_one_undo(self) -> None:
        await self.read()
        (note,) = await self.notes()
        kept = await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source=DM_SOURCE)
        self.assertEqual(await self.notes(), [])
        assert kept.batch is not None
        await self.memory.undo(GUILD_A, self.c, kept.batch, source=DM_SOURCE)
        (note,) = await self.notes()
        row_id = wn.split_id(note.id)[1]
        changed = await self.memory.change_read_bias(
            GUILD_A, self.c, row_id, standing.HOSTILE, source=DM_SOURCE
        )
        self.assertEqual(await self.notes(), [])
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch, source=DM_SOURCE)
        (note,) = await self.notes()
        self.assertEqual(note.how, standing.UNFRIENDLY)  # (Undo put the step back too)
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source=DM_SOURCE)
        self.assertEqual(await self.notes(), [])
        with self.assertRaises(MemoryRuleError):  # (answered: the screen says it's gone)
            await self.memory.change_read_bias(
                GUILD_A, self.c, row_id, standing.WARY, source=DM_SOURCE
            )

    async def test_a_turned_down_or_kept_one_is_never_read_in_again(self) -> None:
        await self.read()
        (note,) = await self.notes()
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source=DM_SOURCE)
        self.assertEqual(await self.read(standing.HOSTILE), 0)
        self.assertEqual(await self.notes(), [])

    async def test_toward_a_named_culture_and_gone_with_it(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD_A, self.c, [Read(self.town, mf.CULTURE, "", "strong", culture="Varnish")],
            source="background",
        )  # fmt: skip
        [(culture_id, _, _)] = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual(await self.read(culture_id=culture_id), 1)
        (note,) = await self.notes()
        self.assertEqual(note.value, "Varnish")

    async def test_a_person_never_holds_one(self) -> None:
        brin = await self.add("Brin", type="character", status="confirmed")
        found = [bias_read.Found(brin, standing.HOSTILE, lineage="elf")]
        written = await self.memory.add_read_biases(GUILD_A, self.c, found, source="background")
        self.assertEqual(written.value, 0)  # (the store's own rule, whoever calls)

    async def test_undo_of_a_turn_down_brings_it_back(self) -> None:
        await self.read()
        (note,) = await self.notes()
        gone = await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source=DM_SOURCE)
        assert gone.batch is not None
        await self.memory.undo(GUILD_A, self.c, gone.batch, source=DM_SOURCE)
        self.assertEqual([n.id for n in await self.notes()], [note.id])

    async def test_a_merge_never_brings_back_one_turned_down(self) -> None:
        other = await self.add("Mithral Hold", type="place", status="confirmed")
        await self.read()
        (note,) = await self.notes()
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source=DM_SOURCE)
        found = [bias_read.Found(other, standing.FRIENDLY, lineage="elf")]
        await self.memory.add_read_biases(GUILD_A, self.c, found, source="background")
        await self.memory.merge(GUILD_A, self.c, self.hall, other, source=DM_SOURCE,
                                dm_said_same=True)  # fmt: skip
        self.assertEqual(await self.notes(), [])

    async def test_a_lineage_dmbot_doesnt_know_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.read(lineage="duergar")

    async def test_a_merge_moves_a_bias_and_the_kept_holders_own_wins(self) -> None:
        other = await self.add("Mithral Hold", type="place", status="confirmed")
        await self.read()
        found = [bias_read.Found(other, standing.FRIENDLY, lineage="elf")]
        await self.memory.add_read_biases(GUILD_A, self.c, found, source="background")
        await self.memory.merge(GUILD_A, self.c, self.hall, other, source=DM_SOURCE,
                                dm_said_same=True)  # fmt: skip
        (note,) = await self.notes()  # (one open toward elves: the kept holder's own)
        self.assertEqual((note.entity_id, note.how), (self.hall, standing.UNFRIENDLY))

    async def test_the_lore_upload_reads_them(self) -> None:
        from types import SimpleNamespace
        from typing import Any, cast

        from dmbot.ui.name_background import store_biases

        campaign = cast(Any, SimpleNamespace(guild_id=GUILD_A, id=self.c))
        made = await store_biases(self.memory, campaign, "Mithral Keep distrusts elves.")
        self.assertEqual(made, 1)
        self.assertEqual([n.value for n in await self.notes()], ["elves"])
