"""The DM's recap page (#1382), run as the website's own database role: only a DM of the
campaign sees or changes a recap; edits, Post, Try again and Don't post are checked in the
database, not just the route; the address door answers only while it is open; and the bot's
Post step re-checks the plan, the chat channel, secret names and who is still recorded."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import psycopg

from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.recaps import notify
from dmbot.recaps.store import Job, RecapStore
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError, decide, edit_recap, list_recaps, read_recap
from tests import recap_fixtures as fx
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}
BOB = SimpleNamespace(id=2002, name="Bob", email="bob@example.com")
NOW = int(time.time())


def callers(needle: str) -> set[str]:
    """Which source files mention this: a new caller of a door fails here and must be looked at."""
    root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    return {
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if needle in f.read_text(encoding="utf-8")
    }


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.campaigns = CampaignStore(self.db)
        # Alice DMs "One"; Bob DMs "Two" in the same server.
        self.one = await self.campaigns.create(THURSDAY.id, "One", ALICE.id)
        self.two = await self.campaigns.create(THURSDAY.id, "Two", BOB.id)
        self.store = RecapStore(self.db)
        for campaign, dm in ((self.one, ALICE.id), (self.two, BOB.id)):
            await self.store.enqueue(THURSDAY.id, campaign.id, 100, dm_user_id=dm)
            await self.store.update(
                THURSDAY.id, campaign.id, 100, state="ready", title="The Silver Lock",
                summary=fx.SUMMARY, original=fx.SUMMARY, housekeeping=["Mira drew her blade."],
                music=["m"], speakers=[fx.ALICE],
            )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        self.session = found
        self.ref = notify.job_ref(THURSDAY.id, self.one.id, 100)
        self.other = notify.job_ref(THURSDAY.id, self.two.id, 100)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def state(self, campaign: str) -> Job:
        job = await self.store.get(THURSDAY.id, campaign, 100)
        assert job is not None
        return job


class TheDmsPage(Base):
    async def test_a_dm_sees_only_their_own_campaigns_recaps(self) -> None:
        listed = await list_recaps(self.web, self.session)
        self.assertEqual([r["ref"] for r in listed], [self.ref])
        self.assertEqual(listed[0]["campaign"], "One")
        one = await read_recap(self.web, self.session, self.ref)
        self.assertEqual(one["title"], "The Silver Lock")
        self.assertNotIn("《", one["text"])  # the cues are hidden on the page
        self.assertIn("《Tense》", one["summary"])  # and kept for the edit box
        self.assertTrue(one["canPost"] and one["canEdit"] and one["canTryAgain"])
        self.assertEqual(one["triesLeft"], 2)

    async def test_another_campaigns_recap_looks_like_none_at_all(self) -> None:
        for call in (
            read_recap(self.web, self.session, self.other),
            decide(self.web, self.session, self.other, "post"),
            edit_recap(self.web, self.session, self.other, summary="x", housekeeping=[]),
        ):
            with self.assertRaises(RecapError) as caught:
                await call
            self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual((await self.state(self.two.id)).status, "draft")

    async def test_a_server_outside_the_sessions_list_and_a_bad_ref_are_not_found(self) -> None:
        for ref in (notify.job_ref(999, self.one.id, 100), "nonsense", f"111.{self.one.id}.7"):
            with self.assertRaises(RecapError):
                await read_recap(self.web, self.session, ref)

    async def test_an_edit_is_saved_and_the_original_is_kept(self) -> None:
        shown = await edit_recap(
            self.web, self.session, self.ref, summary="《Calm》 Edited by the DM.",
            housekeeping=["  Mira   rested. ", ""],
        )  # fmt: skip
        self.assertEqual(shown["housekeeping"], ["Mira rested."])
        job = await self.state(self.one.id)
        self.assertEqual(job.summary, "《Calm》 Edited by the DM.")
        self.assertEqual(job.original, fx.SUMMARY)  # the diff against the draft stays possible

    async def test_edits_that_are_empty_or_too_big_are_refused(self) -> None:
        for summary, bullets in (
            ("  ", []),
            ("x" * 4001, []),
            ("ok", ["b"] * 13),
            ("ok", ["b" * 141]),
        ):
            with self.assertRaises(RecapError) as caught:
                await edit_recap(
                    self.web, self.session, self.ref, summary=summary, housekeeping=bullets
                )
            self.assertEqual(caught.exception.code, "bad_text")

    async def test_the_website_cannot_write_the_bots_columns(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column, value in (("speakers", "'{5}'"), ("calls", "0"), ("original", "'x'"),
                                  ("dm_user_id", "5"), ("emailed_at", "5")):  # fmt: skip
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE recap_jobs SET {column} = {value}")

    async def test_a_player_who_is_not_a_dm_cannot_even_read_it(self) -> None:
        await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)  # type: ignore[arg-type]
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE user_id = %s", (ALICE.id,))
        session = await sessions.find(self.web, token, now=NOW)
        assert session is not None
        self.assertEqual(await list_recaps(self.web, session), [])

    async def test_post_marks_it_for_the_bot_and_only_once(self) -> None:
        shown = await decide(self.web, self.session, self.ref, "post")
        self.assertEqual(shown["status"], "posting")
        self.assertFalse(shown["canEdit"] or shown["canPost"])
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")
        with self.assertRaises(RecapError):  # no edits once it is on its way
            await edit_recap(self.web, self.session, self.ref, summary="x", housekeeping=[])

    async def test_dont_post_leaves_it_out_for_good(self) -> None:
        shown = await decide(self.web, self.session, self.ref, "drop")
        self.assertEqual(shown["status"], "dropped")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "again")

    async def test_try_again_is_offered_twice_and_queues_a_new_job(self) -> None:
        first = await decide(self.web, self.session, self.ref, "again")
        self.assertEqual(
            (first["state"], first["triesLeft"], first["working"]), ("queued", 1, True)
        )
        job = await self.state(self.one.id)
        self.assertEqual((job.tries, job.state, job.beats), (1, "queued", []))
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'failed'")
        second = await decide(self.web, self.session, self.ref, "again")
        self.assertEqual(second["triesLeft"], 0)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'ready'")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "again")
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "nonsense")

    async def test_a_recap_still_being_written_cannot_be_posted_or_edited(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'prose'")
        shown = await read_recap(self.web, self.session, self.ref)
        self.assertTrue(shown["working"])
        with self.assertRaises(RecapError):
            await decide(self.web, self.session, self.ref, "post")


class TheRoutes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_list_read_edit_and_decide(self) -> None:
        listed = await self.client.get("/me/recaps")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()["recaps"]), 1)
        got = await self.client.get(f"/me/recaps/{self.ref}")
        self.assertEqual(got.json()["title"], "The Silver Lock")
        saved = await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": "《Calm》 Hi.", "housekeeping": []}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        posted = await self.client.post(f"/me/recaps/{self.ref}/post")
        self.assertEqual(posted.json()["status"], "posting")
        again = await self.client.post(f"/me/recaps/{self.ref}/post")
        self.assertEqual((again.status_code, again.json()["error"]), (409, "not_allowed"))

    async def test_not_found_bad_body_and_signed_out(self) -> None:
        self.assertEqual((await self.client.get(f"/me/recaps/{self.other}")).status_code, 404)
        self.assertEqual((await self.client.get("/me/recaps/nonsense")).status_code, 404)
        bad = await self.client.put(f"/me/recaps/{self.ref}", json={"summary": 5})
        self.assertEqual(bad.status_code, 422)
        big = await self.client.put(
            f"/me/recaps/{self.ref}", json={"summary": "x" * 5000, "housekeeping": []}
        )
        self.assertEqual((big.status_code, big.json()["error"]), (422, "bad_text"))
        self.assertEqual((await self.client.post(f"/me/recaps/{self.ref}/zap")).status_code, 404)
        anonymous = httpx.AsyncClient(
            transport=self.client._transport,
            base_url=API,
            headers=HEADERS,
        )
        self.assertEqual((await anonymous.get("/me/recaps")).status_code, 401)
        await anonymous.aclose()


class TheAddressDoor(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)

    async def test_it_answers_for_a_dm_with_a_recap_through_the_door(self) -> None:
        self.assertEqual(await self.store.address(THURSDAY.id, ALICE.id), ALICE.email)

    async def test_it_answers_nothing_for_anyone_else_or_with_the_door_shut(self) -> None:
        self.assertIsNone(await self.store.address(THURSDAY.id, 55555))  # no recap job
        async with self.db.guild(THURSDAY.id) as conn:  # the door not opened
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (ALICE.id,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["a"])
        async with self.db.recap_mail(111) as conn:  # another server's recaps are not visible
            cur = await conn.execute("SELECT dmbot_recap_address(%s) AS a", (BOB.id,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["a"])

    def test_only_the_recap_code_opens_the_door(self) -> None:
        self.assertEqual(callers(".recap_mail("), {"recaps/store.py", "schema.py"})
        self.assertEqual(callers("dmbot_recap_address("), {"recaps/store.py", "schema.py"})
        self.assertEqual(callers("dmbot.recap_mail"), {"schema.py", "db.py"})


class Sent:
    def __init__(self) -> None:
        self.mail: list[Any] = []

    async def __call__(self, message: Any) -> None:
        self.mail.append(message)


class TheEmail(Base):
    async def test_the_dm_gets_the_title_and_a_link_never_the_recap(self) -> None:
        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        job = await self.state(self.one.id)
        result = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertEqual(result, "sent")
        body = outbox.mail[0].get_content()
        self.assertIn("The Silver Lock", body)
        self.assertIn(f"{SITE}/account/recaps/{self.ref}", body)
        self.assertNotIn("Dusk settled", body)
        self.assertNotIn("Oskar", body)
        # Once for each session and kind.
        again = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertEqual(again, "already_sent")

    async def test_a_failed_job_emails_the_reason_in_plain_words(self) -> None:
        from dataclasses import replace

        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        job = replace(await self.state(self.one.id), state="failed", last_error="Try tomorrow.")
        await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.one.id, job)
        self.assertIn("couldn't write", outbox.mail[0]["Subject"])
        self.assertIn("Try tomorrow.", outbox.mail[0].get_content())

    async def test_no_site_address_or_no_account_address_sends_nothing(self) -> None:
        from dmbot.mail import Mailer
        from tests.test_web_email_signin import LINK_SECRET, SMTP

        outbox = Sent()
        mailer = Mailer(self.db, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)
        job = await self.state(self.one.id)
        self.assertEqual(
            await notify.tell_the_dm(mailer, self.store, "", THURSDAY.id, self.one.id, job),
            "not_ready",
        )
        # Bob has no website account, so there is no address to write to.
        bobs = await self.state(self.two.id)
        result = await notify.tell_the_dm(mailer, self.store, SITE, THURSDAY.id, self.two.id, bobs)
        self.assertEqual(result, "no_address")
        self.assertEqual(outbox.mail, [])


def fake_bot(job: Job | None, *, allowed: bool = True, secrets: list[str] | None = None,
             channel: Any = "default", agreed: set[int] | None = None) -> Any:  # fmt: skip
    from dmbot.bot import DMBot

    sent = AsyncMock()
    sent.guild = SimpleNamespace(id=THURSDAY.id)
    store = SimpleNamespace(
        get=AsyncMock(return_value=job),
        posted=AsyncMock(return_value=True),
        back_to_draft=AsyncMock(),
        reopen=AsyncMock(),
        to_post=AsyncMock(return_value=[("c" * 32, 100)]),
    )
    campaign = SimpleNamespace(id="c" * 32, dmbchat_channel_id=55)
    bot = SimpleNamespace(
        recaps=store,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        plan_allows=AsyncMock(return_value=allowed),
        get_channel=lambda cid: sent if channel == "default" else channel,
        recap_secret_names=AsyncMock(return_value=[] if secrets is None else secrets),
        consent=SimpleNamespace(
            consenting=AsyncMock(return_value=agreed if agreed is not None else {fx.ALICE})
        ),
    )
    bot._post_one_recap = lambda g, c, a: DMBot._post_one_recap(bot, g, c, a)
    bot.sent = sent
    return bot


class ThePostStep(unittest.IsolatedAsyncioTestCase):
    def job(self, **kw: Any) -> Job:
        from dataclasses import replace

        base = Job(
            100, fx.DM, "ready", 0, 0, 0, 0, 3, "", (), [], "The Empty Hall",
            "《Tense》 ((slow)) Dusk fell on Mira.", "《Tense》 Dusk fell.",
            ("Mira drew her blade.",),
            ("m",), 4, "posting", 0, (fx.ALICE,), {}, None, 1, 1,
        )  # fmt: skip
        return replace(base, **kw)

    async def test_it_posts_without_cues_and_marks_it_posted_once(self) -> None:
        bot = fake_bot(self.job())
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        text = "".join(call.args[0] for call in bot.sent.send.await_args_list)
        self.assertIn("Dusk fell on Mira.", text)
        self.assertNotIn("《", text)
        self.assertIn("approved by your DM", text)
        bot.recaps.posted.assert_awaited_once()
        kwargs = bot.sent.send.await_args.kwargs
        self.assertEqual(kwargs["allowed_mentions"].users, False)

    async def test_each_check_that_fails_posts_nothing_and_tells_the_dm_why(self) -> None:
        cases = {
            "plan": fake_bot(self.job(), allowed=False),
            "channel": fake_bot(self.job(), channel=None),
            "other server": fake_bot(
                self.job(), channel=SimpleNamespace(send=AsyncMock(), guild=SimpleNamespace(id=2))
            ),
            "secret": fake_bot(self.job(), secrets=["Mira"]),
            "unreadable": fake_bot(self.job()),
            "consent": fake_bot(self.job(), agreed=set()),
        }
        cases["unreadable"].recap_secret_names = AsyncMock(return_value=None)
        for name, bot in cases.items():
            with self.subTest(name):
                await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
                bot.recaps.posted.assert_not_awaited()
                bot.recaps.back_to_draft.assert_awaited_once()
                note = bot.recaps.back_to_draft.await_args.kwargs["note"]
                self.assertTrue(note and note[0].isupper(), note)
                if hasattr(bot.sent, "send"):
                    bot.sent.send.assert_not_awaited()

    async def test_a_send_that_fails_puts_it_back_as_a_draft(self) -> None:
        bot = fake_bot(self.job())
        bot.sent.send.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.recaps.reopen.assert_awaited_once()

    async def test_a_job_that_is_not_waiting_to_post_is_left_alone(self) -> None:
        bot = fake_bot(self.job(status="draft"))
        await bot._post_one_recap(THURSDAY.id, "c" * 32, 100)
        bot.sent.send.assert_not_awaited()
        bot.recaps.posted.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
