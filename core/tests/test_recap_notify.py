"""The daily email for a held recap (#2174): a count and a link, once a day, nothing else."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from dmbot.recaps.notify import remind_the_dm

SITE = "https://dmbot.example.org"
CID = "c" * 32


def call(mailer: Any, store: Any, *, site: str = SITE, waiting: int = 2, day: int = 1) -> Any:
    return remind_the_dm(mailer, store, site, 5, CID, 7, 1000, waiting, day)


class TheHeldRecapEmail(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mailer = SimpleNamespace(send=AsyncMock(return_value="sent"))
        self.store = SimpleNamespace(address=AsyncMock(return_value="dm@example.org"))

    async def test_it_holds_a_count_and_a_link_only(self) -> None:
        self.assertEqual(await call(self.mailer, self.store), "sent")
        sent = self.mailer.send.await_args.kwargs
        self.assertEqual((sent["user_id"], sent["to"]), (7, "dm@example.org"))
        self.assertIn("2 name questions", sent["body"])
        self.assertIn(f"{SITE}/account/recaps/5.{CID}.1000", sent["body"])
        self.assertNotIn(CID, sent["kind"])  # no campaign id in the mail log

    async def test_each_day_has_its_own_kind_and_a_day_repeats_the_same_one(self) -> None:
        await call(self.mailer, self.store, day=1)
        await call(self.mailer, self.store, day=1)
        await call(self.mailer, self.store, day=2)
        kinds = [c.kwargs["kind"] for c in self.mailer.send.await_args_list]
        self.assertEqual(kinds[0], kinds[1])  # the mailer's once-per-kind record stops the repeat
        self.assertNotEqual(kinds[0], kinds[2])

    async def test_one_question_reads_in_the_singular(self) -> None:
        await call(self.mailer, self.store, waiting=1)
        self.assertIn("1 name question from", self.mailer.send.await_args.kwargs["body"])

    async def test_nothing_without_a_site_or_questions(self) -> None:
        self.assertEqual(await call(self.mailer, self.store, site=""), "not_ready")
        self.assertEqual(await call(self.mailer, self.store, waiting=0), "not_ready")
        self.mailer.send.assert_not_awaited()


class TheDailyKind(IsolatedAsyncioTestCase):
    """Two held sessions of one campaign must not share a kind, or the second's email is dropped."""

    async def kinds(self, *calls: tuple[int, str]) -> list[str]:
        mailer: Any = SimpleNamespace(send=AsyncMock(return_value="sent"))
        store: Any = SimpleNamespace(address=AsyncMock(return_value="dm@example.org"))
        for session_at, group in calls:
            await remind_the_dm(mailer, store, SITE, 5, CID, 7, session_at, 2, 1, group)
        return [c.kwargs["kind"] for c in mailer.send.await_args_list]

    async def test_two_lone_holds_and_an_import_each_have_their_own(self) -> None:
        one, two, imp_a, imp_b = await self.kinds(
            (1000, ""), (2000, ""), (3000, "a" * 32), (4000, "a" * 32)
        )
        self.assertEqual(len({one, two, imp_a}), 3)
        self.assertEqual(imp_a, imp_b)  # an import's sessions share one email a day
