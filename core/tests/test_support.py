"""Private support tickets (#1340, #1373), run as the website's own database role: any signed-in
account with a plan row (Try It too, #1435) can send one, one per 10 minutes (free accounts: three
a day and one waiting); a person reads back only their own list (when,
subject, status), never the words, which only the admin page's door can read; the team is emailed
without its words; old and deleted accounts' tickets go; and its words reach no log."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx
import psycopg

from dmbot.config import ConfigError
from dmbot.db import Database, drop_schema
from dmbot.mail import Mailer
from dmbot.schema import MIGRATIONS
from dmbot.web import sessions, support
from dmbot.web.accounts import delete_person
from dmbot.web.admin import AdminSessions, FailedTries
from dmbot.web.app import create_app
from dmbot.web.feedback import FeedbackError
from dmbot.web.limits import RateLimit
from dmbot.web.payments import FakeProvider
from dmbot.web.settings import load_web_settings
from tests.pg import REQUIRE_DB, SUPERUSER_URL, TEST_URL, DatabaseTest
from tests.test_web_admin import ADMIN, HASH, PASSWORD
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_email_signin import LINK_SECRET, SMTP
from tests.test_web_feedback import FakeHumanCheck
from tests.test_web_role import ensure_web_role, web_url

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}
TEAM = "team@example.org"
SUBJECT = "My table's invoice looks wrong"
MESSAGE = "Hello, the plan on my last invoice is not the one I picked."
BOB = 8008  # another account: its tickets are not Alice's
NOW = 1_800_000_000
DAY = 86400


NO_ANSWER = {"answeredBy": None, "latest": None}


class Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = False

    async def __call__(self, message: EmailMessage) -> None:
        if self.fail:
            raise RuntimeError("the mail server is down")
        self.sent.append(message)


class SupportBase(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = NOW
        self.tick = 0.0  # the rate limit's own clock, in seconds
        self.outbox = Outbox()
        self.mailer = Mailer(
            self.web,
            SMTP,
            transport=self.outbox,
            site_url=SITE,
            link_secret=LINK_SECRET,
            clock=lambda: float(self.now),
        )
        self.human = FakeHumanCheck()
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30)
        await self.give_plan(ALICE.id, "table", "active")

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def give_plan(self, user_id: int, plan: str, status: str) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (user_id,))
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%s, %s, %s, 18, 0, 1, %s, %s, %s, %s, 0, 'fake', 'sub_1', 0, 0)",
                (
                    user_id,
                    plan,
                    status,
                    self.now - 100,
                    self.now + DAY,
                    self.now + DAY if status == "grace" else None,
                    self.now - DAY if status == "lapsed" else None,
                ),
            )

    async def tickets(self) -> list[dict[str, Any]]:
        """Every ticket, read as the database's administrator (the bot's role is held by the
        same policies, so it opens the admin door like the website does)."""
        async with self.db.support_admin() as conn:
            cur = await conn.execute("SELECT * FROM support_tickets ORDER BY id")
            return list(await cur.fetchall())

    async def rows(self) -> list[support.Row]:
        """Every ticket, newest first (the admin list's first page, big enough to hold them)."""
        return (await support.page(self.web, status=None, number=1, size=1000))[0]

    async def add_row(self, user_id: int, created_at: int, subject: str = "Old") -> None:
        async with self.web.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO support_tickets (created_at, user_id, plan, subject, message)"
                " VALUES (%s, %s, 'table', %s, 'Words')",
                (created_at, user_id, subject),
            )


class SupportRoute(SupportBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.build()

    def build(self, *, human: bool = False, team: str = TEAM) -> None:
        self.limit = RateLimit(clock=lambda: self.tick)
        self.app = create_app(
            settings(link_token_secret=LINK_SECRET, support_team_email=team),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            human_check=self.human if human else None,
            mailer=self.mailer,
            support_limit=self.limit,
            clock=lambda: self.now,
        )

    async def client(self, user_id: int = ALICE.id) -> httpx.AsyncClient:
        token = await sessions.sign_in_by_email(self.web, user_id, now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        return client

    async def send(self, client: httpx.AsyncClient, **fields: Any) -> httpx.Response:
        body = {"subject": SUBJECT, "message": MESSAGE, **fields}
        return await client.post("/support", json=body, headers=HEADERS)

    async def test_a_paying_account_sends_a_ticket_that_is_kept_privately(self) -> None:
        client = await self.client()
        answer = await self.send(client)
        self.assertEqual((answer.status_code, answer.json()), (201, {"ok": True, "reply": True}))
        (row,) = await self.tickets()
        self.assertEqual(
            {k: row[k] for k in ("created_at", "user_id", "plan", "subject", "message")},
            {
                "created_at": self.now,
                "user_id": ALICE.id,
                "plan": "table",
                "subject": SUBJECT,
                "message": MESSAGE,
            },
        )
        self.assertEqual((row["source"], row["status"]), ("website", "new"))
        # The answer carries nothing back: not the words, not an id to look the ticket up by.
        self.assertNotIn(MESSAGE, answer.text)

    async def test_the_answer_says_whether_the_team_can_reply_by_email(self) -> None:
        client = await self.client()
        # Alice signed in with a Discord email, so there is an address to reply to.
        self.assertEqual((await self.send(client)).json(), {"ok": True, "reply": True})
        self.tick += 11 * 60
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET email = NULL, payment_email = NULL")
        self.assertEqual((await self.send(client)).json(), {"ok": True, "reply": False})
        # Either address counts: the payment company's alone is enough.
        self.tick += 11 * 60
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET payment_email = 'pays@example.org'")
        self.assertEqual((await self.send(client)).json(), {"ok": True, "reply": True})

    async def test_the_team_is_emailed_that_one_waits_but_never_the_words(self) -> None:
        await self.send(await self.client())
        (mail,) = self.outbox.sent
        text = f"{mail['Subject']}\n{mail.get_content()}"
        self.assertEqual(str(mail["To"]), TEAM)
        self.assertIn("table", text)  # the plan
        self.assertIn(f"{SITE}/admin", text)
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn(MESSAGE, text)
        self.assertNotIn("List-Unsubscribe", mail)  # to the team, not a person

    async def test_a_failed_email_loses_nothing(self) -> None:
        self.outbox.fail = True
        answer = await self.send(await self.client())
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(len(await self.tickets()), 1)

    async def test_with_no_team_address_the_ticket_is_kept_and_nobody_is_emailed(self) -> None:
        self.build(team="")
        self.assertEqual((await self.send(await self.client())).status_code, 201)
        self.assertEqual((len(await self.tickets()), self.outbox.sent), (1, []))

    async def test_any_account_with_a_plan_row_may_send(self) -> None:
        # Try It, a lapsed paid plan, the payment grace and a working plan all may write (#1435).
        for plan, status in (("try-it", "active"), ("table", "lapsed"), ("table", "grace")):
            await self.give_plan(ALICE.id, plan, status)
            self.tick += 3600  # past the 10 minutes between tickets
            await self.close_all()
            answer = await self.send(await self.client())
            self.assertEqual((plan, status, answer.status_code), (plan, status, 201))
        tickets = await self.tickets()
        self.assertEqual([t["plan"] for t in tickets], ["try-it", "table", "table"])

    async def test_no_account_yet_may_not_send(self) -> None:
        async with self.db.plan_writer(ALICE.id) as conn:  # no plan at all
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (ALICE.id,))
        answer = await self.send(await self.client())
        self.assertEqual((answer.status_code, answer.json()), (403, {"error": "not_paying"}))
        self.assertEqual(await self.tickets(), [])

    async def close_all(self) -> None:
        async with self.db.support_admin() as conn:
            await conn.execute("UPDATE support_tickets SET status = 'closed'")

    async def test_a_free_account_has_one_question_waiting_at_a_time(self) -> None:
        await self.give_plan(ALICE.id, "try-it", "active")
        client = await self.client()
        self.assertEqual((await self.send(client)).status_code, 201)
        self.tick += 3600
        answer = await self.send(client)
        self.assertEqual((answer.status_code, answer.json()), (429, {"error": "one_waiting"}))
        self.assertEqual(len(await self.tickets()), 1)
        await self.close_all()  # the team answered it
        self.assertEqual((await self.send(client)).status_code, 201)

    async def test_a_free_account_sends_three_a_day(self) -> None:
        await self.give_plan(ALICE.id, "table", "lapsed")
        client = await self.client()
        for _ in range(3):
            self.tick += 3600
            self.assertEqual((await self.send(client)).status_code, 201)
            await self.close_all()
        self.tick += 3600
        answer = await self.send(client)
        self.assertEqual((answer.status_code, answer.json()), (429, {"error": "daily"}))
        # A day after the first one, it may write again.
        async with self.db.support_admin() as conn:
            await conn.execute("UPDATE support_tickets SET created_at = created_at - 90000")
        self.assertEqual((await self.send(client)).status_code, 201)

    async def test_another_accounts_tickets_do_not_count_against_a_free_account(self) -> None:
        await self.add_row(BOB, self.now)
        await self.give_plan(ALICE.id, "try-it", "active")
        self.assertEqual((await self.send(await self.client())).status_code, 201)

    async def test_a_paid_account_is_not_held_to_the_free_guard(self) -> None:
        client = await self.client()
        for _ in range(4):
            self.tick += 3600
            self.assertEqual((await self.send(client)).status_code, 201)

    async def test_a_refused_ticket_does_not_use_the_turn(self) -> None:
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (ALICE.id,))
        client = await self.client()
        self.assertEqual((await self.send(client)).status_code, 403)
        self.assertEqual((await self.send(client, message="")).status_code, 400)
        await self.give_plan(ALICE.id, "table", "active")
        self.assertEqual((await self.send(client)).status_code, 201)

    async def test_signed_out_is_refused(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API
        ) as anonymous:
            answer = await self.send(anonymous)
        self.assertEqual(answer.status_code, 401)
        self.assertEqual(await self.tickets(), [])

    async def test_one_ticket_per_account_per_ten_minutes(self) -> None:
        client = await self.client()
        self.assertEqual((await self.send(client)).status_code, 201)
        self.tick += 9 * 60
        again = await self.send(client)
        self.assertEqual((again.status_code, again.json()), (429, {"error": "slow_down"}))
        self.tick += 61
        self.assertEqual((await self.send(client)).status_code, 201)
        self.assertEqual(len(await self.tickets()), 2)

    async def test_the_limit_is_per_account_not_per_address(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.give_plan(BOB, "table", "active")
        self.assertEqual((await self.send(await self.client(ALICE.id))).status_code, 201)
        self.assertEqual((await self.send(await self.client(BOB))).status_code, 201)

    async def test_the_words_are_checked_and_cleaned(self) -> None:
        client = await self.client()
        for fields, code in (
            ({"message": ""}, "empty"),
            ({"subject": "   "}, "empty"),
            ({"subject": "s" * 121}, "subject_too_long"),
            ({"message": "m" * 4001}, "too_long"),
            ({"subject": 7}, "bad_request"),
            ({"message": ["x"]}, "bad_request"),
            ({"turnstile": 5}, "bad_request"),
        ):
            answer = await self.send(client, **fields)
            self.assertEqual((answer.status_code, answer.json()["error"]), (400, code), fields)
        self.assertEqual(
            (await client.post("/support", content=b"[1,", headers=HEADERS)).status_code, 400
        )
        self.assertEqual(await self.tickets(), [])  # no refusal used the turn
        # At the limits: 120 and 4,000 are fine. A subject is one line; control characters go.
        ok = await self.send(client, subject="a\n b" + "s" * 116, message="m" * 3999 + "\x00‮!")
        self.assertEqual(ok.status_code, 201)
        (row,) = await self.tickets()
        self.assertEqual(row["subject"], "a b" + "s" * 116)
        self.assertEqual(row["message"], "m" * 3999 + "!")

    async def test_a_huge_body_is_refused_before_it_is_read(self) -> None:
        client = await self.client()
        answer = await client.post("/support", content=b"x" * (49 * 1024), headers=HEADERS)
        self.assertEqual((answer.status_code, answer.json()), (413, {"error": "too_long"}))

    async def test_turnstile_is_asked_when_it_is_on(self) -> None:
        self.build(human=True)
        client = await self.client()
        none = await self.send(client)
        self.assertEqual((none.status_code, none.json()["error"]), (400, "not_human"))
        wrong = await self.send(client, turnstile="robot")
        self.assertEqual((wrong.status_code, wrong.json()["error"]), (400, "not_human"))
        again = await self.send(client, turnstile="person")  # a wrong answer used the turn
        self.assertEqual(again.status_code, 429)
        self.tick += 11 * 60
        self.assertEqual((await self.send(client, turnstile="person")).status_code, 201)
        self.assertEqual(len(await self.tickets()), 1)

    async def test_a_human_check_that_cannot_run_gives_the_turn_back(self) -> None:
        class Down:
            async def verify(self, token: str, address: str) -> bool:
                raise FeedbackError("Turnstile answered 502")

        self.human = Down()  # type: ignore[assignment]
        self.build(human=True)
        client = await self.client()
        with self.assertLogs(level=logging.WARNING) as seen:
            answer = await self.send(client, turnstile="person")
        self.assertEqual(
            (answer.status_code, answer.json()), (502, {"error": "support_unavailable"})
        )
        self.assertNotIn(SUBJECT, "\n".join(r.getMessage() for r in seen.records))
        self.assertEqual(await self.tickets(), [])
        self.human = FakeHumanCheck()
        self.build(human=True)  # a new app, but the same account: its turn was not used up
        self.limit = RateLimit(clock=lambda: self.tick)
        self.assertEqual(
            (await self.send(await self.client(), turnstile="person")).status_code, 201
        )

    async def test_a_lone_surrogate_is_a_plain_refusal_not_a_server_error(self) -> None:
        client = await self.client()
        answer = await client.post(
            "/support", content=b'{"subject": "s", "message": "\\ud800"}', headers=HEADERS
        )
        self.assertEqual((answer.status_code, answer.json()), (400, {"error": "empty"}))

    async def test_the_words_reach_no_log(self) -> None:
        client = await self.client()
        with self.assertLogs(level=logging.DEBUG) as seen:
            await self.send(client)
            logging.getLogger("dmbot.test").debug("keep the capture open")
        logged = "\n".join(r.getMessage() for r in seen.records)
        self.assertIn(f"Support ticket added by user {ALICE.id}", logged)
        self.assertNotIn(SUBJECT, logged)
        self.assertNotIn("invoice", logged)

    async def test_a_person_sees_their_own_questions_newest_first_with_no_words(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.add_row(ALICE.id, self.now - 100, "older")
        await self.add_row(ALICE.id, self.now, "newer")
        await self.add_row(BOB, self.now - 50, "bob's, not for Alice")
        client = await self.client()
        answer = await client.get("/support/mine")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            answer.json(),
            {
                "tickets": [
                    {"at": self.now, "subject": "newer", "status": "new", **NO_ANSWER},
                    {"at": self.now - 100, "subject": "older", "status": "new", **NO_ANSWER},
                ],
                "reply": True,
            },
        )
        # Neither the words nor anyone else's, and no ticket number to count the others by.
        self.assertNotIn("Words", answer.text)
        self.assertNotIn("bob", answer.text.lower())
        self.assertNotIn('"id"', answer.text)
        bobs = await (await self.client(BOB)).get("/support/mine")
        self.assertEqual([t["subject"] for t in bobs.json()["tickets"]], ["bob's, not for Alice"])

    async def test_the_status_the_team_sets_shows_in_the_list(self) -> None:
        await self.add_row(ALICE.id, self.now - 2, "one")
        await self.add_row(ALICE.id, self.now - 1, "two")
        await self.add_row(ALICE.id, self.now, "three")
        by_subject = {r.subject: r.id for r in await self.rows()}
        await support.set_status(self.web, by_subject["one"], "answered")
        await support.set_status(self.web, by_subject["two"], "closed")
        answer = await (await self.client()).get("/support/mine")
        self.assertEqual(
            [(t["subject"], t["status"]) for t in answer.json()["tickets"]],
            [("three", "new"), ("two", "closed"), ("one", "answered")],
        )

    async def ticket_id(self, subject: str) -> int:
        return {r.subject: r.id for r in await self.rows()}[subject]

    async def test_the_list_shows_who_answered_last_and_the_answers_first_line(self) -> None:
        await self.add_row(ALICE.id, self.now - 3, "helped")
        await self.add_row(ALICE.id, self.now - 2, "team")
        await self.add_row(ALICE.id, self.now - 1, "waiting")
        helped, team = await self.ticket_id("helped"), await self.ticket_id("team")
        await support.add_reply(
            self.web, helped, "ai", "Hi Alice,\nTry the Billing page.\nMore below.", now=1
        )
        await support.add_reply(self.web, team, "ai", "A first answer.", now=1)
        await support.add_reply(self.web, team, "customer", "That did not help.", now=2)
        await support.add_reply(self.web, team, "person", "  \n  I fixed it for you.  ", now=3)
        # What the person wrote by email is never sent back as an "answer".
        await support.add_reply(self.web, team, "customer", "Thanks, one more thing.", now=4)
        shown = {
            t["subject"]: t
            for t in (await (await self.client()).get("/support/mine")).json()["tickets"]
        }
        self.assertEqual(
            (shown["helped"]["answeredBy"], shown["helped"]["latest"]),
            ("helper", "Try the Billing page."),
        )
        self.assertEqual(
            (shown["team"]["answeredBy"], shown["team"]["latest"]),
            ("team", "I fixed it for you."),
        )
        self.assertEqual((shown["waiting"]["answeredBy"], shown["waiting"]["latest"]), (None, None))

    async def test_an_answer_is_cut_to_a_first_line_and_never_anyone_elses(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.add_row(ALICE.id, self.now - 2, "long")
        await self.add_row(BOB, self.now - 1, "bobs")
        long, bobs = await self.ticket_id("long"), await self.ticket_id("bobs")
        await support.add_reply(self.web, long, "ai", ("word " * 60).strip(), now=1)
        await support.add_reply(self.web, bobs, "ai", "Bob's private answer.", now=1)
        alice = (await (await self.client()).get("/support/mine")).text
        self.assertNotIn("Bob", alice)
        latest = json.loads(alice)["tickets"][0]["latest"]
        self.assertLessEqual(len(latest), support.ANSWER_LINE_MAX + 1)
        self.assertTrue(latest.endswith("…"))
        # The door answers nothing for a ticket that is not the signed-in person's.
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT * FROM dmbot_support_latest_answer(%s)", (bobs,))
            self.assertEqual(await cur.fetchall(), [])
        async with self.web.anonymous() as conn:
            cur = await conn.execute("SELECT * FROM dmbot_support_latest_answer(%s)", (long,))
            self.assertEqual(await cur.fetchall(), [])

    async def test_the_websites_role_cannot_read_a_replys_words_itself(self) -> None:
        await self.add_row(ALICE.id, self.now, "q")
        await support.add_reply(
            self.web, await self.ticket_id("q"), "ai", "Secret-ish answer.", now=1
        )
        async with self.web.user(ALICE.id) as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT text FROM support_replies")

    async def test_a_person_opens_their_own_ticket_and_sees_the_whole_conversation(self) -> None:
        await self.add_row(ALICE.id, self.now - 100, "q")
        ticket = await self.ticket_id("q")
        await support.add_reply(self.web, ticket, "ai", "Try the Billing page.", now=self.now - 90)
        await support.add_reply(
            self.web, ticket, "customer", "That did not help.", now=self.now - 80
        )
        await support.add_reply(
            self.web, ticket, "person", "I fixed it for you.", now=self.now - 70
        )
        answer = await (await self.client()).get(f"/support/mine/{self.now - 100}")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            answer.json(),
            {
                "messages": [
                    {"who": "you", "text": "Words", "at": self.now - 100},
                    {"who": "helper", "text": "Try the Billing page.", "at": self.now - 90},
                    {"who": "you", "text": "That did not help.", "at": self.now - 80},
                    {"who": "team", "text": "I fixed it for you.", "at": self.now - 70},
                ]
            },
        )
        # No ticket number comes back to count the other people's by.
        self.assertNotIn('"id"', answer.text)

    async def test_two_tickets_sent_in_one_second_each_open_their_own_conversation(self) -> None:
        first = await support.add(
            self.web, ALICE.id, "table", support.Ticket("one", "First words"), now=self.now
        )
        second = await support.add(
            self.web, ALICE.id, "table", support.Ticket("two", "Second words"), now=self.now
        )
        self.assertNotEqual(first, second)
        await support.add_reply(self.web, first, "ai", "Answer to one.", now=self.now)
        client = await self.client()
        listed = (await client.get("/support/mine")).json()["tickets"]
        self.assertEqual(sorted(t["subject"] for t in listed), ["one", "two"])
        self.assertEqual(len({t["at"] for t in listed}), 2)
        by_subject = {t["subject"]: t["at"] for t in listed}
        one = (await client.get(f"/support/mine/{by_subject['one']}")).json()["messages"]
        two = (await client.get(f"/support/mine/{by_subject['two']}")).json()["messages"]
        self.assertEqual([m["text"] for m in one], ["First words", "Answer to one."])
        self.assertEqual([m["text"] for m in two], ["Second words"])

    async def test_another_accounts_ticket_and_a_missing_one_both_answer_404(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.add_row(BOB, self.now - 5, "bobs")
        await support.add_reply(self.web, await self.ticket_id("bobs"), "ai", "Bob's.", now=1)
        client = await self.client()
        other = await client.get(f"/support/mine/{self.now - 5}")
        missing = await client.get(f"/support/mine/{self.now - 6}")
        self.assertEqual((other.status_code, missing.status_code), (404, 404))
        self.assertEqual(other.json(), missing.json())
        self.assertNotIn("Bob", other.text)
        # Bob does see his own.
        bobs = await (await self.client(BOB)).get(f"/support/mine/{self.now - 5}")
        self.assertEqual([m["text"] for m in bobs.json()["messages"]], ["Words", "Bob's."])

    async def test_a_time_too_big_for_the_database_is_a_404_not_a_crash(self) -> None:
        await self.add_row(ALICE.id, self.now, "q")
        client = await self.client()
        for sent in ("99999999999999999999", str(2**63), str(2**63 - 1), "0"):
            answer = await client.get(f"/support/mine/{sent}")
            self.assertEqual(
                (answer.status_code, answer.json()), (404, {"error": "not_found"}), sent
            )

    async def test_signed_out_gets_401_for_a_conversation(self) -> None:
        await self.add_row(ALICE.id, self.now, "q")
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get(f"/support/mine/{self.now}")).status_code, 401)

    async def test_the_conversation_door_answers_nothing_to_anyone_but_the_owner(self) -> None:
        await self.add_row(ALICE.id, self.now, "q")
        sql = "SELECT * FROM dmbot_support_mine_thread(%s)"
        async with self.web.anonymous() as conn:
            self.assertEqual(await (await conn.execute(sql, (self.now,))).fetchall(), [])
        async with self.web.user(BOB) as conn:
            self.assertEqual(await (await conn.execute(sql, (self.now,))).fetchall(), [])
        # The role still has no right on the words, and the admin door is not opened by this one.
        async with self.web.user(ALICE.id) as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT message FROM support_tickets")

    async def test_reading_a_conversation_logs_no_words(self) -> None:
        await self.add_row(ALICE.id, self.now, "q")
        await support.add_reply(
            self.web, await self.ticket_id("q"), "ai", "Secret-ish answer.", now=self.now
        )
        client = await self.client()
        with self.assertLogs(level=logging.DEBUG) as seen:
            await client.get(f"/support/mine/{self.now}")
            logging.getLogger("dmbot.test").debug("keep the capture open")
        logged = "\n".join(r.getMessage() for r in seen.records)
        self.assertNotIn("Secret-ish", logged)
        self.assertNotIn("Words", logged)

    async def test_the_list_is_at_most_twenty_and_empty_for_a_person_who_never_wrote(self) -> None:
        client = await self.client()
        self.assertEqual(
            (await client.get("/support/mine")).json(), {"tickets": [], "reply": False}
        )
        for n in range(23):
            await self.add_row(ALICE.id, self.now - 1000 + n, f"q{n}")
        tickets = (await client.get("/support/mine")).json()["tickets"]
        self.assertEqual(len(tickets), 20)
        self.assertEqual(tickets[0]["subject"], "q22")

    async def test_the_list_says_when_the_team_cannot_reply_by_email(self) -> None:
        await self.add_row(ALICE.id, self.now)
        client = await self.client()
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET email = NULL, payment_email = NULL")
        self.assertIs((await client.get("/support/mine")).json()["reply"], False)

    async def test_the_list_needs_a_sign_in_but_not_a_paid_plan(self) -> None:
        await self.add_row(ALICE.id, self.now)
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get("/support/mine")).status_code, 401)
        # Someone whose plan has since lapsed still sees what they asked.
        await self.give_plan(ALICE.id, "table", "lapsed")
        client = await self.client()
        self.assertEqual(len((await client.get("/support/mine")).json()["tickets"]), 1)

    async def test_me_says_whether_the_form_is_for_this_account(self) -> None:
        client = await self.client()
        self.assertIs((await client.get("/me")).json()["canSupport"], True)
        await self.give_plan(ALICE.id, "try-it", "active")
        self.assertIs((await client.get("/me")).json()["canSupport"], True)
        await self.give_plan(ALICE.id, "table", "lapsed")
        self.assertIs((await client.get("/me")).json()["canSupport"], True)
        async with self.db.plan_writer(ALICE.id) as conn:  # no account yet
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (ALICE.id,))
        self.assertIs((await client.get("/me")).json()["canSupport"], False)


def _bob() -> Any:
    from dataclasses import replace

    return replace(ALICE, id=BOB, name="Bob", email="bob@example.com")


class SupportKeptPrivately(SupportBase):
    """The database's side, as the website's role: add as yourself, read nothing."""

    async def test_a_person_adds_and_reads_back_their_own_list_but_cannot_change_or_delete(
        self,
    ) -> None:
        await self.add_row(ALICE.id, self.now)
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM support_tickets")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 1)  # their own ticket, since #1373
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("UPDATE support_tickets SET status = 'closed'")
            self.assertEqual(cur.rowcount, 0)
        async with self.web.user(BOB) as conn:  # nobody else's: only the account's own delete
            cur = await conn.execute("DELETE FROM support_tickets")
            self.assertEqual(cur.rowcount, 0)
        self.assertEqual(len(await self.tickets()), 1)

    async def test_a_person_sees_only_their_own_tickets_never_the_words(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.add_row(ALICE.id, self.now - 10, "alice's first")
        await self.add_row(ALICE.id, self.now, "alice's second")
        await self.add_row(BOB, self.now - 5, "bob's")
        for user, expected in ((ALICE.id, ["alice's first", "alice's second"]), (BOB, ["bob's"])):
            async with self.web.user(user) as conn:
                cur = await conn.execute(
                    "SELECT subject, status FROM support_tickets ORDER BY created_at"
                )
                rows = await cur.fetchall()
            self.assertEqual([r["subject"] for r in rows], expected)
            self.assertEqual({r["status"] for r in rows}, {"new"})
        # The words are out of reach of the website's role altogether: its own row, a
        # star, the other person's, and with the admin door open too.
        for query in (
            "SELECT message FROM support_tickets",
            "SELECT * FROM support_tickets",
            "SELECT id FROM support_tickets WHERE message = 'Words'",
        ):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with self.web.user(ALICE.id) as conn:
                    await conn.execute(query)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with self.web.support_admin() as conn:
                    await conn.execute(query)
        # No one is shown through a person's session by a guess at someone else's number.
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM support_tickets WHERE user_id = %s", (BOB,)
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)

    async def test_the_words_come_only_through_the_admin_door(self) -> None:
        await self.add_row(ALICE.id, self.now)
        ticket_id = (await self.tickets())[0]["id"]
        for scope in (self.web.user(ALICE.id), self.web.unscoped()):
            async with scope as conn:
                cur = await conn.execute("SELECT dmbot_support_message(%s) AS m", (ticket_id,))
                row = await cur.fetchone()
                assert row is not None
                self.assertIsNone(row["m"])
        async with self.web.support_admin() as conn:
            cur = await conn.execute("SELECT dmbot_support_message(%s) AS m", (ticket_id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["m"], "Words")
            cur = await conn.execute("SELECT dmbot_support_message(999999) AS m")
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["m"])
        (listed,) = await self.rows()
        self.assertEqual(listed.message, "Words")

    async def test_a_person_cannot_add_for_someone_else_or_as_answered(self) -> None:
        for user, status, source in (
            (BOB, "new", "website"),
            (ALICE.id, "closed", "website"),
            (ALICE.id, "new", "email"),
        ):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with self.web.user(ALICE.id) as conn:
                    await conn.execute(
                        "INSERT INTO support_tickets"
                        " (created_at, user_id, plan, subject, message, source, status)"
                        " VALUES (1, %s, 'table', 's', 'm', %s, %s)",
                        (user, source, status),
                    )
        self.assertEqual(await self.tickets(), [])

    async def test_without_the_admin_door_the_website_reads_nothing_and_with_it_only_status(
        self,
    ) -> None:
        await self.add_row(ALICE.id, self.now)
        async with self.web.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM support_tickets")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)
        (ticket,) = await self.rows()
        self.assertEqual((ticket.status, ticket.user_id), ("new", ALICE.id))
        self.assertTrue(await support.set_status(self.web, ticket.id, "answered"))
        self.assertEqual((await self.rows())[0].status, "answered")
        # The words are not a column the website may read, door or no door (#1373): they come
        # through dmbot_support_message, which `rows` used above.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.web.support_admin() as conn:
                await conn.execute("SELECT message FROM support_tickets")
        # Even through the door, nothing but the status may change (the column grant).
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.web.support_admin() as conn:
                await conn.execute("UPDATE support_tickets SET message = 'edited'")

    async def test_the_reply_address_is_read_only_through_the_admin_door(self) -> None:
        await self.add_row(ALICE.id, self.now)
        # The person can't read it, not even their own (the function answers nothing without the
        # door), and the website with nobody set can't either.
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT dmbot_support_email(%s) AS e", (ALICE.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["e"])
        async with self.web.unscoped() as conn:
            cur = await conn.execute("SELECT dmbot_support_email(%s) AS e", (ALICE.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["e"])
        # Through the door it is the Discord address, or the payment company's when there is one.
        (ticket,) = await self.rows()
        self.assertEqual(ticket.email, "belleros@example.com")
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET payment_email = 'pays@example.org'")
        self.assertEqual((await self.rows())[0].email, "pays@example.org")
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET email = NULL, payment_email = NULL")
        self.assertIsNone((await self.rows())[0].email)

    async def test_an_account_with_no_ticket_gets_no_address_even_with_the_door_open(self) -> None:
        # The door is one the website's role can open itself, so it can't be a way to read the
        # address of an account that never wrote to us.
        async with self.web.support_admin() as conn:
            cur = await conn.execute("SELECT dmbot_support_email(%s) AS e", (ALICE.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["e"])
        await self.add_row(ALICE.id, self.now)
        async with self.web.support_admin() as conn:
            cur = await conn.execute("SELECT dmbot_support_email(%s) AS e", (ALICE.id,))
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["e"], "belleros@example.com")
            # And only that account's: another person's address stays out of reach.
            cur = await conn.execute("SELECT dmbot_support_email(%s) AS e", (BOB,))
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["e"])

    async def test_the_address_is_never_kept_in_the_ticket(self) -> None:
        await self.add_row(ALICE.id, self.now)
        async with self.db.support_admin() as conn:
            cur = await conn.execute("SELECT * FROM support_tickets")
            (row,) = await cur.fetchall()
        self.assertNotIn("email", row)
        self.assertNotIn("belleros@example.com", repr(row))

    async def test_the_door_does_not_open_other_account_tables_to_the_website(self) -> None:
        # The function's policy is for its owner only: the website can't use the setting itself.
        async with self.web.unscoped() as conn:
            await conn.execute("SELECT set_config('dmbot.support_email_lookup', 'on', true)")
            cur = await conn.execute("SELECT count(*) AS n FROM web_users")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)

    async def test_the_list_is_newest_first(self) -> None:
        await self.add_row(ALICE.id, self.now - 50, "older")
        await self.add_row(ALICE.id, self.now, "newer")
        self.assertEqual([r.subject for r in await self.rows()], ["newer", "older"])

    async def test_an_unknown_ticket_is_not_found_and_a_bad_status_is_refused(self) -> None:
        self.assertFalse(await support.set_status(self.web, 999, "closed"))

    async def test_the_sweep_deletes_old_tickets_without_reading_any(self) -> None:
        # The database counts "a year" from the real clock, so the rows do too.
        real = int(time.time())
        await self.add_row(ALICE.id, real - 366 * DAY, "old")
        await self.add_row(ALICE.id, real - 360 * DAY, "recent")
        async with self.web.cleanup("old-tickets") as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM support_tickets")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # it may delete them, not read them
        self.assertEqual(await support.forget_old(self.web), 1)
        self.assertEqual([r["subject"] for r in await self.tickets()], ["recent"])
        # The feedback sweep's own setting doesn't open this table.
        async with self.web.cleanup("old-feedback") as conn:
            cur = await conn.execute("DELETE FROM support_tickets")
            self.assertEqual(cur.rowcount, 0)

    async def test_deleting_an_account_deletes_its_tickets_and_only_its_own(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await self.add_row(ALICE.id, self.now, "alice's")
        await self.add_row(BOB, self.now, "bob's")
        await delete_person(self.web, ALICE.id, now=self.now)
        self.assertEqual([r["subject"] for r in await self.tickets()], ["bob's"])


class TicketTimes(SupportBase):
    """No two of a person's tickets share a sent time (#2214)."""

    async def test_sends_in_the_same_instant_each_get_their_own_second(self) -> None:
        ids = await asyncio.gather(
            *(
                support.add(
                    self.web, ALICE.id, "table", support.Ticket(f"s{n}", "Words"), now=self.now
                )
                for n in range(6)
            )
        )
        self.assertEqual(len(set(ids)), 6)
        times = sorted(t["created_at"] for t in await self.tickets())
        self.assertEqual(times, [self.now + n for n in range(6)])

    async def test_another_persons_tickets_are_not_affected(self) -> None:
        await sessions.sign_in(self.web, _bob(), [THURSDAY], now=self.now, days=30)
        await support.add(self.web, ALICE.id, "table", support.Ticket("a", "Words"), now=self.now)
        await support.add(self.web, BOB, "table", support.Ticket("b", "Words"), now=self.now)
        self.assertEqual({t["created_at"] for t in await self.tickets()}, {self.now})

    async def test_the_database_refuses_a_shared_time_even_without_the_stamp(self) -> None:
        await self.add_row(ALICE.id, self.now, "one")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            await self.add_row(ALICE.id, self.now, "two")


class TicketTimesMigration(DatabaseTest):
    """The migration moves tickets that already share a time apart, then adds the index."""

    async def test_planted_duplicates_are_moved_apart_by_the_later_id(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0155"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        planted = [  # (subject, user, created_at): three at one second, one already at the next
            ("a", 701, 1000),
            ("b", 701, 1000),
            ("c", 701, 1000),
            ("d", 701, 1001),
            ("e", 702, 1000),  # another person at the same second: untouched
        ]
        for subject, user, at in planted:
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO support_tickets (created_at, user_id, plan, subject, message)"
                    " VALUES (%s, %s, 'table', %s, 'Words')",
                    (at, user, subject),
                )
        await self.db.migrate()
        async with self.db.support_admin() as conn:
            cur = await conn.execute(
                "SELECT subject, user_id, created_at FROM support_tickets ORDER BY id"
            )
            got = {r["subject"]: r["created_at"] for r in await cur.fetchall()}
        # a keeps 1000; b and c step up, run into d at 1001 and push it too (d was not a
        # duplicate, but the ones below it ran into it): the order by id is kept, all four apart.
        self.assertEqual([got[k] for k in "abcd"], [1000, 1001, 1002, 1003])
        self.assertEqual(got["e"], 1000)  # another person at the same second: untouched
        async with self.db.unscoped() as conn:  # the opening is gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND tablename = 'support_tickets'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            async with self.db.user(701) as conn:
                await conn.execute(
                    "INSERT INTO support_tickets (created_at, user_id, plan, subject, message)"
                    " VALUES (1000, 701, 'table', 'again', 'Words')"
                )


class SupportAdmin(SupportBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(
                admin_emails=(ADMIN,),
                admin_password_hash=HASH,
                client_ip_header="CF-Connecting-IP",
            ),
            self.web,
            FakeDiscord(),
            admin_sessions=AdminSessions(clock=lambda: 0.0),
            admin_tries=FailedTries(clock=lambda: 0.0),
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self) -> str:
        signed = await self.client.post(
            "/admin/auth/password", json={"email": ADMIN, "password": PASSWORD}, headers=HEADERS
        )
        self.assertEqual(signed.status_code, 204)
        return cast(str, (await self.client.get("/admin/me")).json()["csrf"])

    async def set_status(self, csrf: str, ticket_id: int, status: object) -> httpx.Response:
        return await self.client.post(
            f"/admin/support/{ticket_id}/status",
            json={"status": status},
            headers={**HEADERS, "X-Admin-CSRF": csrf},
        )

    async def test_only_a_signed_in_admin_may_read_or_close(self) -> None:
        await self.add_row(ALICE.id, self.now)
        self.assertEqual((await self.client.get("/admin/support")).status_code, 401)
        self.assertEqual((await self.set_status("x", 1, "closed")).status_code, 401)
        await self.sign_in()
        self.assertEqual((await self.set_status("forged", 1, "closed")).status_code, 403)
        self.assertEqual((await self.tickets())[0]["status"], "new")

    async def test_the_admin_lists_newest_first_and_marks_answered_then_closed(self) -> None:
        await self.add_row(ALICE.id, self.now - 60, "older")
        await self.add_row(ALICE.id, self.now, "newer")
        csrf = await self.sign_in()
        listed = (await self.client.get("/admin/support")).json()["tickets"]
        self.assertEqual([t["subject"] for t in listed], ["newer", "older"])
        first = listed[0]
        self.assertEqual(first["discordId"], str(ALICE.id))
        self.assertRegex(first["dmbotId"], r"^[0-9A-Z]+$")
        self.assertEqual(
            (first["plan"], first["source"], first["status"], first["at"]),
            ("table", "website", "new", self.now),
        )
        self.assertEqual(first["email"], "belleros@example.com")
        self.assertEqual((await self.set_status(csrf, first["id"], "answered")).status_code, 204)
        self.assertEqual((await self.set_status(csrf, first["id"], "closed")).status_code, 204)
        after = (await self.client.get("/admin/support")).json()["tickets"]
        self.assertEqual([t["status"] for t in after], ["closed", "new"])

    async def make_three(self, csrf: str) -> None:
        """Two new, one answered and one closed, so each filter has its own tickets (#1375)."""
        for age, subject in (
            (300, "closed one"),
            (200, "answered one"),
            (100, "new one a"),
            (0, "new one b"),
        ):
            await self.add_row(ALICE.id, self.now - age, subject)
        by_subject = {t["subject"]: t["id"] for t in await self.tickets()}
        for subject, status in (("closed one", "closed"), ("answered one", "answered")):
            self.assertEqual(
                (await self.set_status(csrf, by_subject[subject], status)).status_code, 204
            )

    async def test_each_filter_shows_only_its_tickets_and_the_counts_cover_all(self) -> None:
        csrf = await self.sign_in()
        await self.make_three(csrf)
        expected = {
            "new": ["new one b", "new one a"],
            "answered": ["answered one"],
            "closed": ["closed one"],
            "all": ["new one b", "new one a", "answered one", "closed one"],
        }
        for status, subjects in expected.items():
            body = (await self.client.get("/admin/support", params={"status": status})).json()
            self.assertEqual([t["subject"] for t in body["tickets"]], subjects, status)
            # The counts are the same whichever filter is on: they label the buttons.
            self.assertEqual(
                body["counts"], {"new": 2, "answered": 1, "closed": 1, "escalated": 0}, status
            )
        # No filter is every ticket, as the page behaved before the filters.
        body = (await self.client.get("/admin/support")).json()
        self.assertEqual(len(body["tickets"]), 4)

    async def many(self, count: int, *, answered_every: int = 0) -> None:
        """`count` tickets, one second apart, ticket 0 the oldest; every `answered_every`th is
        answered, the rest new (invented subjects)."""
        for n in range(count):
            await self.add_row(ALICE.id, self.now - count + n, f"ticket {n:03d}")
        if answered_every:
            for t in await self.tickets():
                if int(t["subject"].split()[1]) % answered_every == 0:
                    await support.set_status(self.web, t["id"], "answered")

    async def test_tickets_come_twenty_a_page_newest_first_with_the_page_count(self) -> None:
        await self.many(45)
        await self.sign_in()
        first = (await self.client.get("/admin/support")).json()
        self.assertEqual((len(first["tickets"]), first["page"], first["pages"]), (20, 1, 3))
        self.assertEqual(first["tickets"][0]["subject"], "ticket 044")  # newest first
        second = (await self.client.get("/admin/support", params={"page": 2})).json()
        self.assertEqual((len(second["tickets"]), second["page"]), (20, 2))
        self.assertEqual(second["tickets"][0]["subject"], "ticket 024")
        last = (await self.client.get("/admin/support", params={"page": 3})).json()
        self.assertEqual(len(last["tickets"]), 5)  # the remainder
        self.assertEqual(last["tickets"][-1]["subject"], "ticket 000")
        subjects = [t["subject"] for p in (first, second, last) for t in p["tickets"]]
        self.assertEqual(len(subjects), len(set(subjects)))  # every ticket once
        # The counts cover every ticket, whichever page this is.
        self.assertEqual(last["counts"]["new"], 45)

    async def test_paging_goes_within_the_filtered_list(self) -> None:
        await self.many(45, answered_every=3)  # 15 answered, 30 new
        await self.sign_in()
        new = (await self.client.get("/admin/support", params={"status": "new"})).json()
        self.assertEqual((len(new["tickets"]), new["pages"]), (20, 2))
        answered = (await self.client.get("/admin/support", params={"status": "answered"})).json()
        self.assertEqual((len(answered["tickets"]), answered["pages"]), (15, 1))
        page_two = (
            await self.client.get("/admin/support", params={"status": "new", "page": 2})
        ).json()
        self.assertEqual(len(page_two["tickets"]), 10)
        self.assertEqual({t["status"] for t in page_two["tickets"]}, {"new"})
        # A page that exists unfiltered but not in this filter is refused.
        past = await self.client.get("/admin/support", params={"status": "answered", "page": 2})
        self.assertEqual((past.status_code, past.json()), (400, {"error": "bad_page"}))

    async def test_a_bad_page_is_refused_and_no_tickets_is_one_empty_page(self) -> None:
        await self.sign_in()
        empty = (await self.client.get("/admin/support")).json()
        self.assertEqual((empty["tickets"], empty["page"], empty["pages"]), ([], 1, 1))
        await self.many(21)  # two pages
        for bad in (
            "0",
            "-1",
            "3",
            "abc",
            "",
            "1.5",
            "+2",
            "%202",
            "2%0A",
            "%D9%A2",
            "99999999999",
        ):
            answer = await self.client.get(f"/admin/support?page={bad}")
            self.assertEqual((answer.status_code, answer.json()), (400, {"error": "bad_page"}), bad)
        self.assertEqual((await self.client.get("/admin/support?page=002")).json()["page"], 2)

    async def test_needs_the_team_pages_longest_waiting_first(self) -> None:
        """#1485 with pages: the ticket handed over longest ago is first, on page 1, whatever its
        age. Handed over in the order they were sent (so longest-waiting is the OLDEST, the
        opposite of newest-first), except ticket 010, handed over before all the others."""
        await self.many(25)
        by_subject = {t["subject"]: t["id"] for t in await self.tickets()}
        for n in range(25):
            handed = self.now - 5000 if n == 10 else self.now - 1000 + n
            await support.escalate(self.web, by_subject[f"ticket {n:03d}"], "asked", handed)
        await self.sign_in()
        first = (await self.client.get("/admin/support", params={"status": "escalated"})).json()
        self.assertEqual((len(first["tickets"]), first["pages"]), (20, 2))
        expected = [10, *[n for n in range(25) if n != 10]]  # 010, then 000, 001, ... in order
        self.assertEqual(
            [t["subject"] for t in first["tickets"]], [f"ticket {n:03d}" for n in expected[:20]]
        )
        second = (
            await self.client.get("/admin/support", params={"status": "escalated", "page": 2})
        ).json()
        self.assertEqual(
            [t["subject"] for t in second["tickets"]], [f"ticket {n:03d}" for n in expected[20:]]
        )
        # And every other list is still newest first: the order is the escalated list's alone.
        newest = (await self.client.get("/admin/support", params={"status": "all"})).json()
        self.assertEqual(newest["tickets"][0]["subject"], "ticket 024")

    async def test_tickets_sent_at_the_same_second_keep_one_order_across_a_page_edge(self) -> None:
        for n in range(25):
            await self.add_row(ALICE.id, self.now, f"same {n:03d}")  # one second, 25 tickets
        await self.sign_in()
        one = (await self.client.get("/admin/support")).json()["tickets"]
        two = (await self.client.get("/admin/support", params={"page": 2})).json()["tickets"]
        subjects = [t["subject"] for t in one + two]
        self.assertEqual(len(set(subjects)), 25)  # none repeated or lost across the edge
        self.assertEqual(subjects, sorted(subjects, reverse=True))  # newest id first

    async def test_exactly_twenty_tickets_is_one_page(self) -> None:
        await self.many(20)
        await self.sign_in()
        body = (await self.client.get("/admin/support")).json()
        self.assertEqual((len(body["tickets"]), body["pages"]), (20, 1))
        self.assertEqual((await self.client.get("/admin/support?page=2")).status_code, 400)

    async def test_the_counts_follow_a_change_and_show_zero_for_none(self) -> None:
        csrf = await self.sign_in()
        empty = (await self.client.get("/admin/support", params={"status": "new"})).json()
        self.assertEqual(
            (empty["tickets"], empty["counts"]),
            ([], {"new": 0, "answered": 0, "closed": 0, "escalated": 0}),
        )
        await self.make_three(csrf)
        (new_b,) = [t for t in await self.tickets() if t["subject"] == "new one b"]
        await self.set_status(csrf, new_b["id"], "answered")
        body = (await self.client.get("/admin/support", params={"status": "answered"})).json()
        self.assertEqual(body["counts"], {"new": 1, "answered": 2, "closed": 1, "escalated": 0})
        self.assertEqual([t["subject"] for t in body["tickets"]], ["new one b", "answered one"])

    async def test_this_week_counts_the_last_seven_days_and_nothing_older(self) -> None:
        """#1480: the numbers behind "This week: ..."; made-up tickets, and never a word of one."""
        for age, subject in (
            (600, "answered by the helper"),
            (500, "answered by the team"),
            (400, "still waiting"),
            (300, "asked for a person"),
            (200, "unhappy"),
            (8 * DAY, "too old to count"),
        ):
            await self.add_row(ALICE.id, self.now - age, subject)
        by = {t["subject"]: t["id"] for t in await self.tickets()}
        await support.add_reply(self.web, by["answered by the helper"], "ai", "A.", now=self.now)
        await support.add_reply(self.web, by["answered by the team"], "ai", "B.", now=self.now)
        await support.add_reply(self.web, by["answered by the team"], "customer", "No.", now=1)
        await support.add_reply(self.web, by["answered by the team"], "person", "C.", now=2)
        await support.escalate(self.web, by["asked for a person"], "asked", self.now)
        await support.escalate(self.web, by["unhappy"], "unhappy", self.now)
        await support.add_reply(self.web, by["too old to count"], "ai", "D.", now=self.now)
        await self.sign_in()
        week = (await self.client.get("/admin/support")).json()["week"]
        self.assertEqual(
            week,
            {
                "questions": 5,
                "helper": 1,
                "team": 1,
                "waiting": 3,
                "reasons": {"unhappy": 1, "asked": 1, "limit": 0, "no-email": 0, "helper-down": 0},
            },
        )

    async def test_this_week_counts_every_hand_over_even_after_a_person_answered(self) -> None:
        """#1487: the reason stays on the ticket for good, so the week's count is complete."""
        for age, subject in ((300, "handed over, then answered"), (200, "never handed over")):
            await self.add_row(ALICE.id, self.now - age, subject)
        by = {t["subject"]: t["id"] for t in await self.tickets()}
        await support.escalate(self.web, by["handed over, then answered"], "limit", self.now)
        await support.add_reply(
            self.web, by["handed over, then answered"], "person", "Hi.", now=self.now
        )
        await support.set_status(self.web, by["handed over, then answered"], "answered")
        await support.add_reply(self.web, by["never handed over"], "ai", "Try this.", now=self.now)
        await self.sign_in()
        got = (await self.client.get("/admin/support")).json()
        week = got["week"]
        self.assertEqual(
            week["reasons"], {"unhappy": 0, "asked": 0, "limit": 1, "no-email": 0, "helper-down": 0}
        )
        listed = {t["subject"]: t for t in got["tickets"]}
        # The list still says why a ticket is waiting *now* (nothing: it was answered) ...
        self.assertIsNone(listed["handed over, then answered"]["reason"])
        # ... and when it was handed over; a ticket never handed over has no time.
        self.assertEqual(listed["handed over, then answered"]["handedOverAt"], self.now)
        self.assertIsNone(listed["never handed over"]["handedOverAt"])

    async def test_a_second_hand_over_keeps_the_latest_reason_and_time_and_counts_once(
        self,
    ) -> None:
        await self.add_row(ALICE.id, self.now - 500, "handed over twice")
        (ticket,) = [t["id"] for t in await self.tickets()]
        await support.escalate(self.web, ticket, "asked", self.now - 100)
        await support.add_reply(self.web, ticket, "person", "Hi.", now=self.now - 90)
        await support.set_status(self.web, ticket, "answered")
        await support.add_reply(self.web, ticket, "customer", "Still stuck.", now=self.now - 80)
        await support.set_status(self.web, ticket, "new")
        self.assertTrue(await support.escalate(self.web, ticket, "unhappy", self.now))
        await self.sign_in()
        got = (await self.client.get("/admin/support")).json()
        self.assertEqual(
            got["week"]["reasons"],
            {"unhappy": 1, "asked": 0, "limit": 0, "no-email": 0, "helper-down": 0},
        )
        self.assertEqual(got["tickets"][0]["handedOverAt"], self.now)

    async def test_this_week_is_all_zeros_with_no_tickets_and_holds_no_words(self) -> None:
        await self.sign_in()
        week = (await self.client.get("/admin/support")).json()["week"]
        self.assertEqual(
            (week["questions"], week["helper"], week["team"], week["waiting"]), (0, 0, 0, 0)
        )
        await self.add_row(ALICE.id, self.now, "A subject nobody should see in the numbers")
        shown = (await self.client.get("/admin/support")).json()["week"]
        self.assertNotIn("subject", repr(shown))
        self.assertEqual(shown["questions"], 1)

    async def test_the_counts_cover_every_ticket_even_past_the_list_limit(self) -> None:
        for n in range(5):
            await self.add_row(ALICE.id, self.now - n, f"t{n}")
        listed = (await support.page(self.web, status=None, number=1, size=2))[0]
        self.assertEqual(len(listed), 2)  # the list is cut short...
        self.assertEqual(
            await support.counts(self.web), {"new": 5, "answered": 0, "closed": 0, "escalated": 0}
        )

    async def test_a_bad_filter_is_refused_and_a_non_admin_gets_nothing(self) -> None:
        await self.add_row(ALICE.id, self.now)
        # Signed out: nothing, for every filter, and no counts to learn how many people write.
        for status in ("new", "all", "weird"):
            answer = await self.client.get("/admin/support", params={"status": status})
            self.assertEqual(answer.status_code, 401, status)
            self.assertNotIn("counts", answer.text)
        await self.sign_in()
        for status in ("weird", "NEW", "", "new; DROP TABLE support_tickets"):
            answer = await self.client.get("/admin/support", params={"status": status})
            self.assertEqual(answer.status_code, 400, status)
        self.assertEqual(len(await self.tickets()), 1)

    async def test_a_ticket_number_too_big_for_the_database_is_not_found_not_a_crash(self) -> None:
        csrf = await self.sign_in()
        for number in ("99999999999999999999", "0", "-3"):
            answer = await self.client.post(
                f"/admin/support/{number}/status",
                json={"status": "closed"},
                headers={**HEADERS, "X-Admin-CSRF": csrf},
            )
            self.assertEqual(answer.status_code, 422, number)

    async def test_bad_status_and_unknown_ticket_are_refused_in_codes(self) -> None:
        csrf = await self.sign_in()
        await self.add_row(ALICE.id, self.now)
        (ticket,) = await self.tickets()
        bad = await self.set_status(csrf, ticket["id"], "deleted")
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_status"}))
        gone = await self.set_status(csrf, 99999, "closed")
        self.assertEqual((gone.status_code, gone.json()), (404, {"error": "no_ticket"}))

    async def test_reading_and_closing_log_no_words(self) -> None:
        await self.add_row(ALICE.id, self.now, "A secret subject")
        csrf = await self.sign_in()
        (ticket,) = await self.tickets()
        with self.assertLogs(level=logging.DEBUG) as seen:
            await self.client.get("/admin/support")
            await self.set_status(csrf, ticket["id"], "closed")
        logged = "\n".join(r.getMessage() for r in seen.records)
        self.assertIn(f"support ticket {ticket['id']} to closed", logged)
        self.assertNotIn("secret", logged)
        self.assertNotIn(ADMIN, logged)


class DoorBoundary(unittest.TestCase):
    """The website's role can open the admin door itself, so the only protection is which code
    does. This keeps that list short: a new caller fails here and must be looked at."""

    ROOT = Path(__file__).resolve().parents[1] / "src" / "dmbot"

    def callers(self, needle: str) -> set[str]:
        return {
            str(f.relative_to(self.ROOT))
            for f in self.ROOT.rglob("*.py")
            if needle in f.read_text(encoding="utf-8")
        }

    def test_only_support_opens_the_door_and_only_the_admin_routes_use_support(self) -> None:
        self.assertEqual(self.callers("db.support_admin("), {"web/support.py"})
        self.assertEqual(self.callers("support.page("), {"web/admin_api.py"})
        self.assertEqual(self.callers("support.set_status("), {"web/admin_api.py"})
        self.assertEqual(self.callers("support.thread("), {"web/admin_api.py", "web/support_ai.py"})
        # The reply address is read by this one query, and the SQL function only by it.
        self.assertEqual(self.callers("dmbot_support_email("), {"web/support.py", "schema.py"})
        # The setting's own name is set nowhere else (a hand-made set_config would skip the door).
        self.assertEqual(self.callers("dmbot.support_admin"), {"schema.py"})


class TeamAddress(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        "DATABASE_URL": "postgresql://x",
        "DISCORD_CLIENT_ID": "1",
        "DISCORD_CLIENT_SECRET": "s",
        "WEB_SECRET_KEY": "k" * 32,
        "WEB_SITE_URL": "http://localhost:4321",
        "WEB_API_URL": "http://localhost:8080",
    }

    def test_it_is_one_plain_address_or_empty(self) -> None:
        self.assertEqual(load_web_settings(self.ENV).support_team_email, "")
        good = {**self.ENV, "SUPPORT_TEAM_EMAIL": " team@example.org "}
        self.assertEqual(load_web_settings(good).support_team_email, "team@example.org")
        for bad in ("team", "a@example.org, b@example.org", "Team <t@example.org>", "t@example"):
            with self.assertRaises(ConfigError, msg=bad):
                load_web_settings({**self.ENV, "SUPPORT_TEAM_EMAIL": bad})


class NotifyTeam(unittest.IsolatedAsyncioTestCase):
    async def test_it_sends_one_plain_message_and_never_raises(self) -> None:
        outbox = Outbox()
        mailer = Mailer(None, SMTP, transport=outbox, site_url=SITE, link_secret=LINK_SECRET)  # type: ignore[arg-type]
        self.assertTrue(await mailer.notify_team(TEAM, "A ticket waits", "Open the admin page."))
        self.assertFalse(await mailer.notify_team("not an address", "s", "b"))
        self.assertFalse(await mailer.notify_team("a@example.org, b@example.org", "s", "b"))
        outbox.fail = True
        self.assertFalse(await mailer.notify_team(TEAM, "s", "b"))
        self.assertEqual(len(outbox.sent), 1)
        self.assertEqual(str(outbox.sent[0]["To"]), TEAM)
        self.assertNotIn("List-Unsubscribe", outbox.sent[0])


class Reading(unittest.TestCase):
    def test_the_form_is_checked(self) -> None:
        self.assertEqual(
            support.read_ticket({"subject": " Hi\n there ", "message": " Words\r\nmore "}),
            support.Ticket("Hi there", "Words\nmore"),
        )
        bad: list[object] = [None, [], "x", {}, {"subject": "s"}, {"message": "m"}]
        for raw in bad:
            self.assertIsInstance(support.read_ticket(raw), str, raw)


if __name__ == "__main__":
    unittest.main()
