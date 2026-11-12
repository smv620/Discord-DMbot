"""The SRD's languages are never suggested as names (#4227): "Elvish" is rules vocabulary, so the
readers never propose it, and one already waiting is turned down as "Not a name" would."""

from __future__ import annotations

import unittest

from dmbot.memory import ontology
from dmbot.memory.models import BACKGROUND_SOURCE, PROPOSED, REJECTED
from dmbot.names import not_names as nn
from tests.test_memory_store import GUILD_A, MemoryTest


class TheWords(unittest.TestCase):
    def test_a_word_that_is_only_a_language_is_one_alone_or_after_a_speech_word(self) -> None:
        for said in ("Elvish", "speaks Dwarvish", "Thieves' Cant", "Deep Speech", "draconic"):
            with self.subTest(said=said):
                self.assertTrue(nn.is_language(said))
                self.assertEqual(nn.shape_of(said), "language")

    def test_one_that_is_also_a_name_or_a_creature_is_one_only_after_a_speech_word(self) -> None:
        for said in ("speaks Sylvan", "spoke Giant", "speaking Common"):
            with self.subTest(said=said):
                self.assertTrue(nn.is_language(said))
        for said in ("Sylvan", "Terran", "The Giant", "the goblin", "Halfling", "The Common"):
            with self.subTest(said=said):
                self.assertFalse(nn.is_language(said))
                self.assertNotEqual(nn.shape_of(said), "language")

    def test_a_name_that_only_starts_with_a_language_word_is_still_a_name(self) -> None:
        for said in ("Old Vael", "Goblin Grik", "Common Room", "Elvira", "Elvish Court"):
            with self.subTest(said=said):
                self.assertFalse(nn.is_language(said))
                self.assertIsNone(nn.shape_of(said))

    def test_in_elvish_is_still_no_name(self) -> None:
        self.assertEqual(nn.shape_of("in Elvish"), "clause")  # a cut-out clause, as before

    def test_the_kinds_rule_uses_the_same_list(self) -> None:
        self.assertTrue(nn.ONLY_LANGUAGES <= ontology.RULES_WORDS)
        self.assertIn("language", ontology.RULES_WORDS)
        self.assertNotIn("sylvan", ontology.RULES_WORDS)  # also a name: free as a kind


class WaitingOnes(MemoryTest):
    async def test_a_waiting_language_is_turned_down_and_the_dms_own_is_kept(self) -> None:
        elvish = await self.add("Elvish", type="concept", source="scan", status=PROPOSED)
        grik = await self.add("Grik", source="scan", status=PROPOSED)
        own = await self.add("Draconic", type="concept", source="dm", status=PROPOSED)
        self.assertEqual(await self.memory.drop_language_suggestions(GUILD_A, self.c), 1)
        status = {}
        for entity_id in (elvish, grik, own):
            entity = await self.memory.entity(GUILD_A, self.c, entity_id)
            assert entity is not None
            status[entity_id] = entity.status
        self.assertEqual(status[elvish], REJECTED)
        self.assertEqual(status[grik], PROPOSED)  # a real name stays
        self.assertEqual(status[own], PROPOSED)  # the DM's own entry is never touched
        self.assertEqual(await self.memory.drop_language_suggestions(GUILD_A, self.c), 0)

    async def test_a_language_read_from_the_dms_text_keeps_none_of_it(self) -> None:
        # as "Not a name" does (#2167): the quote of the DM's upload goes from the entry and the log
        elvish = await self.add(
            "Elvish",
            type="concept",
            source=BACKGROUND_SOURCE,
            status=PROPOSED,
            description="She whispered in Elvish to the moonwell.",
        )
        self.assertEqual(await self.memory.drop_language_suggestions(GUILD_A, self.c), 1)
        entity = await self.memory.entity(GUILD_A, self.c, elvish)
        assert entity is not None
        self.assertEqual((entity.status, entity.description), (REJECTED, ""))
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT before, after FROM memory_changes")
            logged = " ".join(str(v) for row in await cur.fetchall() for v in row.values())
        self.assertNotIn("moonwell", logged)

    async def test_nothing_to_drop_writes_no_change(self) -> None:
        await self.add("Grik", source="scan", status=PROPOSED)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_changes")
            before = (await cur.fetchone())["n"]
        self.assertEqual(await self.memory.drop_language_suggestions(GUILD_A, self.c), 0)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_changes")
            self.assertEqual((await cur.fetchone())["n"], before)
