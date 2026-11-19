"""A character coming back to the party (#4537, party knowledge slice 4a;
`docs/design/party_knowledge.md` §6; Supervisor's ruling on #4537, 2026-11-19): a new stretch with
its Undo, every row from before kept, and the question about the time away asked and answered as a
join's. Invented names and ids only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import join_questions
from dmbot.memory.gate import ENTRY
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError
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


class ComingBack(Party):
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

    async def test_back_keeps_everything_and_asks_about_the_time_away(self) -> None:
        self.assertFalse(await self.sees(self.tuck, ENTRY, self.bell))
        before = await self.rows()
        done = await self.memory.back_in_party(GUILD, self.c, self.tuck)
        self.assertEqual((done.value.from_at, done.value.to_at), (13_000, None))
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
