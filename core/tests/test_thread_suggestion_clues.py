"""Quests slice 6b (#4624; design `docs/design/quests.md` §7, §9.3): the lore reader suggests a
thread's clues with it, and ✅ Keep ties each clue left on to the thread.

Code's side (no AI): a clue needs words and its own quote in the text, the same words twice is
one, at most `CLUES_MAX`, and who has it is only a name the text gives. The keep's side (Postgres):
a clue is the campaign's clue of that name or a new one hidden from the players, tied with the DM's
`clue_to` in the thread's own change; the characters the text says have it learn it and are listed
as knowing it, and only they may (ruling 3); one Undo takes it all back. A session's clues are 6c's:
the session reader keeps none yet. Invented names."""

from __future__ import annotations

import json
import unittest

from dmbot.memory import gate
from dmbot.memory import thread_clues as tc
from dmbot.memory import thread_read as tr
from dmbot.memory._changes import ENTITIES, KNOWERS, RELATIONS
from dmbot.memory.models import DM, name_key
from dmbot.memory.thread_suggestions import ThreadSuggestionStore, clues_from, extra_json, unkeep_in
from dmbot.ui import thread_questions as ui
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest

TEXT = (
    "Ilsa hunts for her lost brother. She carries his torn journal, and a warning carved in the "
    "door of his hut. Ilsa and Brann both read the warning."
)
IDS = {"Ilsa": "ilsa", "Brann": "brann"}
ENTRIES = {name_key(n): {i} for n, i in IDS.items()}
NAMES = {"ilsa": "Ilsa", "brann": "Brann"}


def hunt(*clues: tr.SaidClue) -> tr.Said:
    return tr.Said(
        tr.PURSUIT,
        "find her lost brother",
        "",
        "",
        "Ilsa",
        (),
        (),
        "Ilsa hunts for her lost brother",
        clues=clues,
    )


class TheReader(unittest.TestCase):
    def check(self, *clues: tr.SaidClue) -> tuple[tr.Suggestion, dict[str, int]]:
        (s,), counts = tr.check([hunt(*clues)], TEXT, ENTRIES, NAMES)
        return s, counts

    def test_a_clue_needs_its_quote_and_who_has_it_is_a_name_the_text_gives(self) -> None:
        s, counts = self.check(
            tr.SaidClue("his torn journal", ("Ilsa",), "She carries his torn journal"),
            tr.SaidClue("a warning", ("Ilsa", "Brann", "Nobody"), "a warning carved in the door"),
            tr.SaidClue("a map", (), "a map of the north"),  # (not in the text)
            tr.SaidClue("his torn journal", (), "his torn journal"),  # (the same words: one)
        )
        self.assertEqual(
            [(c.words, c.who_ids) for c in s.clues],
            [("his torn journal", ("ilsa",)), ("a warning", ("ilsa", "brann"))],
        )
        self.assertEqual(counts, {"clue_no_quote": 1})

    def test_at_most_six(self) -> None:
        many = [tr.SaidClue(f"clue {i}", (), "his torn journal") for i in range(9)]
        s, counts = self.check(*many)
        self.assertEqual(len(s.clues), tr.CLUES_MAX)
        self.assertEqual(counts, {"clue_over": 9 - tr.CLUES_MAX})

    def test_the_answer_and_the_schema_carry_clues(self) -> None:
        clue = {"words": "the journal", "who": ["Ilsa"], "quote": "j"}
        raw = {"threads": [{"shape": "pursuit", "aim": "a", "taker": "Ilsa", "quote": "q",
                            "clues": [clue]}]}  # fmt: skip
        (said,) = tr.parse(json.dumps(raw))
        self.assertEqual(said.clues, (tr.SaidClue("the journal", ("Ilsa",), "j"),))
        item = tr.SCHEMA["properties"]["threads"]["items"]
        self.assertIn("clues", item["required"])
        self.assertIn('"clues"', tr.INSTRUCTIONS)

    def test_stored_and_read_back_and_junk_reads_as_none(self) -> None:
        s, _ = self.check(tr.SaidClue("his torn journal", ("Ilsa",), "his torn journal"))
        self.assertEqual(clues_from(extra_json(s)), s.clues)
        self.assertEqual(clues_from("{"), ())
        self.assertEqual(clues_from({"clues": [{"who": ["x"]}]}), ())  # (no words: nothing)

    def test_the_card_line(self) -> None:
        line = tr.ReadClue("his torn journal", "q", ("ilsa", "gone"))
        self.assertEqual(
            ui.line_for(line, {"ilsa": "Ilsa"}), ("his torn journal", "🔎 Clue · held by Ilsa")
        )
        self.assertEqual(ui.line_for(tr.ReadClue("a mark", "q"), {}), ("a mark", "🔎 Clue"))


class TheSession(unittest.TestCase):
    def test_a_sessions_clues_wait_for_6c(self) -> None:
        from dmbot.memory import session_threads as st

        s, _ = TheReader().check(tr.SaidClue("his torn journal", (), "his torn journal"))
        self.assertTrue(s.clues)
        self.assertEqual(st._no_words(s, (1,), (2,)).clues, ())


class TheKeep(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ThreadSuggestionStore(self.db)
        self.ilsa = await self.add(
            "Ilsa", type="character", role="player_character", played_by=DM_USER,
            status="confirmed",
        )  # fmt: skip
        self.brann = await self.add("Brann", type="character", role="npc", status="confirmed")

    async def offer(self) -> None:
        s = tr.Suggestion(
            "quest", "Ilsa's search", "find her lost brother", "Ilsa hunts",
            ((tr.TAKER, self.ilsa),), tr.STATE["quest"],
            clues=(
                tr.ReadClue("his torn journal", "his torn journal", (self.ilsa,)),
                tr.ReadClue("a warning on the door", "a warning carved"),
            ),
        )  # fmt: skip
        self.assertEqual(await self.store.offer(GUILD_A, self.c, [s], "Notes", 5), 1)

    async def keep(self, leave_off: list[int] | None = None) -> tuple[str, int]:
        (s,) = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual([c.words for c in s.clues], ["his torn journal", "a warning on the door"])
        self.assertEqual(len(s.lines), 2)
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, s.name, s.kind, s.state, aim=s.aim, parts=list(s.parts),
            source=DM, suggestion_id=s.id, leave_off=leave_off or [],
        )  # fmt: skip
        assert kept.batch is not None
        return kept.value.id, kept.batch

    async def clues(self, thread: str) -> list[tc.ThreadClue]:
        return await self.memory.thread_clues(GUILD_A, self.c, thread, gate.Viewer.the_dm())

    async def test_kept_with_the_thread_in_one_change_and_one_undo(self) -> None:
        await self.offer()
        thread, batch = await self.keep()
        got = await self.clues(thread)
        self.assertEqual(
            [(c.name, c.found, c.false_lead) for c in got],
            [("a warning on the door", False, False), ("his torn journal", True, False)],
        )
        journal = next(c.clue_id for c in got if c.name == "his torn journal")
        async with self.memory._read(GUILD_A, self.c) as scope:
            rows = {r["id"]: r for r in await scope.select(ENTITIES, " AND type = 'clue'")}
        self.assertTrue(all(r["hidden_from_players"] for r in rows.values()))
        self.assertTrue(rows[journal]["audience_limited"])  # (ruling 3: the text names who has it)
        self.assertEqual([e.id for e in await self.memory.clue_audience(GUILD_A, self.c, journal)],
                         [self.ilsa])  # fmt: skip
        warning = next(c.clue_id for c in got if c.name != "his torn journal")
        self.assertFalse(rows[warning]["audience_limited"])  # (nobody named: nobody has it yet)
        await self.memory.undo(GUILD_A, self.c, batch, after=unkeep_in(batch))
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)
        async with self.memory._read(GUILD_A, self.c) as scope:  # (the clues and who knew them too)
            self.assertEqual(await scope.select(ENTITIES, " AND type = 'clue'"), [])
            self.assertEqual(
                await scope.select(RELATIONS, " AND predicate IN ('clue_to', 'knows')"), []
            )
            self.assertEqual(await scope.select(KNOWERS, " AND about_entity_id = %s", [journal]),
                             [])  # fmt: skip

    async def test_the_campaigns_own_public_clue_keeps_its_audience_and_asks_nothing(self) -> None:
        """The reviewer's B2: a reused clue a player character already knows is never narrowed
        by a Keep (no 🤫 question); its holders are only added."""
        mine = await self.add("His torn journal", type="clue", status="confirmed")
        await self.memory.learn_statement(GUILD_A, self.c, gate.ENTRY, mine, self.ilsa, source=DM)
        await self.offer()
        await self.keep()
        async with self.memory._read(GUILD_A, self.c) as scope:
            (row,) = await scope.select(ENTITIES, " AND id = %s", [mine])
        self.assertFalse(row["audience_limited"])
        self.assertEqual(await self.memory.secret_questions_count(GUILD_A, self.c), 0)

    async def test_an_npc_holder_has_it_but_the_table_has_not_found_it(self) -> None:
        s = tr.Suggestion(
            "quest", "Ilsa's search", "find her lost brother", "Ilsa hunts",
            ((tr.TAKER, self.ilsa),), tr.STATE["quest"],
            clues=(tr.ReadClue("a warning on the door", "a warning carved", (self.brann,)),),
        )  # fmt: skip
        await self.store.offer(GUILD_A, self.c, [s], "Notes", 5)
        (w,) = await self.store.waiting(GUILD_A, self.c)
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, w.name, w.kind, w.state, aim=w.aim, parts=list(w.parts),
            source=DM, suggestion_id=w.id,
        )  # fmt: skip
        (only,) = await self.clues(kept.value.id)
        self.assertFalse(only.found)
        audience = await self.memory.clue_audience(GUILD_A, self.c, only.clue_id)
        self.assertEqual([e.id for e in audience], [self.brann])

    async def test_a_clue_left_off_is_never_kept_and_the_campaigns_own_is_reused(self) -> None:
        mine = await self.add("A warning on the door", type="clue", status="confirmed")
        await self.offer()
        thread, _ = await self.keep(leave_off=[0])
        (only,) = await self.clues(thread)
        self.assertEqual(only.clue_id, mine)


if __name__ == "__main__":
    unittest.main()
