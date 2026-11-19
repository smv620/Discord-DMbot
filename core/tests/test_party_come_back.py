"""A character coming back to the party (#4537, party knowledge slice 4a;
`docs/design/party_knowledge.md` §6; Supervisor's ruling on #4537, 2026-11-19): a new stretch with
its Undo, every row from before kept, and the question about the time away asked and answered as a
join's. Invented names and ids only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import join_questions
from dmbot.memory.gate import ENTRY
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, TooLateToUndo
from dmbot.ui import party_card, party_join
from dmbot.web.recaps import _scoped
from tests.test_party_join import CARA, GUILD, Party
from tests.test_web_relationships import Web


class TheWords(unittest.TestCase):
    def test_the_question_about_the_time_away_names_who_and_keeps_what_was_known(self) -> None:
        one = party_join.question_text(["Okthandom"], 12, back=True)
        self.assertTrue(
            one.startswith(
                "**Okthandom is back in the party.** What does Okthandom know about what "
                "happened while away?"
            )
        )
        self.assertIn("knows only what was known before leaving", one)
        self.assertIn("leave out the 12 things Okthandom doesn't know yet.", one)
        self.assertNotIn("backstory", one)  # (not a newcomer: everything from before is kept)
        two = party_join.question_text(["Okthandom", "Vell"], 0, back=True)
        self.assertIn("Okthandom and Vell are back in the party.", two)
        self.assertIn("what each knew before leaving", two)
        self.assertNotIn("leave out", two)
        for text in (one, two, party_join.BEFORE_DONE_ONE, party_card.RETURNED):
            for word in ("she", "he", "her", "him", "his", "they", "them", "their"):
                self.assertNotIn(f" {word} ", f" {text} ")  # (ruling C: named, no pronoun)

    def test_the_buttons_are_short_and_not_an_undo(self) -> None:
        self.assertLessEqual(len(party_join.BEFORE_BUTTON), 26)  # (no name: phones cut at ~25)
        self.assertFalse(party_card.RETURN_BUTTON.startswith("↩️"))  # (↩️ is an Undo here)


class Away(Party):
    """Vell and Tuck played session 1; Tuck left; the party learned the bell while Tuck was away."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 11_000
        await self.memory.leave_party(GUILD, self.c, self.tuck, "away")
        self.now = 12_000
        made = await self.memory.add_entity(
            GUILD, self.c, name="The drowned bell", type="quest", status=CONFIRMED, source=DM
        )
        self.bell = str(made.value.id)  # (shown while Tuck was away: Vell alone knows it)
        self.now = 13_000

    async def rows(self) -> set[tuple[Any, ...]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT perspective_id, about_entity_id, state_id, relation_id, alias_id,"
                " confidence FROM memory_knowers WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.c),
            )
            return {tuple(r.values()) for r in await cur.fetchall()}

    async def questions(self) -> list[join_questions.Question]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                f"SELECT {join_questions._COLUMNS} FROM memory_join_questions"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY created_at, id",
                (GUILD, self.c),
            )
            return [join_questions._question(r) for r in await cur.fetchall()]


class ComingBack(Away):
    async def test_back_keeps_everything_and_asks_about_the_time_away(self) -> None:
        self.assertFalse(await self.sees(self.tuck, ENTRY, self.bell))
        before = await self.rows()
        done = await self.memory.back_in_party(GUILD, self.c, self.tuck)
        span, waiting = done.value
        self.assertEqual((span.from_at, span.to_at, waiting), (13_000, None, join_questions.BACK))
        self.assertIn(self.tuck, await self.memory.current_party(GUILD, self.c))
        self.assertEqual(await self.rows(), before)  # (nothing written until the DM answers)
        self.assertTrue(await self.sees(self.tuck, ENTRY, self.npc))  # (kept from before)
        [q] = await self.questions()
        self.assertEqual(
            (q.entity_id, q.kind, q.status), (self.tuck, join_questions.BACK, "waiting")
        )
        spans = await self.memory.party_spans(GUILD, self.c, self.tuck)
        self.assertEqual([s.open for s in spans], [False, True])  # (the leave stays on record)

    async def test_what_the_party_knows_catches_up_once_and_undoes_to_the_rows_before(self) -> None:
        await self.memory.back_in_party(GUILD, self.c, self.tuck)
        before = await self.rows()
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        self.assertEqual(q.kind, join_questions.BACK)
        count = await self.memory.party_known_count(GUILD, self.c, [self.tuck])
        done = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertEqual(done.value, count)  # (the number shown is the number copied)
        self.assertGreater(count, 0)
        self.assertTrue(await self.sees(self.tuck, ENTRY, self.bell))
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.rows(), before)

    async def test_only_what_was_known_before_writes_nothing(self) -> None:
        await self.memory.back_in_party(GUILD, self.c, self.tuck)
        before = await self.rows()
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        [answered] = await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        self.assertEqual(answered.kind, join_questions.BACK)
        self.assertEqual(await self.rows(), before)

    async def test_undoing_the_return_closes_it_again_and_takes_the_question(self) -> None:
        before = await self.rows()
        done = await self.memory.back_in_party(GUILD, self.c, self.tuck)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertNotIn(self.tuck, await self.memory.current_party(GUILD, self.c))
        self.assertEqual(await self.questions(), [])
        self.assertEqual(await self.rows(), before)
        spans = await self.memory.party_spans(GUILD, self.c, self.tuck)
        self.assertEqual([s.open for s in spans], [False])

    async def test_refused_for_one_in_the_party_or_not_a_player_character(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.back_in_party(GUILD, self.c, self.vell)
        with self.assertRaises(MemoryRuleError):
            await self.memory.back_in_party(GUILD, self.c, self.npc)

    async def test_alone_in_the_party_nothing_is_asked(self) -> None:
        await self.memory.leave_party(GUILD, self.c, self.vell, "left")
        await self.memory.back_in_party(GUILD, self.c, self.tuck)
        self.assertEqual(await self.questions(), [])

    async def test_a_join_and_a_return_go_as_two_messages(self) -> None:
        cara = await self.pc("Cara", CARA)
        self.now = 13_100
        await self.memory.back_in_party(GUILD, self.c, self.tuck)
        first = await self.memory.join_questions_to_send(GUILD, self.c)
        second = await self.memory.join_questions_to_send(GUILD, self.c)
        self.assertEqual([(q.entity_id, q.kind) for q in first], [(cara, join_questions.JOIN)])
        self.assertEqual(
            [(q.entity_id, q.kind) for q in second], [(self.tuck, join_questions.BACK)]
        )
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])

    async def test_switch_off_gives_what_the_party_knows_at_once(self) -> None:
        self.memory.party_join = False
        await self.memory.back_in_party(GUILD, self.c, self.tuck)
        self.assertTrue(await self.sees(self.tuck, ENTRY, self.bell))
        self.assertEqual(await self.questions(), [])


class ComingBackEdges(Away):
    async def test_one_who_left_before_their_join_question_keeps_it(self) -> None:
        cara = await self.pc("Cara", CARA)  # (a join question waits, unanswered)
        self.now = 13_500
        await self.memory.leave_party(GUILD, self.c, cara, "left")
        self.now = 14_000
        done = await self.memory.back_in_party(GUILD, self.c, cara)
        self.assertEqual(done.value[1], join_questions.JOIN)  # (it already covers the time away)
        self.assertEqual(
            [(q.entity_id, q.kind) for q in await self.questions()], [(cara, join_questions.JOIN)]
        )

    async def test_a_return_answered_with_a_copy_is_undone_after_the_copy(self) -> None:
        back = await self.memory.back_in_party(GUILD, self.c, self.tuck)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None and back.batch is not None
        copy = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        assert copy.batch is not None
        with self.assertRaises(TooLateToUndo):  # (never out of the party holding the catch-up)
            await self.memory.undo(GUILD, self.c, back.batch)
        self.assertIn(self.tuck, await self.memory.current_party(GUILD, self.c))
        await self.memory.undo(GUILD, self.c, copy.batch)
        await self.memory.undo(GUILD, self.c, back.batch)  # (the copy's Undo first, then this)
        self.assertNotIn(self.tuck, await self.memory.current_party(GUILD, self.c))

    async def test_a_return_answered_only_what_was_known_before_undoes(self) -> None:
        back = await self.memory.back_in_party(GUILD, self.c, self.tuck)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None and back.batch is not None
        await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        await self.memory.undo(GUILD, self.c, back.batch)
        self.assertNotIn(self.tuck, await self.memory.current_party(GUILD, self.c))


class TheCardsButton(unittest.IsolatedAsyncioTestCase):
    """🤝 Back in the party in Discord: the words follow what the store says waits, the question
    goes at once (the sender itself never sends during play), and a refusal is worded by why."""

    async def press(self, outcome: Any) -> tuple[list[str], list[str]]:
        from types import SimpleNamespace
        from unittest import mock

        edits: list[str] = []
        sent: list[str] = []

        async def back_in_party(g: int, c: str, e: str) -> Any:
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(value=(None, outcome), batch=7)

        memory = SimpleNamespace(
            back_in_party=back_in_party,
            party_spans=mock.AsyncMock(return_value=[SimpleNamespace(open=True)]),
        )
        bot = SimpleNamespace(memory=memory)
        campaign = SimpleNamespace(guild_id=1, id="c" * 32)

        async def edit(_i: Any, text: str, view: Any = None) -> None:
            edits.append(text)

        async def send(_b: Any, g: int, c: str) -> int:
            sent.append(c)
            return 1

        with (
            mock.patch.object(party_card, "_answer_first", mock.AsyncMock()),
            mock.patch.object(party_card, "_bot", return_value=bot),
            mock.patch.object(party_card, "_dm_campaign", mock.AsyncMock(return_value=campaign)),
            mock.patch.object(party_card, "_name", mock.AsyncMock(return_value="Tuck")),
            mock.patch.object(party_card, "_edit", edit),
            mock.patch.object(party_join, "send_join_questions", send),
        ):
            await party_card.come_back(mock.Mock(), "c" * 32, "e" * 32)
        return edits, sent

    async def test_a_question_about_the_time_away_is_promised_and_sent(self) -> None:
        edits, sent = await self.press(join_questions.BACK)
        self.assertEqual(edits, [party_card.RETURNED.format(name="Tuck")])
        self.assertEqual(sent, ["c" * 32])

    async def test_alone_nothing_is_promised_or_sent(self) -> None:
        edits, sent = await self.press(None)
        self.assertEqual(edits, [party_card.RETURNED_ALONE.format(name="Tuck")])
        self.assertEqual(sent, [])

    async def test_a_waiting_join_question_is_named_as_such(self) -> None:
        edits, _ = await self.press(join_questions.JOIN)
        self.assertEqual(edits, [party_card.RETURNED_STILL_NEW.format(name="Tuck")])

    async def test_a_refusal_in_the_party_says_already_back(self) -> None:
        edits, sent = await self.press(MemoryRuleError("That character is in the party now."))
        self.assertEqual(edits, [party_card.BACK_ALREADY.format(name="Tuck")])
        self.assertEqual(sent, [])


class TheWebDoorSaysWhichQuestion(Web):
    bob_has_a_hero = False

    async def test_a_coming_back_is_listed_with_its_kind(self) -> None:
        tavin = await self.add("Tavin", "character", role="player_character")
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id,"
                " group_id, sent_at, created_at, kind) VALUES (%s, %s, %s, %s, %s, 1, 1,"
                " 'back')",
                (self.g, self.one.id, "b" * 32, tavin, "a" * 32),
            )
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_join_questions(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(
            found["r"],
            [{"group": "a" * 32, "answer": "waiting", "kind": "back", "names": ["Tavin"]}],
        )


if __name__ == "__main__":
    unittest.main()
