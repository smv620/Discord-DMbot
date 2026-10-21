"""What a DM types on the recap page (#2113): queued by the website's doors, sorted by the bot's
loop, polled, and "Rewrite with these". Invented fixtures only."""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot import schema
from dmbot.recaps import requests as rq
from dmbot.recaps.feedback import Sorted
from dmbot.recaps.store import MAX_CORRECTIONS, MAX_TRIES
from dmbot.recaps.style import MAX_NOTES
from dmbot.web import recap_requests as web
from dmbot.web import sessions
from dmbot.web.recaps import RecapError, read_recap
from tests import recap_fixtures as fx
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import API, SITE, THURSDAY, FakeDiscord, settings

POINTS = Sorted(
    style=("Shorter, please.",),
    corrections=("Mira did not draw her blade.",),
    replies=("DMbot doesn't invent story.",),
)


class WithRequests(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.requests = rq.RequestStore(self.db)

    async def send(self, text: str = "Mira did not draw her blade.") -> str:
        got = await web.send_instructions(self.web, self.session, self.ref, text)
        return str(got["requestId"])

    async def answered(self, points: Sorted = POINTS) -> str:
        rid = await self.send()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None and request.id == rid
        await self.requests.finish(THURSDAY.id, request, answer=rq.answer_of(points))
        return rid

    async def code(self, call: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await call
        return err.exception.code


class TheQueue(WithRequests):
    async def test_the_words_are_queued_and_nothing_else_changes(self) -> None:
        before = await self.state(self.one.id)
        rid = await self.send()
        self.assertRegex(rid, r"^[0-9a-f]{32}$")
        self.assertEqual(
            await web.read_instructions(self.web, self.session, self.ref, rid), {"state": "waiting"}
        )
        after = await self.state(self.one.id)
        self.assertEqual((after.tries, after.corrections), (before.tries, before.corrections))

    async def test_the_limits_and_the_refusals(self) -> None:
        for bad in ("", "   ", None, 7):
            self.assertEqual(
                await self.code(web.send_instructions(self.web, self.session, self.ref, bad)),
                "bad_text",
            )
        self.assertEqual(
            await self.code(web.send_instructions(self.web, self.session, self.ref, "x" * 1001)),
            "too_big",
        )
        for _ in range(3):
            await self.send()
        self.assertEqual(await self.code(self.send()), "too_soon")  # three waiting is the limit

    async def test_a_recap_that_is_posted_or_skipped_takes_no_notes(self) -> None:
        await self.store.decide(THURSDAY.id, self.one.id, 100, "drop")
        self.assertEqual(await self.code(self.send()), "not_editable")

    async def test_a_person_who_is_not_a_dm_of_the_campaign_is_told_nothing(self) -> None:
        bob_token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, bob_token, now=NOW)
        assert bob is not None
        self.assertEqual(
            await self.code(web.send_instructions(self.web, bob, self.ref, "hello")), "not_found"
        )
        rid = await self.send()
        self.assertEqual(
            await self.code(web.read_instructions(self.web, bob, self.ref, rid)), "not_found"
        )

    async def test_an_odd_request_id_is_not_found(self) -> None:
        for odd in ("", "x", "F" * 32, None, "a" * 33):
            self.assertEqual(
                await self.code(web.read_instructions(self.web, self.session, self.ref, odd)),
                "not_found",
            )


class TheBot(WithRequests):
    async def work(self, gate: Any = None, sort: Any = None) -> str:
        await self.send()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None
        return await rq.run_one(
            self.requests,
            THURSDAY.id,
            request,
            gate=gate or AsyncMock(return_value=None),
            sort=sort or AsyncMock(return_value=POINTS),
        )

    async def test_the_sorted_points_come_back_with_ids_and_the_plain_reply(self) -> None:
        self.assertEqual(await self.work(), "done")
        rid = await self.latest()
        got = await web.read_instructions(self.web, self.session, self.ref, rid)
        self.assertEqual(got["state"], "done")
        self.assertEqual(
            got["points"],
            {
                "corrections": [{"id": "c1", "text": "Mira did not draw her blade."}],
                "style": [{"id": "s1", "text": "Shorter, please."}],
                "replies": ["DMbot doesn't invent story."],
            },
        )

    async def latest(self) -> str:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT id FROM recap_requests ORDER BY created_at DESC")
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def test_a_gate_that_says_no_asks_the_ai_nothing(self) -> None:
        sort = AsyncMock()
        self.assertEqual(
            await self.work(gate=AsyncMock(return_value=rq.TOO_SOON), sort=sort), "too_soon"
        )
        sort.assert_not_awaited()
        got = await web.read_instructions(self.web, self.session, self.ref, await self.latest())
        self.assertEqual(got, {"state": "failed", "error": "too_soon"})

    async def test_a_sort_that_fails_or_raises_is_failed_not_stuck(self) -> None:
        self.assertEqual(await self.work(sort=AsyncMock(return_value=None)), "sort_failed")
        self.assertEqual(
            await self.work(sort=AsyncMock(side_effect=RuntimeError("db secret words"))),
            "sort_failed",
        )
        got = await web.read_instructions(self.web, self.session, self.ref, await self.latest())
        self.assertEqual(got["state"], "failed")

    async def test_the_words_are_never_logged(self) -> None:
        with (
            self.assertNoLogs(level=logging.DEBUG)
            if False
            else self.assertLogs("dmbot.recaps.requests", level="WARNING") as logs
        ):
            await self.work(
                sort=AsyncMock(side_effect=RuntimeError("Mira did not draw her blade."))
            )
        self.assertNotIn("Mira", " ".join(logs.output))

    async def test_a_stalled_claim_is_taken_again_and_old_rows_are_swept(self) -> None:
        await self.send()
        first = await self.requests.claim_next(THURSDAY.id)
        assert first is not None
        self.assertIsNone(await self.requests.claim_next(THURSDAY.id))  # held by the first
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_requests SET claimed_at = 1")
        again = await self.requests.claim_next(THURSDAY.id)
        assert again is not None and again.id == first.id
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_requests SET created_at = 1")
        self.assertEqual(await self.requests.sweep(THURSDAY.id), 1)


class TheCaps(WithRequests):
    def test_the_doors_caps_are_the_codes_own(self) -> None:
        sql = schema.RECAP_REQUESTS
        self.assertIn(f"max_tries CONSTANT INT := {MAX_TRIES};", sql)
        self.assertIn(f"max_corrections CONSTANT INT := {MAX_CORRECTIONS};", sql)
        self.assertIn(f"max_notes CONSTANT INT := {MAX_NOTES};", sql)

    async def test_a_second_dm_of_the_campaign_cannot_read_or_use_the_first_dms_request(
        self,
    ) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
                (THURSDAY.id, self.one.id, BOB.id),
            )
        rid = await self.answered()
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, token, now=NOW)
        assert bob is not None
        self.assertEqual(
            await self.code(web.read_instructions(self.web, bob, self.ref, rid)), "not_found"
        )
        body = {"requestId": rid, "corrections": ["c1"]}
        self.assertEqual(await self.code(web.rewrite(self.web, bob, self.ref, body)), "not_found")

    async def test_a_request_for_one_recap_is_refused_on_another(self) -> None:
        await self.store.enqueue(THURSDAY.id, self.one.id, 200, dm_user_id=fx.ALICE)
        await self.store.update(
            THURSDAY.id, self.one.id, 200, state="ready", title="T", summary=fx.SUMMARY
        )
        rid = await self.answered()
        other = f"{THURSDAY.id}.{self.one.id}.200"
        self.assertEqual(
            await self.code(web.read_instructions(self.web, self.session, other, rid)), "not_found"
        )

    async def test_two_rewrites_of_one_request_give_one_ok(self) -> None:
        import asyncio

        rid = await self.answered()
        body = {"requestId": rid, "corrections": ["c1"]}
        results = await asyncio.gather(
            web.rewrite(self.web, self.session, self.ref, body),
            web.rewrite(self.web, self.session, self.ref, body),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual((await self.state(self.one.id)).tries, 1)


class TheRewrite(WithRequests):
    async def test_the_kept_points_queue_a_try_clear_the_approval_and_save_the_notes(self) -> None:
        await self.store.approve(
            THURSDAY.id, self.one.id, 100, fx.ALICE, "text", expected=await self.hash()
        )
        rid = await self.answered()
        got = await web.rewrite(
            self.web,
            self.session,
            self.ref,
            {"requestId": rid, "corrections": ["c1"], "style": ["s1"]},
        )
        self.assertTrue(got["needsApproval"])
        self.assertEqual(got["triesLeft"], MAX_TRIES - 1)
        job = await self.state(self.one.id)
        self.assertEqual(
            (job.state, job.tries, job.approved_by, job.audio_choice), ("queued", 1, 0, "")
        )
        self.assertEqual(job.corrections, ("Mira did not draw her blade.",))
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT text FROM recap_style_notes")
            self.assertEqual([r["text"] for r in await cur.fetchall()], ["Shorter, please."])
            cur = await conn.execute("SELECT count(*) AS n FROM recap_requests")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # the request is used up

    async def hash(self) -> str:
        from dmbot.recaps.approval import text_hash

        job = await self.state(self.one.id)
        return text_hash(job.title, job.summary)

    async def test_only_what_the_dm_kept_and_only_ids_that_were_offered(self) -> None:
        rid = await self.answered()
        for body in (
            {"requestId": rid, "corrections": ["c9"]},
            {"requestId": rid, "style": ["c1"]},
            {"requestId": rid},
            {"requestId": rid, "corrections": []},
            {"requestId": rid, "corrections": "c1"},
        ):
            self.assertEqual(
                await self.code(web.rewrite(self.web, self.session, self.ref, body)), "bad_choice"
            )
        self.assertEqual((await self.state(self.one.id)).tries, 0)

    async def test_a_request_that_is_not_done_or_not_the_dms_own_is_not_found(self) -> None:
        rid = await self.send()  # still waiting
        body = {"requestId": rid, "corrections": ["c1"]}
        self.assertEqual(
            await self.code(web.rewrite(self.web, self.session, self.ref, body)), "not_found"
        )

    async def test_no_tries_left_and_not_editable_change_nothing(self) -> None:
        rid = await self.answered()
        await self.store.update(THURSDAY.id, self.one.id, 100, state="ready")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = %s", (MAX_TRIES,))
        body = {"requestId": rid, "corrections": ["c1"]}
        self.assertEqual(
            await self.code(web.rewrite(self.web, self.session, self.ref, body)), "no_tries"
        )
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 0, status = 'posted'")
        self.assertEqual(
            await self.code(web.rewrite(self.web, self.session, self.ref, body)), "not_editable"
        )

    async def test_the_note_limit_drops_the_oldest(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for n in range(10):
                await conn.execute(
                    "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text,"
                    " created_by, created_at) VALUES (%s, %s, %s, %s, %s, 0)",
                    (THURSDAY.id, self.one.id, n + 1, f"old {n}", fx.ALICE),
                )
        rid = await self.answered()
        await web.rewrite(self.web, self.session, self.ref, {"requestId": rid, "style": ["s1"]})
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT text FROM recap_style_notes ORDER BY position")
            texts = [r["text"] for r in await cur.fetchall()]
        self.assertEqual(len(texts), 10)
        self.assertNotIn("old 0", texts)
        self.assertEqual(texts[-1], "Shorter, please.")

    async def test_the_recap_read_shows_the_new_state(self) -> None:
        rid = await self.answered()
        await web.rewrite(
            self.web, self.session, self.ref, {"requestId": rid, "corrections": ["c1"]}
        )
        shown = await read_recap(self.web, self.session, self.ref)
        self.assertFalse(shown["canPost"])  # being written again


class TheLateWorker(WithRequests):
    async def test_a_worker_whose_claim_was_taken_back_cannot_overwrite_the_answer(self) -> None:
        await self.send()
        first = await self.requests.claim_next(THURSDAY.id)
        assert first is not None
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_requests SET claimed_at = 1")  # a stalled claim
        second = await self.requests.claim_next(THURSDAY.id)
        assert second is not None and second.claimed_at != first.claimed_at
        self.assertTrue(
            await self.requests.finish(
                THURSDAY.id, second, answer=rq.answer_of(Sorted(corrections=("The real one.",)))
            )
        )
        late = rq.answer_of(Sorted(corrections=("Words the DM never read.",)))
        self.assertFalse(await self.requests.finish(THURSDAY.id, first, answer=late))
        got = await web.read_instructions(self.web, self.session, self.ref, first.id)
        self.assertEqual(got["points"]["corrections"][0]["text"], "The real one.")

    async def test_a_sort_that_hangs_ends_before_its_claim_can_be_taken_back(self) -> None:
        self.assertLess(rq.SORT_TIMEOUT_S, rq.RETAKE_AFTER_S)
        await self.send()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None

        async def hang(_text: str) -> Sorted:
            await asyncio.sleep(30)
            return POINTS

        from unittest.mock import patch

        with patch.object(rq, "SORT_TIMEOUT_S", 0.05):
            outcome = await rq.run_one(
                self.requests, THURSDAY.id, request,
                gate=AsyncMock(return_value=None), sort=hang,
            )  # fmt: skip
        self.assertEqual(outcome, "sort_failed")

    def test_an_empty_sort_says_so(self) -> None:
        from dmbot.recaps.feedback import NOTHING_REPLY

        self.assertEqual(
            rq.answer_of(Sorted()),
            {"corrections": [], "style": [], "replies": [NOTHING_REPLY]},
        )


class TheGate(unittest.IsolatedAsyncioTestCase):
    """The four things checked at the moment of use, before any AI call."""

    def request(self, user: int = 7) -> rq.Request:
        return rq.Request("a" * 32, "c" * 32, 100, user, "words", 1)

    async def stop(self, **kw: Any) -> str | None:
        base: dict[str, Any] = {
            "campaign": SimpleNamespace(dm_user_ids=frozenset({7})),
            "job": SimpleNamespace(status="draft"),
            "has_ai": True,
            "feedback_gate": AsyncMock(return_value=None),
        }
        base.update(kw)
        return await rq.stop_reason(self.request(), **base)

    async def test_all_four_pass_when_nothing_is_wrong(self) -> None:
        self.assertIsNone(await self.stop())

    async def test_each_failure_stops_it_before_the_ai(self) -> None:
        gate = AsyncMock(return_value=None)
        cases: dict[str, dict[str, Any]] = {
            "demoted": {"campaign": SimpleNamespace(dm_user_ids=frozenset({8}))},
            "posted": {"job": SimpleNamespace(status="posted")},
            "gone": {"job": None},
            "no campaign": {"campaign": None},
            "no ai": {"has_ai": False},
        }
        for name, kw in cases.items():
            self.assertEqual(await self.stop(feedback_gate=gate, **kw), rq.NOT_ALLOWED, name)
        gate.assert_not_awaited()  # none of them got as far as the limit check
        self.assertEqual(
            await self.stop(feedback_gate=AsyncMock(return_value=rq.TOO_SOON)), rq.TOO_SOON
        )


class TheRoutes(WithRequests):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        import httpx

        from dmbot.web.app import create_app
        from dmbot.web.payments import FakeProvider

        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(
            self.web, cast(Any, fx_alice()), [THURSDAY], now=NOW, days=30
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_the_words_are_accepted_with_202_and_can_be_polled(self) -> None:
        got = await self.client.post(f"/me/recaps/{self.ref}/instructions", json={"text": "hello"})
        self.assertEqual(got.status_code, 202, got.text)
        rid = got.json()["requestId"]
        poll = await self.client.get(f"/me/recaps/{self.ref}/instructions/{rid}")
        self.assertEqual((poll.status_code, poll.json()), (200, {"state": "waiting"}))

    async def test_a_big_body_is_refused_by_its_declared_length_and_when_streamed(self) -> None:
        url = f"/me/recaps/{self.ref}/instructions"
        declared = await self.client.post(
            url, content=b"x" * 9000, headers={"content-type": "application/json"}
        )
        self.assertEqual((declared.status_code, declared.json()["error"]), (413, "too_big"))

        async def chunks() -> Any:
            for _ in range(10):
                yield b"x" * 1000  # no length up front

        streamed = await self.client.post(
            url, content=chunks(), headers={"content-type": "application/json"}
        )
        self.assertEqual((streamed.status_code, streamed.json()["error"]), (413, "too_big"))

    async def test_a_long_text_a_bad_body_and_too_many_requests(self) -> None:
        url = f"/me/recaps/{self.ref}/instructions"
        long = await self.client.post(url, json={"text": "x" * 1001})
        self.assertEqual((long.status_code, long.json()["error"]), (413, "too_big"))
        bad = await self.client.post(
            url, content=b"{nope", headers={"content-type": "application/json"}
        )
        self.assertEqual((bad.status_code, bad.json()["error"]), (422, "bad_text"))
        codes = []
        for _ in range(14):
            got = await self.client.post(url, json={"text": "hi"})
            codes.append(got.status_code)
        self.assertIn(429, codes)

    async def test_the_refusal_codes_map_to_the_shapes_statuses(self) -> None:
        self.assertEqual(web.STATUS["not_allowed"], (403, "not_allowed"))
        self.assertEqual(web.STATUS["not_editable"][0], 409)
        self.assertEqual(web.STATUS["no_tries"][0], 409)
        self.assertEqual(web.STATUS["too_soon"][0], 429)
        self.assertEqual(web.STATUS["bad_choice"][0], 422)

    async def test_rewrite_over_http(self) -> None:
        rid = await self.answered()
        got = await self.client.post(
            f"/me/recaps/{self.ref}/rewrite", json={"requestId": rid, "corrections": ["c1"]}
        )
        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(got.json()["needsApproval"])
        again = await self.client.post(
            f"/me/recaps/{self.ref}/rewrite", json={"requestId": rid, "corrections": ["c1"]}
        )
        self.assertEqual((again.status_code, again.json()["error"]), (404, "not_found"))


def fx_alice() -> Any:
    from tests.test_web_api import ALICE

    return ALICE


class TheDoorDetails(WithRequests):
    async def test_the_same_note_in_another_case_or_spacing_is_kept_once(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
                " created_at) VALUES (%s, %s, 1, %s, %s, 0)",
                (THURSDAY.id, self.one.id, "shorter, please.", fx.ALICE),
            )
        rid = await self.answered(Sorted(style=("  Shorter,   PLEASE. ",)))
        await web.rewrite(self.web, self.session, self.ref, {"requestId": rid, "style": ["s1"]})
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT text FROM recap_style_notes")
            self.assertEqual([r["text"] for r in await cur.fetchall()], ["Shorter, PLEASE."])

    async def test_adding_a_request_sweeps_the_campaigns_hour_old_rows(self) -> None:
        old = await self.send()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_requests SET created_at = 1")
        await self.send()
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT id FROM recap_requests")
            ids = [r["id"] for r in await cur.fetchall()]
        self.assertNotIn(old, ids)
        self.assertEqual(len(ids), 1)
