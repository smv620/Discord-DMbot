"""The 🤫 Known or secret? questions on the website (#4598, migration 0527): a database door for a
DM of the campaign and nobody else, and three queue actions (keep, make, Undo) that the bot applies
through the same store calls as Discord's card, a make being one change with one Undo. A 🌍 World
read's question is not on the website. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import secret_questions as sq
from dmbot.memory import web_actions as wa
from dmbot.memory.models import CONFIRMED, DM
from dmbot.web import secret_question_words as words
from dmbot.web import secret_questions
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

PLAYER = 9


class Questions(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # A connection the players already know, which a DM's own "secret" would have narrowed:
        # DMbot asks instead (#4524), with the upload it came from.
        self.hero = (
            await self.memory.add_entity(
                self.g, self.one.id, name="Ilsa", type="character", role="player_character",
                played_by=PLAYER, source=DM, status=CONFIRMED,
            )
        ).value.id  # fmt: skip
        self.bob = (
            await self.memory.add_entity(
                self.g, self.one.id, name="Bob Tamsin", type="npc", source=DM, status=CONFIRMED
            )
        ).value.id  # fmt: skip
        tie = await self.memory.add_relation(
            self.g, self.one.id, self.bob, "ally_of", self.hero, source=DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        self.tie = tie.value[0]
        with sq.evidence("Tavin's tale, 3 Nov"):
            await self.memory.update_relation(
                self.g, self.one.id, self.tie.id, secret=True, source=DM
            )
        (self.question,) = await self.memory.secret_questions_waiting(self.g, self.one.id)

    async def queue_one(self, action: str, text: str, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.queue_one(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome

    async def listed(self) -> list[dict[str, Any]]:
        got = await secret_questions.read(self.web, self.session, self.cref)
        questions: list[dict[str, Any]] = got["questions"]
        return questions

    async def secret(self) -> bool:
        tie = await self.memory.relation(self.g, self.one.id, self.tie.id)
        assert tie is not None
        return tie.secret


class TheDoor(Questions):
    async def test_a_narrow_question_names_the_connection_and_says_where_it_came_from(self) -> None:
        (one,) = await self.listed()
        self.assertEqual(one["id"], self.question.id)
        self.assertEqual(one["kind"], "narrow")
        self.assertIn("Bob Tamsin", one["heading"])
        self.assertIn("Ilsa", one["heading"])
        self.assertIn("sounds like a secret, but your players already know it.", one["heading"])
        self.assertEqual(one["source"], 'From "Tavin\'s tale, 3 Nov".')
        self.assertEqual(one["keep"]["label"], "Keep it known")
        self.assertEqual(one["make"]["label"], "Make it secret")

    async def test_a_reveal_question_names_the_two_names(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.bob, "Old Bob", kind="old", secret=True, status=CONFIRMED,
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        async with self.memory._write(self.g, self.one.id, DM) as w:
            await sq.ask_reveal(w, made.value.id)
        reveal = next(q for q in await self.listed() if q["kind"] == "reveal")
        self.assertEqual(
            reveal["heading"],
            "Your players know the name Old Bob. Only you know it is Bob Tamsin.",
        )
        self.assertEqual(
            (reveal["keep"]["label"], reveal["make"]["label"]), ("Keep it secret", "Let them know")
        )

    async def test_a_worlds_question_is_not_on_the_website(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.bob, "Old Bob", kind="old", secret=True, status=CONFIRMED,
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        async with self.memory._write(self.g, self.one.id, DM) as w:
            await sq.ask(
                w, sq.REVEAL, "memory_aliases", made.value.id, {**sq.COMMON, "secret": False}
            )
        self.assertEqual(len(await self.memory.secret_questions_waiting(self.g, self.one.id)), 2)
        self.assertEqual([q["kind"] for q in await self.listed()], ["narrow"])

    async def test_an_item_gone_since_is_said_so_and_a_player_gets_nothing(self) -> None:
        await self.sql(
            "DELETE FROM memory_relations WHERE guild_id = %s AND id = %s", self.g, self.tie.id
        )
        (gone,) = await self.listed()
        self.assertEqual(gone["heading"], words.GONE_LINE)
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as refused:
            await secret_questions.read(self.web, bob, self.cref)
        self.assertEqual(refused.exception.code, "not_allowed")


class TheAnswers(Questions):
    async def test_keep_it_known_changes_nothing_and_the_question_goes(self) -> None:
        done = await self.act("secret-keep", self.question.id)
        self.assertFalse(done.error)
        self.assertEqual(done.batches, ())
        self.assertFalse(await self.secret())
        self.assertEqual(await self.listed(), [])

    async def test_make_it_secret_is_one_change_and_undo_puts_the_question_back(self) -> None:
        done = await self.act("secret-make", self.question.id)
        self.assertFalse(done.error)
        (batch,) = done.batches
        self.assertTrue(await self.secret())
        self.assertEqual(await self.listed(), [])
        undone = await self.act("secret-undo", str(batch))
        self.assertFalse(undone.error)
        self.assertFalse(await self.secret())
        self.assertEqual(len(await self.listed()), 1)

    async def test_a_reveal_keeps_the_secret_with_keep_and_lets_them_know_with_make(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.bob, "Old Bob", kind="old", secret=True, status=CONFIRMED,
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        async with self.memory._write(self.g, self.one.id, DM) as w:
            asked = await sq.ask_reveal(w, made.value.id)
        done = await self.act("secret-make", asked)
        self.assertFalse(done.error)
        self.assertEqual(len(done.batches), 1)
        aliases = await self.memory.aliases(self.g, self.one.id, include_secret=True)
        self.assertFalse(next(a for a in aliases if a.id == made.value.id).secret)

    async def test_a_question_answered_meanwhile_is_gone(self) -> None:
        await self.memory.answer_secret_question(
            self.g, self.one.id, self.question.id, False, source=DM
        )
        done = await self.act("secret-make", self.question.id)
        self.assertEqual(done.error, wa.GONE)
        self.assertFalse(await self.secret())

    async def test_a_worlds_question_is_refused_as_a_bad_target(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.bob, "Old Bob", kind="old", secret=True, status=CONFIRMED,
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        async with self.memory._write(self.g, self.one.id, DM) as w:
            await sq.ask(
                w, sq.REVEAL, "memory_aliases", made.value.id, {**sq.COMMON, "secret": False}
            )
        world = next(
            q for q in await self.memory.secret_questions_waiting(self.g, self.one.id)
            if q.change.get("common")
        )  # fmt: skip
        done = await self.act("secret-make", world.id)
        self.assertEqual(done.error, wa.BAD_TARGET)

    async def test_an_undo_of_a_change_that_is_not_the_dms_own_answer_is_gone(self) -> None:
        other = await self.memory.add_alias(
            self.g, self.one.id, self.bob, "Tamsin", kind="nickname", source=DM, status=CONFIRMED
        )
        self.assertIsNotNone(other.batch)
        done = await self.act("secret-undo", str(other.batch))
        self.assertEqual(done.error, wa.GONE)

    async def test_another_dm_cannot_undo_this_dms_answer(self) -> None:
        made = await self.act("secret-make", self.question.id)
        (batch,) = made.batches
        self.assertTrue(await self.actions.secret_batch_made(self.g, self.one.id, ALICE.id, batch))
        self.assertFalse(await self.actions.secret_batch_made(self.g, self.one.id, 999, batch))

    async def test_another_campaigns_questions_are_not_listed_and_its_batch_is_not_ours(
        self,
    ) -> None:
        other_ref = f"{self.g}.{self.two.id}"
        with self.assertRaises(RecapError) as refused:  # (a DM of one campaign is no DM of another)
            await secret_questions.read(self.web, self.session, other_ref)
        self.assertEqual(refused.exception.code, "not_allowed")
        made = await self.act("secret-make", self.question.id)
        (batch,) = made.batches
        self.assertFalse(await self.actions.secret_batch_made(self.g, self.two.id, ALICE.id, batch))

    async def test_the_door_refuses_a_bad_shape_and_a_player(self) -> None:
        def refused(got: Any) -> bool:
            return got is None or got["status"] != "ok"

        for text in ("", "not an id", self.question.id + "0"):
            self.assertTrue(refused(await self.queue_one("secret-keep", text)), text)
        self.assertTrue(refused(await self.queue_one("secret-undo", "12x")))
        bob = await self.signed(BOB, THURSDAY)
        self.assertTrue(refused(await self.queue_one("secret-keep", self.question.id, who=bob)))

    async def test_the_page_queues_only_a_known_action(self) -> None:
        for body in ({}, {"action": "nope"}, {"action": "keep"}, {"action": "undo", "batch": "x"}):
            with self.assertRaises(RecapError) as bad:
                await secret_questions.change(self.web, self.session, self.cref, body)
            self.assertEqual(bad.exception.code, "bad_action")
        queued = await secret_questions.change(
            self.web, self.session, self.cref, {"action": "make", "question": self.question.id}
        )
        self.assertEqual(len(queued), 32)
