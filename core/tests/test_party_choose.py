"""The join question's "Let me choose" (#4537, party knowledge slice 4c;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, G to J; the ux-critic's words):
the question's characters are caught up a session or a story thread at a time, each pick its own
change with its own Undo, the first answering the question. Invented names and ids only."""

from __future__ import annotations

import re
import unittest
from typing import Any

from dmbot.memory import join_questions
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, TooLateToUndo
from dmbot.ui import party_choose
from tests.test_party_join import CARA, GUILD, Party


class TheWords(unittest.TestCase):
    def test_counts_are_for_each_character(self) -> None:
        names = {"m": "Mira", "t": "Tavin"}
        self.assertEqual(
            party_choose.count_words({"m": 14}, names), "14 things Mira doesn't know yet"
        )
        self.assertEqual(party_choose.count_words({"m": 14, "t": 9}, names), "Mira 14 · Tavin 9")
        self.assertEqual(
            party_choose.done_text({"m": 14, "t": 9}, names, "session 3"),
            "✅ From session 3, Mira now knows 14 more things and Tavin 9. Nothing was sent to the"
            " players.",
        )
        self.assertEqual(
            party_choose.done_text({"m": 1}, names, "session 3"),
            "✅ Mira now knows 1 thing the rest of the party knew from session 3. Nothing was sent"
            " to Mira's player.",
        )

    def test_the_page_says_nothing_changes_and_why_lore_isnt_under_a_session(self) -> None:
        one = party_choose.page_text(["Mira"], back=False, sessions_cut=False, threads_cut=True)
        self.assertTrue(
            one.startswith("**Choose what Mira knows.** Nothing changes until you pick.")
        )
        self.assertIn("Until then Mira knows only what everyone knows.", one)
        self.assertIn("aren't part of any session", one)
        self.assertIn("Showing the 25 story threads", one)
        self.assertNotIn("last 25 sessions", one)
        back = party_choose.page_text(["Okthandom", "Vell"], back=True, sessions_cut=True,
                                      threads_cut=False)  # fmt: skip
        self.assertIn("Until then each knows only what each knew before leaving.", back)
        pronoun = re.compile(r"\b(she|he|her|hers|him|his|they|them|their)\b", re.IGNORECASE)
        words = [v for k, v in vars(party_choose).items() if k.isupper() and isinstance(v, str)]
        for text in [*words, one, back]:
            self.assertIsNone(pronoun.search(text), text)  # (ruling C: named, no pronoun)


class LetMeChoose(Party):
    """Session 1 (5_000) gave the party a connection; the drowned bell is a quest they know. Cara
    joins afterwards and is asked about."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bell = await self.made("The drowned bell", "quest")
        self.kesh = await self.made("Kesh", "npc")
        rel = await self.memory.add_relation(
            GUILD, self.c, self.kesh, "knows", self.npc, source=DM, confidence=1.0,
            status=CONFIRMED, from_session_at=5_000,
        )  # fmt: skip
        self.met = str(rel.value[0].id)  # (came up in session 1)
        self.cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        self.group = q.group_id

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

    async def status(self) -> str:
        [q] = await self.memory.join_question_group(GUILD, self.c, self.group)
        return q.status

    async def test_the_page_lists_what_has_something_to_give(self) -> None:
        choices = await self.memory.join_choices(GUILD, self.c, self.group)
        self.assertEqual(choices.members, [self.cara])
        [(at, number, each)] = choices.sessions
        self.assertEqual((at, number), (5_000, 1))
        self.assertEqual(set(each), {self.cara})
        self.assertIn(self.bell, [t for t, _, _ in choices.threads])

    async def test_a_session_pick_answers_and_its_undo_puts_everything_back(self) -> None:
        before = await self.rows()
        done = await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        told, first = done.value
        self.assertTrue(first)
        self.assertGreater(told[self.cara], 0)
        self.assertTrue(await self.sees(self.cara, "connection", self.met))
        self.assertEqual(await self.status(), join_questions.CHOSEN)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.rows(), before)
        self.assertEqual(await self.status(), join_questions.WAITING)  # (asked again)

    async def test_a_later_pick_only_adds_and_its_undo_takes_only_what_it_gave(self) -> None:
        one = await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        after_one = await self.rows()
        two = await self.memory.join_choose(GUILD, self.c, self.group, thread_id=self.bell)
        self.assertFalse(two.value[1])  # (not the one that answered)
        self.assertTrue(await self.sees(self.cara, "entry", self.bell))
        assert two.batch is not None and one.batch is not None
        await self.memory.undo(GUILD, self.c, two.batch)
        self.assertEqual(await self.rows(), after_one)
        self.assertEqual(await self.status(), join_questions.CHOSEN)

    async def test_lore_is_never_in_a_session_pick(self) -> None:
        await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        self.assertFalse(await self.sees(self.cara, "entry", self.bell))  # (no session: a thread)

    async def test_what_the_party_knows_may_follow_picks(self) -> None:
        await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        await self.memory.join_answer_party(GUILD, self.c, self.group)
        self.assertTrue(await self.sees(self.cara, "entry", self.bell))
        self.assertEqual(await self.status(), "party")

    async def test_the_join_cannot_be_undone_under_a_pick(self) -> None:
        joined = await self.memory.join_question_group(GUILD, self.c, self.group)
        await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT batch FROM memory_join_questions WHERE guild_id = %s AND id = %s",
                (GUILD, joined[0].id),
            )
            row = await cur.fetchone()
        assert row is not None and row["batch"] is not None
        # (out of the party, holding what was chosen: refused, by the question's rule or the change
        # log's own "changed again since")
        with self.assertRaises((TooLateToUndo, MemoryRuleError)):
            await self.memory.undo(GUILD, self.c, int(row["batch"]))

    async def test_the_count_shown_is_what_is_given(self) -> None:
        dan = await self.pc("Dan", 700_000_000_000_000_105)
        await self.memory.unclaim_join_questions(GUILD, self.c, self.group)
        [q, *_] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        choices = await self.memory.join_choices(GUILD, self.c, q.group_id)
        self.assertEqual(set(choices.members), {self.cara, dan})
        [(_, _, shown)] = choices.sessions
        done = await self.memory.join_choose(GUILD, self.c, q.group_id, session_at=5_000)
        self.assertEqual(done.value[0], shown)

    async def test_a_pick_after_only_what_everyone_knows_leaves_that_answer(self) -> None:
        await self.memory.join_answer_everyone(GUILD, self.c, self.group)
        done = await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        self.assertFalse(done.value[1])  # (not answered by the pick: it only adds)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.status(), "everyone")

    async def test_the_first_pick_waits_for_the_later_ones_undo(self) -> None:
        one = await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        two = await self.memory.join_choose(GUILD, self.c, self.group, thread_id=self.bell)
        assert one.batch is not None and two.batch is not None
        with self.assertRaises(TooLateToUndo):
            await self.memory.undo(GUILD, self.c, one.batch)
        await self.memory.undo(GUILD, self.c, two.batch)
        await self.memory.undo(GUILD, self.c, one.batch)
        self.assertEqual(await self.status(), join_questions.WAITING)

    async def test_a_first_pick_with_nothing_to_give_changes_nothing(self) -> None:
        # (a thread the party doesn't know yet: hidden, so there is nothing to give from it)
        made = await self.memory.add_entity(
            GUILD, self.c, name="A quiet hook", type="hook", status=CONFIRMED, source=DM,
            hidden_from_players=True,
        )  # fmt: skip
        done = await self.memory.join_choose(
            GUILD, self.c, self.group, thread_id=str(made.value.id)
        )
        self.assertEqual((done.value, done.batch), (({}, False), None))
        self.assertEqual(await self.status(), join_questions.WAITING)

    async def test_nothing_left_writes_nothing(self) -> None:
        await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        again = await self.memory.join_choose(GUILD, self.c, self.group, session_at=5_000)
        self.assertEqual((again.value[0], again.batch), ({}, None))


if __name__ == "__main__":
    unittest.main()
