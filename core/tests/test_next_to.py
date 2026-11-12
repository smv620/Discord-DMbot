"""Two places beside each other (#4173 slice 6a): how a `next_to` is worded, and that a one-way
word ("on the shore of") keeps the order it was said in once stored, whichever place's id sorts
first. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import link_words, next_words
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as BY_DM
from tests.test_memory_store import GUILD_A, MemoryTest


class TheWords(unittest.TestCase):
    def test_the_words_said_fold_to_one(self) -> None:
        for said, kept in (
            ("on the shores of", next_words.SHORE), ("Lakeside", next_words.SHORE),
            ("shares a border with", next_words.BORDERS), ("  at the  foot of ", next_words.FOOT),
            ("on", next_words.ON), ("hugs", ""), ("below", ""), ("", ""),
        ):  # fmt: skip
            with self.subTest(said=said):
                self.assertEqual(next_words.word_of(said), kept)

    def test_one_way_words_and_how_they_read(self) -> None:
        self.assertTrue(next_words.one_way("on the shore of"))
        self.assertFalse(next_words.one_way("borders"))
        self.assertFalse(next_words.one_way(""))
        self.assertEqual(next_words.forward("on the coast of"), "is on the coast of")
        self.assertEqual(next_words.forward(""), "is next to")
        self.assertEqual(next_words.pair("borders"), "share a border")
        self.assertEqual(next_words.pair(""), "are next to each other")

    def test_a_one_way_word_never_reads_both_ways(self) -> None:
        self.assertFalse(link_words.reads_both_ways("next_to", "on the shore of", both_ways=True))
        self.assertTrue(link_words.reads_both_ways("next_to", "borders"))


class Stored(MemoryTest):
    async def test_on_the_shore_of_keeps_its_order_whichever_id_is_first(self) -> None:
        orders = set()
        for n in range(6):  # (ids are random: six pairs meet both orders all but surely)
            town = await self.add(f"Targos {n}", type="place", status=CONFIRMED)
            lake = await self.add(f"Maer Dualdon {n}", type="place", status=CONFIRMED)
            orders.add(town < lake)
            written = await self.memory.add_relation(
                GUILD_A, self.c, town, "next_to", lake, source=BY_DM, confidence=1.0,
                status=CONFIRMED, detail="on the shore of",
            )  # fmt: skip
            rel = written.value[0]
            self.assertEqual(
                (rel.subject_id, rel.object_id, rel.detail), (town, lake, "on the shore of")
            )
        self.assertEqual(orders, {True, False})

    async def test_plain_next_to_is_one_fact_whichever_way_it_is_said(self) -> None:
        a = await self.add("Bryn Shander", type="place", status=CONFIRMED)
        b = await self.add("Termalaine", type="place", status=CONFIRMED)
        await self.memory.add_relation(
            GUILD_A, self.c, a, "next_to", b, source=BY_DM, confidence=1.0, status=CONFIRMED
        )
        await self.memory.add_relation(
            GUILD_A, self.c, b, "next_to", a, source=BY_DM, confidence=1.0, status=CONFIRMED
        )
        found = [
            r for r in await self.memory.relations(GUILD_A, self.c) if r.predicate == "next_to"
        ]
        self.assertEqual(len(found), 1)
