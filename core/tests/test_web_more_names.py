"""The website's 📝 More names press (#5081, migration 0615): the queue door takes `names-more`
with no text and only from a DM, the bot's side calls the same `add_more_names` Discord's button
does and answers with counts, and the poll says how it ended. Postgres for the door and the poll.
Invented names."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.web import name_checks, relationships
from dmbot.web.recaps import RecapError
from tests.test_web_api import ALICE
from tests.test_web_relationships import Web


class TheQueueAndThePoll(Web):
    async def test_a_dm_queues_it_with_no_text_and_nothing_else(self) -> None:
        answer = await name_checks.queue_more_names(self.web, self.session, self.cref)
        self.assertEqual(len(answer), 32)
        with self.assertRaises(RecapError):  # (the door takes no words with it)
            await relationships._queue(
                self.web, self.session, self.cref, entity=None, action="names-more", target="",
                text="Mira", flag=False,
            )  # fmt: skip

    async def test_a_player_and_a_stranger_cannot_queue_it(self) -> None:
        with self.assertRaises(RecapError):
            await name_checks.queue_more_names(self.web, self.player, self.cref)

    async def test_the_poll_says_how_many_were_made_and_how_many_wait(self) -> None:
        answer = await name_checks.queue_more_names(self.web, self.session, self.cref)
        first = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((first["state"], first["made"], first["left"]), ("waiting", None, None))
        actions = wa.ActionStore(self.db)
        taken = await actions.claim_next(self.g)
        assert taken is not None
        self.assertEqual(taken.action, "names-more")
        await actions.progress(self.g, taken, 20, 120)
        mid = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual(mid["state"], "working")
        self.assertEqual(mid["progress"], {"done": 20, "total": 120})
        await actions.finish(self.g, taken, result_counts="made:12 left:40")
        done = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((done["state"], done["made"], done["left"]), ("done", 12, 40))
        self.assertIn("stage", done["waits"])

    async def test_a_failed_press_says_so_and_carries_no_counts(self) -> None:
        answer = await name_checks.queue_more_names(self.web, self.session, self.cref)
        actions = wa.ActionStore(self.db)
        taken = await actions.claim_next(self.g)
        assert taken is not None
        await actions.finish(self.g, taken, error=wa.FAILED)
        got = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((got["state"], got["error"], got["made"]), ("failed", wa.FAILED, None))


def action(user: int = ALICE.id) -> Any:
    return SimpleNamespace(
        action="names-more", text="", user_id=user, campaign_id="c1", target_id=""
    )


def run(a: Any, **kw: Any) -> wa.Outcome:
    return asyncio.run(wa.apply(None, 5, {ALICE.id}, a, **kw))  # type: ignore[arg-type]


class TheApplier(unittest.TestCase):
    def test_it_calls_the_bots_add_more_names_and_answers_with_counts(self) -> None:
        asked: list[tuple[int, str]] = []

        async def more(
            guild: int, cid: str, progress: Callable[[int, int], Awaitable[None]] | None
        ) -> tuple[int, int]:
            asked.append((guild, cid))
            assert progress is not None
            await progress(10, 120)
            return 12, 40

        moved: list[tuple[int, int]] = []

        class Store:
            async def progress(self, guild: int, a: Any, done: int, total: int) -> bool:
                moved.append((done, total))
                return True

        done = run(action(), more_names=more, actions=Store())
        self.assertEqual((done.error, done.counts), ("", "made:12 left:40"))
        self.assertEqual(asked, [(5, "c1")])
        self.assertEqual(moved, [(10, 120)])

    def test_nobody_but_a_dm_and_no_bot_and_a_failure_all_change_nothing(self) -> None:
        async def boom(*_: Any) -> tuple[int, int]:
            raise RuntimeError("Mira Stormgrace")

        self.assertEqual(run(action(user=99)).error, wa.NOT_ALLOWED)
        self.assertEqual(run(action()).error, wa.FAILED)  # (no bot to do it)
        failed = run(action(), more_names=boom)
        self.assertEqual((failed.error, failed.counts), (wa.FAILED, ""))

    def test_a_progress_that_cannot_be_written_never_fails_the_press(self) -> None:
        async def more(guild: int, cid: str, progress: Any) -> tuple[int, int]:
            await progress(1, 2)
            return 1, 0

        class Broken:
            async def progress(self, *_: Any) -> bool:
                raise RuntimeError("down")

        self.assertEqual(run(action(), more_names=more, actions=Broken()).counts, "made:1 left:0")
