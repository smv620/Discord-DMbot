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


class TheReadyEmail(IsolatedAsyncioTestCase):
    """The recap-ready email goes to the same people as the waiting one: every DM (#2237)."""

    async def test_every_dm_of_the_campaign_gets_it_once_the_jobs_own_first(self) -> None:
        from dmbot.recaps.notify import tell_the_dm

        mailer: Any = SimpleNamespace(send=AsyncMock(return_value="sent"))
        store: Any = SimpleNamespace(
            address=AsyncMock(side_effect=lambda guild, user: f"dm{user}@example.org")
        )
        job: Any = SimpleNamespace(
            state="ready",
            title="The Gate",
            dm_user_id=9,
            session_at=1000,
            tries=0,
            last_error="",
            updated_at=50,
        )
        result = await tell_the_dm(mailer, store, SITE, 5, CID, job, {7, 8, 9})
        self.assertEqual(result, "sent")
        calls = [c.kwargs for c in mailer.send.await_args_list]
        self.assertEqual([c["user_id"] for c in calls], [9, 7, 8])  # the job's own DM first
        self.assertEqual([c["to"] for c in calls], [f"dm{u}@example.org" for u in (9, 7, 8)])
        self.assertEqual(len({c["kind"] for c in calls}), 1)  # one kind, so once for each person

    async def test_one_failed_address_lookup_does_not_stop_the_other_dms(self) -> None:
        from dmbot.recaps.notify import tell_the_dm

        mailer: Any = SimpleNamespace(send=AsyncMock(return_value="sent"))

        async def address(guild: int, user: int) -> str:
            if user == 7:
                raise RuntimeError("down")
            return f"dm{user}@example.org"

        store: Any = SimpleNamespace(address=address)
        job: Any = SimpleNamespace(
            state="failed",
            title="",
            dm_user_id=9,
            session_at=1000,
            tries=1,
            last_error="x",
            updated_at=50,
        )
        await tell_the_dm(mailer, store, SITE, 5, CID, job, {7, 8, 9})
        calls = {c.kwargs["user_id"]: c.kwargs["to"] for c in mailer.send.await_args_list}
        self.assertEqual(calls, {9: "dm9@example.org", 7: None, 8: "dm8@example.org"})

    async def test_with_no_other_dms_only_the_jobs_dm_is_emailed(self) -> None:
        from dmbot.recaps.notify import tell_the_dm

        mailer: Any = SimpleNamespace(send=AsyncMock(return_value="sent"))
        store: Any = SimpleNamespace(address=AsyncMock(return_value="dm@example.org"))
        job: Any = SimpleNamespace(
            state="failed",
            title="",
            dm_user_id=9,
            session_at=1000,
            tries=1,
            last_error="x",
            updated_at=50,
        )
        await tell_the_dm(mailer, store, SITE, 5, CID, job)
        self.assertEqual([c.kwargs["user_id"] for c in mailer.send.await_args_list], [9])


class TheFailureEmailKey(IsolatedAsyncioTestCase):
    """A failure of ours gives the DM's Try again back (#2341), so the email's key must not be
    built from the tries: fail, Try again, fail again must send two emails."""

    async def test_a_second_failure_after_a_given_back_try_is_still_emailed(self) -> None:
        from dmbot.recaps.notify import tell_the_dm

        mailer: Any = SimpleNamespace(send=AsyncMock(return_value="sent"))
        store: Any = SimpleNamespace(address=AsyncMock(return_value="dm@example.org"))

        def job(tries: int, updated_at: int) -> Any:
            return SimpleNamespace(
                state="failed", session_at=1000, dm_user_id=7, tries=tries,
                last_error="The recap came out too long for DMbot to finish.",
                updated_at=updated_at,
            )  # fmt: skip

        await tell_the_dm(mailer, store, SITE, 5, CID, job(0, 100))  # the first run fails
        await tell_the_dm(mailer, store, SITE, 5, CID, job(0, 200))  # Try again, ours, tries back
        kinds = [c.kwargs["kind"] for c in mailer.send.await_args_list]
        self.assertEqual(len(kinds), 2)
        self.assertNotEqual(kinds[0], kinds[1])
