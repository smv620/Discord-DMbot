"""A goodbye or a death: "Still in the party?" (#4537, party knowledge slice 6a;
`docs/design/party_knowledge.md` §4 "Readers only suggest", §6 and §11; Supervisor's ruling on
slice 6, 2026-11-21). Invented names and ids only."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignStore
from dmbot.memory import join_questions, party_record
from dmbot.memory.join_questions import DIED, AlreadyAnswered
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore
from dmbot.questions import kinds
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import party_leave
from tests.pg import DatabaseTest

EXTRACTOR = "extractor"  # (a session reader, not the DM)
GUILD = 700_000_000_000_000_061
DM_ID, ANNA, BEN = (700_000_000_000_000_601, 700_000_000_000_000_602, 700_000_000_000_000_603)


class TheWords(unittest.TestCase):
    def test_the_rulings_words_with_no_session_and_no_pronoun(self) -> None:
        text = party_leave.line_for(DIED, "Okthandom")
        self.assertEqual(
            text, "**Okthandom is marked dead.** Mark Okthandom as no longer in the party?"
        )
        self.assertNotIn("session", text)  # (ruling S: a card edit between sessions asks too)
        goodbye = party_leave.line_for(join_questions.LEAVE, "Okthandom")
        for line in (text, goodbye, party_leave.LEFT_DONE.format(name="Okthandom")):
            for word in ("she", "he", "her", "him", "his", "they", "them"):  # (ruling C)
                self.assertNotIn(f" {word} ", f" {line} ")

    def test_still_in_the_party_is_a_plain_answer(self) -> None:
        # (ruling S: characters come back in this game, so "still in the party" never warns)
        self.assertEqual(
            party_leave.STAYS_DONE.format(name="Vell"), "✅ Vell is still in the party."
        )

    def test_a_button_keeps_its_action_on_a_phone(self) -> None:
        self.assertEqual(
            party_leave._label(party_leave.LEFT_BUTTON, "Vell", False), "Left the party"
        )
        self.assertEqual(party_leave._label(party_leave.LEFT_BUTTON, "Vell", True), "Vell: left")
        long = party_leave._label(party_leave.STAYS_BUTTON, "Bartholomew Thistlewood", True)
        self.assertTrue(long.endswith(": stays"))
        self.assertLessEqual(len(long), 25)

    def test_each_character_has_its_own_row_and_later_comes_last(self) -> None:
        people = [(f"{i:032x}", f"N{i}") for i in range(6)]
        view = party_leave.question_view("c" * 32, people)
        rows = [item.row for item in view.children]
        self.assertEqual(rows, [0, 0, 1, 1, 2, 2, 3, 3, 4])  # (four characters, then "Later")

    def test_its_own_kind_on_the_questions_list(self) -> None:
        self.assertEqual(kinds.LABELS[kinds.LEAVES], "Still in the party?")
        self.assertIn(kinds.LEAVES, kinds.ORDER)


class TheMessageAfterAnAnswer(unittest.IsolatedAsyncioTestCase):
    """One answered, the rest of the same message stay with their buttons (the ux-critic)."""

    async def test_the_others_still_waiting_stay_under_the_confirmation(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        vell, tuck = "a" * 32, "b" * 32
        group = [
            SimpleNamespace(entity_id=vell, kind=DIED, status=join_questions.LEFT),
            SimpleNamespace(entity_id=tuck, kind=DIED, status="waiting"),
        ]
        names = {vell: "Vell", tuck: "Tuck"}
        memory = SimpleNamespace(
            staying_group_for=AsyncMock(return_value="g" * 32),
            join_question_group=AsyncMock(return_value=group),
            entity=AsyncMock(side_effect=lambda g, c, e: SimpleNamespace(name=names[e])),
        )
        bot = SimpleNamespace(memory=memory)
        campaign = SimpleNamespace(guild_id=GUILD, id="c" * 32)
        text, view = await party_leave._after_answer(bot, campaign, vell, "✅ Vell has left.")
        self.assertTrue(text.startswith("✅ Vell has left.\n\n**Tuck is marked dead.**"))
        self.assertEqual(
            [item.custom_id[:14] for item in view.children][:2],
            ["dmbot:pleavel:", "dmbot:pleaves:"],
        )
        many = [
            SimpleNamespace(entity_id=f"{i:032x}", kind=DIED, status="waiting") for i in range(6)
        ]
        memory.join_question_group = AsyncMock(return_value=many)
        memory.entity = AsyncMock(return_value=SimpleNamespace(name="Someone"))
        shown = await party_leave.waiting_in(bot, campaign, "g" * 32)
        assert shown is not None
        self.assertIn("Buttons for the first 4 here.", shown[0])
        memory.join_question_group = AsyncMock(return_value=group)
        memory.entity = AsyncMock(side_effect=lambda g, c, e: SimpleNamespace(name=names[e]))
        group[1].status = "stays"
        text, view = await party_leave._after_answer(bot, campaign, vell, "✅ Vell has left.")
        self.assertEqual((text, view), ("✅ Vell has left.", None))


class Party(DatabaseTest):
    """A campaign with two player characters, Vell and Tuck, after one session."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 10_000
        self.memory = MemoryStore(self.db, clock=lambda: float(self.now))
        self.c = (await CampaignStore(self.db).create(GUILD, "Saltmarsh", DM_ID)).id
        self.sessions = TranscriptStore(self.db)
        self.vell = await self.pc("Vell", ANNA)
        self.tuck = await self.pc("Tuck", BEN)
        await self.played(5_000, 6_000)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, name=name, type="character", role=PLAYER_CHARACTER, played_by=player,
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def played(self, start: int, end: int | None) -> str:
        sid = await self.sessions.open_session(GUILD, self.c, start)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3 WHERE id = %s", (sid,)
            )
        if end is not None:
            await self.sessions.end_session(GUILD, sid, end)
        return sid

    async def dies(self, entity_id: str, source: str = DM) -> int | None:
        done = await self.memory.set_condition(GUILD, self.c, entity_id, "dead", source=source)
        return done.batch

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT entity_id, kind, status, sent_at FROM memory_join_questions"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY created_at, id",
                (GUILD, self.c),
            )
            return [
                (r["entity_id"], r["kind"], r["status"], r["sent_at"]) for r in await cur.fetchall()
            ]

    async def open_stretches(self) -> set[str]:
        return set(await self.memory.current_party(GUILD, self.c))


class ADeath(Party):
    async def test_a_death_asks_after_play_once_and_closes_nothing(self) -> None:
        sid = await self.played(9_000, None)  # (a session is running)
        await self.dies(self.vell)
        self.assertEqual(await self.rows(), [(self.vell, DIED, "waiting", None)])
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})  # (§11)
        self.assertEqual(await self.memory.staying_questions_waiting(GUILD, self.c), (1, 10_000))
        self.assertEqual(await self.memory.staying_questions_to_send(GUILD, self.c), [])  # (play)
        await self.sessions.end_session(GUILD, sid, 9_500)
        sent = await self.memory.staying_questions_to_send(GUILD, self.c)
        self.assertEqual([(q.entity_id, q.kind) for q in sent], [(self.vell, DIED)])
        self.assertEqual(await self.memory.staying_questions_to_send(GUILD, self.c), [])  # (once)
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])  # (its own)

    async def test_left_the_party_closes_with_the_cards_call_and_its_undo_asks_again(
        self,
    ) -> None:
        await self.dies(self.vell)
        done = await self.memory.staying_answer_left(GUILD, self.c, self.vell)
        self.assertEqual(done.value.ended, "died")
        self.assertEqual(await self.open_stretches(), {self.tuck})
        self.assertEqual((await self.rows())[0][2], join_questions.LEFT)
        with self.assertRaises(AlreadyAnswered):
            await self.memory.staying_answer_left(GUILD, self.c, self.vell)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})
        self.assertEqual((await self.rows())[0][2], "waiting")

    async def test_still_in_the_party_writes_nothing_and_isnt_asked_again(self) -> None:
        await self.dies(self.vell)
        await self.memory.staying_answer_stays(GUILD, self.c, self.vell)
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})
        self.assertEqual(await self.memory.staying_questions_waiting(GUILD, self.c), (0, None))
        with self.assertRaises(AlreadyAnswered):
            await self.memory.staying_answer_stays(GUILD, self.c, self.vell)

    async def test_undoing_the_death_withdraws_only_a_waiting_question(self) -> None:
        batch = await self.dies(self.vell)
        assert batch is not None
        await self.memory.undo(GUILD, self.c, batch)
        self.assertEqual(await self.rows(), [])
        # (an answered one stays answered: "Left the party" has its own Undo, ruling S)
        batch = await self.dies(self.tuck)
        await self.memory.staying_answer_left(GUILD, self.c, self.tuck)
        assert batch is not None
        await self.memory.undo(GUILD, self.c, batch)
        self.assertEqual([r[2] for r in await self.rows()], [join_questions.LEFT])

    async def test_brought_back_by_a_later_condition_withdraws_a_waiting_one(self) -> None:
        await self.dies(self.vell)
        await self.memory.set_condition(GUILD, self.c, self.vell, "alive", source=DM)  # (Revivify)
        self.assertEqual(await self.rows(), [])

    async def test_the_cards_own_button_answers_it(self) -> None:
        await self.dies(self.vell)
        await self.memory.leave_party(GUILD, self.c, self.vell, "died")
        self.assertEqual((await self.rows())[0][2], join_questions.LEFT)

    async def test_an_npc_or_one_out_of_the_party_isnt_asked(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.c, name="Harbourmaster", type="character", status=CONFIRMED, source=DM
        )
        await self.dies(str(made.value.id))
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        await self.dies(self.tuck)
        self.assertEqual(await self.rows(), [])

    async def test_a_join_question_and_a_death_wait_side_by_side(self) -> None:
        self.memory.party_join = True
        cara = await self.pc("Cara", 700_000_000_000_000_604)
        await self.dies(cara)
        self.assertEqual({r[1] for r in await self.rows()}, {join_questions.JOIN, DIED})
        async with self.db.guild(GUILD) as conn:  # (the join's own reads see only the join)
            self.assertEqual(await join_questions.waiting_entities(conn, GUILD, self.c), {cara})
        self.assertEqual(await self.memory.join_questions_waiting(GUILD, self.c), (1, 10_000))
        self.assertEqual(await self.memory.staying_questions_waiting(GUILD, self.c), (1, 10_000))

    async def test_from_the_first_session_leaves_a_death_question_alone(self) -> None:
        # (the review's case: one who joined and died; the join's own answer answers the join only)
        self.memory.party_join = True
        cara = await self.pc("Cara", 700_000_000_000_000_604)
        batch = await self.dies(cara)
        await self.memory.join_from_first_session(GUILD, self.c, cara)
        self.assertEqual(await self.memory.staying_questions_waiting(GUILD, self.c), (1, 10_000))
        assert batch is not None
        await self.memory.undo(GUILD, self.c, batch)  # (not refused: the death had no copy)
        self.assertEqual(await self.memory.staying_questions_waiting(GUILD, self.c), (0, None))

    async def test_two_dms_answer_one_wins_the_other_is_told(self) -> None:
        await self.dies(self.vell)
        await self.memory.staying_answer_stays(GUILD, self.c, self.vell)
        with self.assertRaises(AlreadyAnswered):
            await self.memory.staying_answer_left(GUILD, self.c, self.vell)
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})  # (stays won)
        await self.dies(self.tuck)
        await self.memory.staying_answer_left(GUILD, self.c, self.tuck)
        with self.assertRaises(AlreadyAnswered):
            await self.memory.staying_answer_stays(GUILD, self.c, self.tuck)

    async def test_the_join_prompt_and_reads_ignore_a_death_question(self) -> None:
        await self.dies(self.vell)
        self.assertFalse(await self.memory.join_question_waiting_for(GUILD, self.c, self.vell))
        async with self.memory._write(GUILD, self.c, DM) as w:  # (withdraw touches its kind only)
            await join_questions.ask(w, [self.tuck], join_questions.LEAVE)
            await join_questions.withdraw(w, self.tuck, DIED)
        self.assertEqual({r[1] for r in await self.rows()}, {DIED, join_questions.LEAVE})

    async def test_the_latest_message_of_a_character_is_found(self) -> None:
        await self.dies(self.vell)
        [sent] = await self.memory.staying_questions_to_send(GUILD, self.c)
        self.assertEqual(
            await self.memory.staying_group_for(GUILD, self.c, self.vell), sent.group_id
        )

    async def test_a_readers_death_asks_the_dm_too(self) -> None:
        # (the question is the DM's own confirmation: a reader's "dead" never closes anything)
        await self.dies(self.vell, EXTRACTOR)
        self.assertEqual([r[1] for r in await self.rows()], [DIED])
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})


class NeverClosesByItself(Party):
    """§11 and Supervisor's ruling: only the DM's answer or the card's button ends a stretch.
    A whole session with a death and a goodbye question: nothing calls `close_span`."""

    async def test_a_session_with_a_death_and_a_goodbye_closes_no_stretch(self) -> None:
        real = party_record.close_span
        calls: list[str] = []

        async def counted(*args: Any, **kwargs: Any) -> Any:
            calls.append("close_span")
            return await real(*args, **kwargs)

        with patch.object(party_record, "close_span", counted):
            sid = await self.played(9_000, None)
            await self.dies(self.vell, EXTRACTOR)  # (a reader's death during play)
            async with self.memory._write(
                GUILD, self.c, EXTRACTOR
            ) as w:  # (a goodbye, as 6b will ask)
                await join_questions.ask(w, [self.tuck], join_questions.LEAVE)
            await self.sessions.end_session(GUILD, sid, 9_500)
            await self.memory.staying_questions_to_send(GUILD, self.c)
            await self.memory.staying_questions_to_send(GUILD, self.c)
        self.assertEqual(calls, [])
        self.assertEqual(await self.open_stretches(), {self.vell, self.tuck})
        self.assertEqual({r[1] for r in await self.rows()}, {DIED, join_questions.LEAVE})


class TheWebsiteDoor(Party):
    async def test_the_websites_join_door_keeps_to_joins_until_its_half(self) -> None:
        await self.dies(self.vell)
        await self.memory.staying_questions_to_send(GUILD, self.c)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
                " WHERE p.proname = 'dmbot_web_join_questions'"
                " AND p.pronamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIn("q.kind IN ('join', 'back')", row["d"])


if __name__ == "__main__":
    unittest.main()
