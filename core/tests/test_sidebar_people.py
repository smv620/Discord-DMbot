"""Who is who at the table in the sidebar's message (#1188 part 3): names only, no ids, for
the DM's answers and for table questions alike. The names are made up."""

from __future__ import annotations

import unittest

from dmbot.rules.index import srd
from dmbot.sidebar import context
from tests.test_memory_store import DM, GUILD_A, MemoryTest
from tests.test_people import OTHER, PLAYER, SRC


def build(people: list[str], *, table: bool = False) -> str:
    return context.build(
        "Who is Mike?",
        target="2024",
        fallback="2014",
        index=srd(),
        house_rules=[],
        lookup=None,
        scene="",
        people=people,
        table=table,
    ).prompt


class Message(unittest.TestCase):
    def test_the_lines_are_in_the_message_for_the_dm_and_for_the_table(self) -> None:
        for table in (False, True):
            prompt = build(["Mia plays Cerric, is also called Mike"], table=table)
            self.assertIn("WHO IS WHO AT THE TABLE", prompt)
            self.assertIn("Mia plays Cerric, is also called Mike", prompt)

    def test_nothing_is_added_when_nobody_is_known(self) -> None:
        self.assertNotIn("WHO IS WHO", build([]))

    def test_a_long_list_is_cut(self) -> None:
        prompt = build([f"Player{i} plays Hero{i}" for i in range(30)])
        self.assertIn("Player11", prompt)
        self.assertNotIn("Player12 ", prompt)


class FromMemory(MemoryTest):
    async def test_confirmed_players_names_and_characters_only(self) -> None:
        await self.add("Cerric", type="player_character", played_by=PLAYER)
        npc = await self.add("Belleros")
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.learn_people(GUILD_A, self.c, [("Belleros", OTHER, 2)], limit=10)
        before = await self.memory.table_people(GUILD_A, self.c)
        # Waiting questions are not true yet: nothing about them is told.
        self.assertEqual(sorted(before), [(OTHER, [], []), (PLAYER, ["Cerric"], [])])
        rows = await self.memory.people_suggestions(GUILD_A, self.c)
        mike = next(s for s in rows if s.name == "Mike")
        await self.memory.accept_person_name(GUILD_A, self.c, mike.row_id)
        after = dict((u, (c, a)) for u, c, a in await self.memory.table_people(GUILD_A, self.c))
        self.assertEqual(after[PLAYER], (["Cerric"], ["Mike"]))
        self.assertTrue(npc and SRC)

    async def test_another_campaigns_people_are_not_here(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Another", DM)).id
        await self.add("Cerric", type="player_character", played_by=PLAYER, campaign=other)
        self.assertEqual(await self.memory.table_people(GUILD_A, self.c), [])
