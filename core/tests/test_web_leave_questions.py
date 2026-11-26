"""The website's "Still in the party?" (#4895, migration 0572): only a DM reads or answers it, only
a question already sent and still waiting for a character in the party shows (by names), "Left the
party" is one change whose Undo (the existing `join-undo`, this DM's own) asks the question again,
"Still in the party" writes only the answer, and a second answer finds `gone`. Postgres. Invented
names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.web import join_questions, leave_questions, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web

GROUP = "c" * 32


class LeavingOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.tavin = await self.add("Tavin", "character", role="player_character")

    async def ask(
        self, entity: str, kind: str = "died", *, sent: bool = True, status: str = "waiting"
    ) -> None:
        now = int(time.time())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id, kind,"
                " status, group_id, sent_at, created_at) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, %s, %s, %s, %s, %s)",
                (
                    self.g, self.one.id, entity, kind, status, GROUP if sent else None,
                    now if sent else None, now,
                ),
            )  # fmt: skip

    async def bot_loop(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, action, actions=self.actions)
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def answer(self, character: str, how: str, who: Any = None) -> str:
        return await leave_questions.queue_answer(
            self.web, who or self.session, self.cref, {"character": character, "answer": how}
        )

    async def read(self) -> dict[str, Any]:
        return await leave_questions.read(self.web, self.session, self.cref)

    async def status_of(self, entity: str) -> str:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT status FROM memory_join_questions WHERE entity_id = %s"
                " ORDER BY created_at DESC LIMIT 1",
                (entity,),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["status"])

    async def test_a_dm_reads_the_waiting_questions_by_character(self) -> None:
        await self.ask(self.tavin)
        got = await self.read()
        self.assertEqual(
            got,
            {"groups": [{"group": GROUP, "kind": "died",
                         "characters": [{"id": self.tavin, "name": "Tavin"}]}]},
        )  # fmt: skip

    async def test_a_goodbye_is_its_own_kind_and_a_join_question_is_not_listed(self) -> None:
        await self.ask(self.tavin, "leave")
        self.assertEqual((await self.read())["groups"][0]["kind"], "leave")
        # (the join door keeps to joins: a goodbye is never offered with the join buttons)
        self.assertEqual(
            (await join_questions.read(self.web, self.session, self.cref))["groups"], []
        )
        await self.sql("DELETE FROM memory_join_questions")
        await self.ask(self.tavin, "join")
        self.assertEqual((await self.read())["groups"], [])

    async def test_an_npc_is_not_listed(self) -> None:
        npc = await self.add("Old Bram", "character", role="npc")
        await self.ask(npc)
        self.assertEqual((await self.read())["groups"], [])

    async def test_an_unsent_answered_or_left_character_is_not_listed(self) -> None:
        await self.ask(self.tavin, sent=False)
        self.assertEqual((await self.read())["groups"], [])
        await self.sql("DELETE FROM memory_join_questions")
        await self.ask(self.tavin, status="stays")
        self.assertEqual((await self.read())["groups"], [])
        await self.sql("DELETE FROM memory_join_questions")
        await self.ask(self.tavin)
        await self.sql(
            "UPDATE memory_party_spans SET to_at = GREATEST(from_at, %s), ended = 'retired'"
            " WHERE entity_id = %s", int(time.time()), self.tavin,
        )  # fmt: skip
        self.assertEqual((await self.read())["groups"], [])

    async def test_only_a_dm_reads_or_answers_it(self) -> None:
        await self.ask(self.tavin)
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as read:
            await leave_questions.read(self.web, bob, self.cref)
        self.assertEqual(read.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as answered:
            await self.answer(self.tavin, "left", who=bob)
        self.assertEqual(answered.exception.code, "not_allowed")

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        for body in (
            {"character": "nope", "answer": "left"}, {"character": self.tavin, "answer": "later"},
            {"answer": "left"}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as err:
                await leave_questions.queue_answer(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_still_in_the_party_writes_only_the_answer_and_a_second_press_is_gone(
        self,
    ) -> None:
        await self.ask(self.tavin)
        before = await self.memory.version(self.g, self.one.id)
        await self.answer(self.tavin, "stays")
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, done.batches), ("", ()))
        self.assertEqual(await self.status_of(self.tavin), "stays")
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)
        await self.answer(self.tavin, "stays")
        (again,) = await self.bot_loop()
        self.assertEqual(again.error, wa.GONE)
        self.assertEqual((await self.read())["groups"], [])

    async def test_left_the_party_is_one_change_and_its_undo_asks_again(self) -> None:
        await self.ask(self.tavin)
        answer_id = await self.answer(self.tavin, "left")
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        self.assertEqual(await self.status_of(self.tavin), "left")
        self.assertEqual((await self.read())["groups"], [])
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual((read["state"], read["batch"]), ("done", done.batches[0]))
        (batch,) = done.batches
        # a made-up number never reaches `undo`; this DM's own change does, and the question waits
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch + 1000))
        (bad,) = await self.bot_loop()
        self.assertEqual(bad.error, wa.GONE)
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        self.assertEqual(await self.status_of(self.tavin), "waiting")
        self.assertEqual(len((await self.read())["groups"]), 1)

    async def test_a_second_left_press_is_gone(self) -> None:
        await self.ask(self.tavin)
        await self.answer(self.tavin, "left")
        await self.bot_loop()
        await self.answer(self.tavin, "left")
        (again,) = await self.bot_loop()
        self.assertEqual(again.error, wa.GONE)
