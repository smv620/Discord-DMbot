"""The website's "Did they mean…?" (#4841 B1, migration 0563): only a DM reads or answers it; the
door lists the open questions with the heard word, who said it and the names that are safe to offer
(never a hidden, secret, unnamed, limited, unconfirmed or merged one), leaves out a question that
expired or whose line is gone, and the answer goes to the bot's own function with its code coming
back and no word. Postgres. Invented names."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.waiting_questions import Unanswered, WaitingQuestionStore
from dmbot.web import heard_waiting, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_all_names import Listing
from tests.test_web_api import ALICE, THURSDAY

PLAYER = 4242  # a speaker (a Discord-shaped number made up for the test)
MS = 1_700_000_000_000


class HeardOnTheWeb(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.waiting = WaitingQuestionStore(self.db)
        self.tsession = await self.transcripts.open_session(self.g, self.one.id, int(time.time()))
        await self.transcripts.add_lines(
            self.g,
            self.tsession,
            [Line(MS + 1000, PLAYER, "I met Marin there", "I met Marin there")],
        )
        self.maren = await self.add("Maren", "character", role="npc")
        self.marron = await self.add("Marron", "character", role="npc")
        self.asked: list[tuple[int, int, str, int]] = []
        self.answers = [SimpleNamespace(code="fixed", done=True, reason="")]

    async def ask(self, options: list[str], at: int = 1000, heard: str = "Marin") -> None:
        await self.waiting.save(
            self.g, self.one.id, self.tsession,
            [Unanswered(PLAYER, MS + at, heard, tuple(options))],
        )  # fmt: skip

    async def fake_heard(self, guild_id: int, question: int, pick: str, user_id: int) -> Any:
        self.asked.append((guild_id, question, pick, user_id))
        return self.answers.pop(0)

    async def read(self, who: Any = None) -> dict[str, Any]:
        return await heard_waiting.read(self.web, who or self.session, self.cref)

    async def asking(self) -> list[wa.Outcome]:
        store = wa.ActionStore(self.db)
        done = []
        while (action := await store.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                MemoryStore(self.db), self.g, {ALICE.id}, action, actions=store,
                heard=self.fake_heard,
            )  # fmt: skip
            await store.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def test_a_dm_reads_the_question_with_its_names_in_order_and_with_their_index(
        self,
    ) -> None:
        await self.ask([self.maren, self.marron])
        got = await self.read()
        self.assertEqual(got["total"], 1)
        (question,) = got["questions"]
        self.assertEqual(question["heard"], "Marin")
        self.assertEqual(
            question["options"],
            [
                {"index": 0, "id": self.maren, "name": "Maren"},
                {"index": 1, "id": self.marron, "name": "Marron"},
            ],
        )

    async def test_the_speaker_is_their_character_or_nobody(self) -> None:
        await self.ask([self.maren, self.marron])
        self.assertIsNone((await self.read())["questions"][0]["speaker"])
        await self.add("Tavin", "character", role="player_character", played_by=PLAYER)
        self.assertEqual((await self.read())["questions"][0]["speaker"], "Tavin")

    async def test_a_hidden_secret_unnamed_limited_or_merged_name_is_never_a_candidate(
        self,
    ) -> None:
        hidden = await self.add("Hidden One", "character", role="npc", hidden_from_players=True)
        secret = await self.add("Veiled One", "character", role="npc")
        await self.alias(secret, "The Hooded Stranger", secret=True)
        gone = await self.add("Merged One", "character", role="npc")
        limited = await self.add("Some One", "character", role="npc")
        await self.sql("UPDATE memory_entities SET audience_limited = true WHERE id = %s", limited)
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.maren,
            gone,
        )
        await self.ask([self.maren, hidden, secret])
        options = (await self.read())["questions"][0]["options"]
        self.assertEqual([o["name"] for o in options], ["Maren"])  # (index 0 keeps its place)
        await self.sql("DELETE FROM waiting_name_questions")
        await self.ask([limited, gone, self.marron])
        options = (await self.read())["questions"][0]["options"]
        self.assertEqual([(o["index"], o["name"]) for o in options], [(2, "Marron")])
        stranger = await self.add("A Stranger", "character", role="npc")
        await self.sql(
            "UPDATE memory_entities SET unnamed = true, unnamed_what = 'a stranger' WHERE id = %s",
            stranger,
        )
        await self.sql("DELETE FROM waiting_name_questions")
        await self.ask([stranger, self.marron])
        options = (await self.read())["questions"][0]["options"]
        self.assertEqual([(o["index"], o["name"]) for o in options], [(1, "Marron")])

    async def test_a_name_with_no_confirmed_name_entry_is_not_offered(self) -> None:
        await self.sql(
            "UPDATE memory_aliases SET status = 'proposed' WHERE entity_id = %s AND text = %s",
            self.marron,
            "Marron",
        )
        await self.ask([self.maren, self.marron])
        options = (await self.read())["questions"][0]["options"]
        self.assertEqual([o["name"] for o in options], ["Maren"])

    async def test_an_expired_question_and_one_whose_line_is_gone_are_not_listed(self) -> None:
        await self.ask([self.maren, self.marron])
        await self.ask([self.maren, self.marron], at=900_000)  # (a line that is not there)
        self.assertEqual((await self.read())["total"], 1)
        await self.sql("UPDATE waiting_name_questions SET created_at = created_at - 700000")
        self.assertEqual((await self.read())["total"], 0)

    async def test_only_a_dm_of_the_campaign_reads_or_answers(self) -> None:
        await self.ask([self.maren, self.marron])
        who: Any = BOB
        token = await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, token, now=NOW)
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await self.read(bob)
        self.assertEqual(err.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as err2:
            await heard_waiting.queue_keep(self.web, bob, self.cref, {"question": "1"})
        self.assertEqual(err2.exception.code, "not_allowed")

    async def test_a_pick_and_a_keep_go_to_the_bots_own_function_and_come_back_as_a_code(
        self,
    ) -> None:
        await heard_waiting.queue_pick(
            self.web, self.session, self.cref, {"question": "7", "index": 1}
        )
        self.answers = [SimpleNamespace(code="line-kept", done=True, reason="")]
        (picked,) = await self.asking()
        self.assertEqual((picked.error, picked.counts), ("", "heard:line-kept"))
        await heard_waiting.queue_keep(self.web, self.session, self.cref, {"question": "7"})
        self.answers = [SimpleNamespace(code="kept", done=True, reason="")]
        (kept,) = await self.asking()
        self.assertEqual(kept.counts, "heard:kept")
        self.assertEqual(self.asked, [(self.g, 7, "1", ALICE.id), (self.g, 7, "keep", ALICE.id)])

    async def test_a_refused_answer_is_its_code_as_the_error_and_the_answer_read_says_so(
        self,
    ) -> None:
        answer = await heard_waiting.queue_pick(
            self.web, self.session, self.cref, {"question": "7", "index": 0}
        )
        self.answers = [SimpleNamespace(code="name-gone", done=True, reason="")]
        (done,) = await self.asking()
        self.assertEqual(done.error, "name_gone")
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((read["state"], read["error"]), ("failed", "name_gone"))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [{"question": "x", "index": 0}, {"question": "1", "index": 3}, 5, {}]
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await heard_waiting.queue_pick(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertEqual(await self.asking(), [])
