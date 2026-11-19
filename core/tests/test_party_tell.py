"""Catching one party member up from a card (#4537, party knowledge slice 4b;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, 2026-11-19; the ux-critic's
words): what all the other members know on the card and the member doesn't, one change with one
Undo, never what only some know. Invented names and ids only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError
from dmbot.ui import party_tell
from tests.test_party_join import CARA, GUILD, Party


class TheWords(unittest.TestCase):
    def test_the_label_keeps_the_start_and_cuts_the_name(self) -> None:
        self.assertEqual(party_tell.one_label("Mira"), "📖 Catch up: Mira")
        long = party_tell.one_label("Mira Stormborn-of-the-Northern-Wastes")
        self.assertLessEqual(len(long), party_tell.LABEL_MAX)
        self.assertTrue(long.startswith("📖 Catch up: Mira") and long.endswith("…"))
        picked = party_tell.pick_label("Mira Stormborn-of-the-Northern-Wastes", 6)
        self.assertTrue(picked.endswith(" (6 things)"))  # (the name is cut, never the count)
        self.assertEqual(party_tell.pick_label("Mira", 1), "Mira (1 thing)")

    def test_the_card_line_names_who_is_missing_something(self) -> None:
        self.assertEqual(party_tell.card_line([]), "")
        self.assertEqual(
            party_tell.card_line([("Mira", 6)]),
            "\nMira is missing 6 things on this card that the rest of the party knows.",
        )
        self.assertIn(
            "Mira and Tavin are missing things", party_tell.card_line([("Mira", 6), ("Tavin", 2)])
        )
        for text in (party_tell.DONE, party_tell.UNDONE, party_tell.CANT_UNDO, party_tell.WHO):
            for word in ("she", "he", "her", "him", "his", "they", "them", "their"):
                self.assertNotIn(f" {word} ", f" {text} ")  # (ruling C: named, no pronoun)


class CatchingUp(Party):
    """Vell and Tuck know the harbourmaster and the drowned bell; Cara joined and was told only
    what everyone knows."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bell = await self.made("The drowned bell", "quest")  # (shown: Vell and Tuck know it)
        self.cove = await self.made("Kesh", "npc")  # (named by the quest)
        rel = await self.memory.add_relation(
            GUILD, self.c, self.cove, "knows", self.bell, source=DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        self.at_cove = str(rel.value[0].id)
        self.cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)

    async def made(self, name: str, kind: str) -> str:
        done = await self.memory.add_entity(
            GUILD, self.c, name=name, type=kind, status=CONFIRMED, source=DM
        )
        return str(done.value.id)

    async def rows(self) -> set[tuple[Any, ...]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT perspective_id, about_entity_id, state_id, relation_id, alias_id,"
                " confidence FROM memory_knowers WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.c),
            )
            return {tuple(r.values()) for r in await cur.fetchall()}

    async def test_the_card_offers_whoever_is_missing_what_the_rest_know(self) -> None:
        [(who, count)] = await self.memory.tell_choices(GUILD, self.c, self.npc)
        self.assertEqual(who, self.cara)
        self.assertGreater(count, 0)
        self.assertFalse(await self.sees(self.cara, ENTRY, self.npc))

    async def test_catching_up_is_one_change_and_its_undo_puts_the_rows_back(self) -> None:
        before = await self.rows()
        [(_, count)] = await self.memory.tell_choices(GUILD, self.c, self.npc)
        done = await self.memory.tell_member(GUILD, self.c, self.cara, self.npc)
        self.assertEqual(done.value, count)  # (the number shown is the number told)
        self.assertTrue(await self.sees(self.cara, ENTRY, self.npc))
        self.assertEqual(await self.memory.tell_choices(GUILD, self.c, self.npc), [])
        again = await self.memory.tell_member(GUILD, self.c, self.cara, self.npc)
        self.assertEqual((again.value, again.batch), (0, None))  # (nothing left: no change)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.rows(), before)

    async def test_a_quests_card_brings_the_places_it_names(self) -> None:
        done = await self.memory.tell_member(GUILD, self.c, self.cara, self.bell)
        self.assertGreaterEqual(done.value, 3)  # (the quest, its connection, Kesh it names…)
        self.assertTrue(await self.sees(self.cara, ENTRY, self.cove))
        self.assertTrue(await self.sees(self.cara, CONNECTION, self.at_cove))
        self.assertFalse(await self.sees(self.cara, ENTRY, self.npc))  # (not on this card)

    async def test_a_persons_card_doesnt_bring_the_quest_that_names_them(self) -> None:
        before = await self.rows()
        await self.memory.tell_member(GUILD, self.c, self.cara, self.cove)
        self.assertTrue(await self.sees(self.cara, CONNECTION, self.at_cove))
        made = await self.rows() - before
        self.assertNotIn(self.bell, {r[1] for r in made})  # (no row about the quest itself)

    async def test_what_only_some_know_is_never_told(self) -> None:
        secret = await self.made("The harbourmaster's debt", "event")
        await self.memory.set_statement_knowers(
            GUILD, self.c, "entry", secret, [self.vell], source=DM, dm_chose_secret=True
        )
        self.assertEqual(await self.memory.tell_choices(GUILD, self.c, secret), [])
        before = await self.rows()
        done = await self.memory.tell_member(GUILD, self.c, self.cara, secret)
        self.assertEqual((done.value, done.batch), (0, None))
        self.assertEqual(await self.rows(), before)

    async def test_one_not_in_the_party_is_refused_and_nobody_is_offered_alone(self) -> None:
        await self.memory.leave_party(GUILD, self.c, self.cara, "left")
        with self.assertRaises(MemoryRuleError):
            await self.memory.tell_member(GUILD, self.c, self.cara, self.npc)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        self.assertEqual(await self.memory.tell_choices(GUILD, self.c, self.npc), [])


if __name__ == "__main__":
    unittest.main()
