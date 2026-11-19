"""The website answers the DM's join question (#4778 part b, migration 0554): only a DM reads or
answers it, only a question already sent and still waiting shows (by names), "Only what everyone
knows" writes nothing, "What the party knows" is one change with an Undo the DM's own done answer
alone may take back, and a second answer finds `gone`. Postgres. Invented names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import join_questions, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web

GROUP = "a" * 32


class JoinOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.heir = await self.add("The Hidden Heir", "character", role="npc")
        self.secret_link = await self.link(
            "ally_of", self.mira, self.heir, "mira-heir", secret=True
        )
        self.tavin = await self.add("Tavin", "character", role="player_character")

    async def ask(self, entity: str, *, sent: bool = True) -> None:
        """A join question for this character (made sent, in `GROUP`, unless `sent` is off)."""
        now = int(time.time())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id,"
                " group_id, sent_at, created_at) VALUES (%s, %s, replace(gen_random_uuid()::text,"
                " '-', ''), %s, %s, %s, %s)",
                (self.g, self.one.id, entity, GROUP if sent else None, now if sent else None, now),
            )

    async def bot_loop(self) -> list[wa.Outcome]:
        """The bot's loop for the queue, as the real one: with the action store."""
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, action, actions=self.actions)
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def answer(self, how: str, group: str = GROUP, who: Any = None) -> str:
        return await join_questions.queue_answer(
            self.web, who or self.session, self.cref, {"group": group, "answer": how}
        )

    async def test_a_dm_reads_the_waiting_groups_by_name(self) -> None:
        await self.ask(self.tavin)
        got = await join_questions.read(self.web, self.session, self.cref)
        self.assertEqual(
            got, {"groups": [{"group": GROUP, "answer": "waiting", "names": ["Tavin"]}]}
        )

    async def test_a_question_not_sent_yet_or_of_a_character_who_left_is_not_listed(self) -> None:
        await self.ask(self.tavin, sent=False)
        self.assertEqual(
            (await join_questions.read(self.web, self.session, self.cref))["groups"], []
        )
        await self.sql("UPDATE memory_join_questions SET group_id = %s, sent_at = 1", GROUP)
        await self.sql(
            "UPDATE memory_party_spans SET to_at = GREATEST(from_at, 1), ended = 'left'"
            " WHERE entity_id = %s",
            self.tavin,
        )
        self.assertEqual(
            (await join_questions.read(self.web, self.session, self.cref))["groups"], []
        )

    async def test_only_a_dm_of_the_campaign_reads_or_answers(self) -> None:
        await self.ask(self.tavin)
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as read:
            await join_questions.read(self.web, bob, self.cref)
        self.assertEqual(read.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as answered:
            await self.answer("everyone", who=bob)
        self.assertEqual(answered.exception.code, "not_allowed")

    async def test_a_bad_group_or_answer_is_refused_before_anything_is_queued(self) -> None:
        for body in ({"group": "nope", "answer": "party"}, {"group": GROUP, "answer": "all"}, 5):
            with self.subTest(body=body):
                with self.assertRaises(RecapError) as err:
                    await join_questions.queue_answer(self.web, self.session, self.cref, body)
                self.assertEqual(err.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_only_what_everyone_knows_writes_nothing_and_a_second_answer_is_gone(
        self,
    ) -> None:
        await self.ask(self.tavin)
        before = await self.memory.version(self.g, self.one.id)
        await self.answer("everyone")
        (done,) = await self.bot_loop()
        self.assertEqual(done.error, "")
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)
        got = await join_questions.read(self.web, self.session, self.cref)
        self.assertEqual(got["groups"][0]["answer"], "everyone")  # (the party answer may follow)
        await self.answer("everyone")
        (again,) = await self.bot_loop()
        self.assertEqual(again.error, wa.GONE)

    async def test_what_the_party_knows_is_one_change_and_its_undo_is_the_dms_own(self) -> None:
        await self.ask(self.tavin)
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        answer_id = await self.answer("party")
        (done,) = await self.bot_loop()
        self.assertEqual(done.error, "")
        self.assertEqual(len(done.batches), 1)
        self.assertTrue(done.counts.startswith("given:"))
        # what the page reads: how it ended, how many things were given (Discord's "N things the
        # party knew") and the change number for its Undo
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual(read["state"], "done")
        self.assertEqual(read["given"], int(done.counts.removeprefix("given:")))
        self.assertGreater(read["given"], 0)
        self.assertEqual(read["batch"], done.batches[0])
        (batch,) = done.batches
        # a made-up change number, or one this DM did not make, never reaches `undo`
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch + 1000))
        (bad,) = await self.bot_loop()
        self.assertEqual(bad.error, wa.GONE)
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        got = await join_questions.read(self.web, self.session, self.cref)
        self.assertEqual(got["groups"][0]["answer"], "everyone")  # (Undo reads as it did)

    async def test_an_undo_with_no_digits_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as err:
            await join_questions.queue_undo(self.web, self.session, self.cref, "x1")
        self.assertEqual(err.exception.code, "not_found")

    async def test_the_party_answer_may_follow_everyone_and_a_second_party_press_is_gone(
        self,
    ) -> None:
        await self.ask(self.tavin)
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        await self.answer("everyone")
        await self.bot_loop()
        await self.answer("party")  # (as in Discord: "everyone" first, then "the party")
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        await self.answer("party")
        (again,) = await self.bot_loop()
        self.assertEqual(again.error, wa.GONE)

    async def test_a_retried_undo_is_done_and_another_dm_cannot_undo_this_dms_change(self) -> None:
        await self.ask(self.tavin)
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        await self.answer("party")
        (done,) = await self.bot_loop()
        (batch,) = done.batches
        # the same batch asked by a user who did not make it is `gone`: read in the store
        self.assertFalse(await self.actions.join_batch_made(self.g, self.one.id, 12345, batch))
        self.assertTrue(await self.actions.join_batch_made(self.g, self.one.id, ALICE.id, batch))
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch))
        await self.bot_loop()
        await join_questions.queue_undo(self.web, self.session, self.cref, str(batch))
        (retried,) = await self.bot_loop()
        self.assertEqual(retried.error, "")  # (taken back already: not an error)

    async def test_a_group_lists_only_the_characters_still_in_the_party(self) -> None:
        wren = await self.add("Wren", "character", role="player_character")
        await self.ask(self.tavin)
        await self.ask(wren)
        await self.sql(
            "UPDATE memory_party_spans SET to_at = GREATEST(from_at, 1), ended = 'left'"
            " WHERE entity_id = %s",
            wren,
        )
        got = await join_questions.read(self.web, self.session, self.cref)
        self.assertEqual([g["names"] for g in got["groups"]], [["Tavin"]])
