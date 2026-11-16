"""A story thread's clues (#4624, quests slice 6a): the count's words, the DM tying, marking and
untying a clue, and Supervisor's condition that the tie and its "false lead" word are the DM's
alone on every surface: refused at the one write layer, left out by the gate, and never in a
player's list of a thread's clues. Postgres. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory import gate
from dmbot.memory import thread_clues as tc
from dmbot.memory._changes import BELIEFS, PERSPECTIVES, RELATIONS
from dmbot.memory.models import DM, OPEN, MemoryRuleError, new_id
from dmbot.ui import thread_clues as screen
from dmbot.ui.thread_parts import card_lines
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest


def clue(name: str, *, found: bool, lead: bool = False) -> tc.ThreadClue:
    return tc.ThreadClue(name, "r", name, lead, found)


class TheCount(unittest.TestCase):
    def test_the_four_forms(self) -> None:
        self.assertEqual(tc.found_text([]), "")
        some = [clue("a", found=True), clue("b", found=True), clue("c", found=False)]
        self.assertEqual(tc.found_text(some), "2 of 3 clues found. Not yet found: 1.")
        lead = [clue("a", found=True), clue("b", found=False, lead=True)]
        self.assertEqual(
            tc.found_text(lead), "1 of 2 clues found (1 is a false lead). Not yet found: 1."
        )
        every = [clue("a", found=True), clue("b", found=True)]
        self.assertEqual(tc.found_text(every), "2 clues found so far.")
        self.assertEqual(tc.found_text([clue("a", found=True)]), "1 clue found so far.")
        self.assertEqual(
            tc.found_text([clue("a", found=False)]), "0 of 1 clue found. Not yet found: 1."
        )

    def test_never_a_percentage(self) -> None:
        many = [clue(str(i), found=i < 3) for i in range(7)]
        self.assertNotIn("%", tc.found_text(many))

    def test_the_word(self) -> None:
        self.assertEqual(tc.word_of("False lead"), tc.FALSE_LEAD)
        self.assertEqual(tc.word_of(""), tc.POINTS_TO)


class TheClues(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.new_thread(
            GUILD_A, self.c, "Who killed the Speaker?", "mystery", OPEN, source=DM
        )
        self.thread = made.value.id
        # (hidden: nobody at the table has it yet; a clue the players are shown is found)
        self.glove = await self.add(
            "A bloody glove", type="clue", status="confirmed", hidden_from_players=True
        )
        self.pc = await self.add(
            "Tavin", type="character", role="player_character", played_by=DM_USER,
            status="confirmed",
        )  # fmt: skip

    async def listed(self) -> list[tc.ThreadClue]:
        return await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())

    async def tie(self, **kw: object) -> str:
        written = await self.memory.tie_clue(
            GUILD_A,
            self.c,
            self.thread,
            source=DM,
            **kw,  # type: ignore[arg-type]
        )
        return written.value[0].id

    async def test_a_tie_is_secret_with_its_word_and_one_undo(self) -> None:
        rel = await self.tie(clue_id=self.glove)
        async with self.memory._read(GUILD_A, self.c) as scope:
            (row,) = await scope.select(RELATIONS, " AND id = %s", [rel])
        self.assertEqual(
            (row["predicate"], row["detail"], row["secret"]), ("clue_to", tc.POINTS_TO, True)
        )
        with self.assertRaises(MemoryRuleError):
            await self.tie(clue_id=self.glove)  # (tied already)
        with self.assertRaises(MemoryRuleError):
            await self.tie(clue_id=self.pc)  # (not a clue)
        with self.assertRaises(MemoryRuleError):
            await self.memory.tie_clue(
                GUILD_A, self.c, self.thread, clue_id=self.glove, source="extractor"
            )

    async def test_a_new_clue_by_name_marked_untied_and_undone(self) -> None:
        written = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="A torn letter", false_lead=True, source=DM
        )
        clues = await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())
        self.assertEqual(
            [(c.name, c.false_lead, c.found) for c in clues], [("A torn letter", True, False)]
        )
        letter = clues[0].clue_id
        await self.memory.set_clue_word(
            GUILD_A, self.c, self.thread, letter, false_lead=False, source=DM
        )
        clues = await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())
        self.assertFalse(clues[0].false_lead)
        untied = await self.memory.untie_clue(GUILD_A, self.c, self.thread, letter, source=DM)
        self.assertEqual(
            await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm()), []
        )
        assert untied.batch is not None
        await self.memory.undo(GUILD_A, self.c, untied.batch)
        self.assertEqual(
            len(await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())),
            1,
        )
        assert written.batch is not None

    async def test_found_is_a_player_characters_knowing_and_players_see_only_theirs(self) -> None:
        await self.tie(clue_id=self.glove, false_lead=True)
        await self.tie(new_name="A torn letter")
        await self.add(  # (a second player character who doesn't know it: one is enough)
            "Mira", type="character", role="player_character", played_by=DM_USER + 1,
            status="confirmed",
        )  # fmt: skip
        await self.memory.learn_statement(
            GUILD_A, self.c, gate.ENTRY, self.glove, self.pc, source=DM
        )
        dm = await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())
        self.assertEqual(
            [(c.name, c.found) for c in dm], [("A bloody glove", True), ("A torn letter", False)]
        )
        self.assertEqual(
            tc.found_text(dm), "1 of 2 clues found (1 is a false lead). Not yet found: 1."
        )
        mine = await self.memory.thread_clues(
            GUILD_A, self.c, self.thread, gate.Viewer.player([self.pc])
        )
        self.assertEqual(
            [(c.name, c.false_lead, c.relation_id) for c in mine], [("A bloody glove", False, "")]
        )

    async def test_only_the_dms_own_tie_and_one_per_clue_whatever_its_word(self) -> None:
        with self.assertRaises(MemoryRuleError):  # (a kept claim or any other path: refused)
            await self.memory.add_relation(
                GUILD_A, self.c, self.glove, tc.CLUE_TO, self.thread, source=DM, confidence=1.0,
                status="confirmed",
            )  # fmt: skip
        await self.tie(clue_id=self.glove)
        with self.assertRaises(MemoryRuleError):
            await self.tie(clue_id=self.glove, false_lead=True)
        self.assertEqual(
            len(await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())),
            1,
        )

    async def test_a_tie_by_a_new_name_undone_takes_the_clue_too(self) -> None:
        written = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="A torn letter", source=DM
        )
        self.assertTrue(written.value[1])  # (made new)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch, source=DM)
        self.assertEqual(
            await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm()), []
        )
        again = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="A bloody glove", source=DM
        )
        self.assertFalse(again.value[1])  # (the campaign's clue of that name)

    async def test_saying_both_names_aloud_shows_the_players_nothing(self) -> None:
        """B3 of the review: the after-session exposures never pick up a tie, and a reveal of one
        writes nothing (the recap's stricter guard still sees it)."""
        from dmbot.memory.exposures import secret_connections

        rel = await self.tie(clue_id=self.glove)
        self.assertEqual(await secret_connections(self.memory, GUILD_A, self.c), [])
        found = await secret_connections(self.memory, GUILD_A, self.c, dm_only=True)
        self.assertEqual([c.relation_id for c in found], [rel])
        done = await self.memory.reveal_exposed(GUILD_A, self.c, spellings=[], relation_ids=[rel])
        self.assertEqual(done.value, (0, 0))

    async def test_nobody_but_the_dm_can_ever_know_a_tie(self) -> None:
        """Supervisor's condition on #4624: every path that would let a player know the tie is
        refused at the write layer, and a row put in behind its back grants nothing."""
        rel = await self.tie(clue_id=self.glove, false_lead=True)
        with self.assertRaises(MemoryRuleError):
            await self.memory.learn_statement(
                GUILD_A, self.c, gate.CONNECTION, rel, self.pc, source=DM
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_common_knowledge(
                GUILD_A, self.c, gate.CONNECTION, [rel], source=DM
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, gate.CONNECTION, rel, [self.pc], source=DM, dm_chose_secret=True
            )
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            with self.assertRaises(MemoryRuleError):
                await w.update(RELATIONS, rel, {"secret": False})
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            (party,) = await w.select(PERSPECTIVES, " AND kind = 'party'")
            with self.assertRaises(MemoryRuleError):
                await w.insert(
                    BELIEFS,
                    {"id": new_id(), "perspective_id": party["id"], "relation_id": rel,
                     "confidence": 1.0, "status": "revealed_fact", "session_at": None,
                     "evidence_ids": [], "recorded_at": w.now},
                )  # fmt: skip
        # a knower row written straight to the table, as an old door might: the gate ignores it
        async with self.memory._read(GUILD_A, self.c) as scope:
            views = await scope.select(PERSPECTIVES, " AND entity_id = %s", [self.pc])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_knowers (guild_id, campaign_id, id, perspective_id,"
                " relation_id, created_at, confidence) VALUES (%s, %s, %s, %s, %s, 1, 1.0)",
                (GUILD_A, self.c, new_id(), views[0]["id"], rel),
            )
        async with self.memory._read(GUILD_A, self.c) as scope:
            seen = await gate.visible_to(scope, gate.Viewer.player([self.pc]))
            party_seen = await gate.visible_to(scope, gate.Viewer.the_party())
        self.assertFalse(seen.knows(gate.CONNECTION, rel))
        self.assertFalse(party_seen.knows(gate.CONNECTION, rel))


class NeverFromAReader(unittest.TestCase):
    def test_a_readers_words_never_make_a_tie(self) -> None:
        """B4 of the review: "is a clue to" from a text falls back to the catch-all, its words kept;
        "lead to" (a misspelt "led to") is no word of a tie."""
        from dmbot.memory.claims import resolve_relationship
        from dmbot.memory.ontology import Ontology

        onto = Ontology.build()
        for words in ("is a clue to", "clue to", "clue_to", "evidence for", "has the clue"):
            with self.subTest(words):
                found = resolve_relationship(onto, words)
                self.assertTrue(found is None or found[0].key != tc.CLUE_TO)
        self.assertNotIn(
            "lead_to", next(p for p in onto.predicates.values() if p.key == tc.CLUE_TO).synonyms
        )


class TheScreenWords(unittest.TestCase):
    def test_the_screen_and_the_card_line(self) -> None:
        clues = [clue("A *glove*", found=True, lead=True), clue("A letter", found=False)]
        text = screen.screen_text("Who did it?", clues)
        self.assertIn("🔎 **Clues to Who did it?**", text)
        self.assertIn("1 of 2 clues found (1 is a false lead). Not yet found: 1.", text)
        self.assertIn("• A \\*glove\\* (found · false lead)", text)
        self.assertIn("• A letter (not found yet)", text)
        self.assertIn(screen.NONE_YET, screen.screen_text("X", []))
        lines = card_lines([], {}, None, [], clues=tc.found_text(clues))
        self.assertEqual(
            lines, ["🔎 **Clues:** 1 of 2 clues found (1 is a false lead). Not yet found: 1."]
        )
        self.assertEqual(card_lines([], {}, None, []), [])  # (no clue: no line)


class TheScreen(TheClues):
    """Add, mark and untie through the screen's own buttons, each with its Undo."""

    async def press(self, handler: Any) -> list[dict[str, Any]]:
        shown: list[dict[str, Any]] = []

        async def redraw(_it: Any, _c: str, _t: str, **kw: Any) -> None:
            shown.append(kw)

        campaign = SimpleNamespace(guild_id=GUILD_A, id=self.c)
        with (
            patch.object(screen, "_dm", AsyncMock(return_value=(campaign, self.memory))),
            patch.object(screen, "_answer_first", AsyncMock()),
            patch.object(screen, "open_clues", redraw),
            patch.object(screen, "changed", lambda *_: None),
        ):
            await handler(SimpleNamespace())
        return shown

    async def dm_clues(self) -> list[tc.ThreadClue]:
        return await self.memory.thread_clues(GUILD_A, self.c, self.thread, gate.Viewer.the_dm())

    async def test_add_mark_and_untie(self) -> None:
        form = screen.ClueForm(self.c, self.thread)
        form.name._value = "A bloody glove"  # (the campaign's clue of that name, never a twin)
        (said,) = await self.press(form._submit)
        self.assertEqual(said["note"], "✅ **A bloody glove** is now a clue to this thread.")
        (only,) = await self.dm_clues()
        self.assertEqual(only.clue_id, self.glove)
        (said,) = await self.press(screen.ClueMenu(self.c, self.thread, only)._mark)
        self.assertIn("false lead", said["note"])
        self.assertTrue((await self.dm_clues())[0].false_lead)
        (said,) = await self.press(
            screen.ClueMenu(self.c, self.thread, (await self.dm_clues())[0])._untie
        )
        self.assertEqual(await self.dm_clues(), [])
        await self.memory.undo(GUILD_A, self.c, said["undo"], source=DM)
        self.assertEqual(len(await self.dm_clues()), 1)

    async def test_a_refusal_is_said_on_the_screen(self) -> None:
        await self.tie(clue_id=self.glove)
        form = screen.ClueForm(self.c, self.thread)
        form.name._value = "A bloody glove"
        (said,) = await self.press(form._submit)
        self.assertEqual(said["note"], f"⚠️ {tc.ALREADY_TIED}")


class TheWebsiteGraph(unittest.TestCase):
    """B2 of the review: the website builds its own gate input, so a knower row on a tie (the kind
    the write layer refuses) must still draw nothing for a player, the party or someone seen as."""

    def test_a_player_is_never_drawn_a_tie_even_with_a_row(self) -> None:
        from dmbot.web import graph_view
        from tests.test_node_summary_run import KESH, MIRA, PC, world

        src, kn = world([("l1", MIRA, KESH, tc.CLUE_TO)], {"l1": [PC]})
        src["links"][0]["detail"] = tc.FALSE_LEAD
        for viewer in (
            gate.Viewer.player([PC]),
            gate.Viewer.the_party(),
            gate.Viewer.character(PC),
        ):
            graph = graph_view.build(src, kn, viewer)
            self.assertNotIn("l1", [e["id"] for e in graph.edges])
        dm = graph_view.build(src, kn, gate.Viewer.the_dm())
        self.assertIn("l1", [e["id"] for e in dm.edges])


if __name__ == "__main__":
    unittest.main()
