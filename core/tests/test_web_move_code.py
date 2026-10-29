"""The website's move-code door (#1246), run as the website's own database role: the code is made
and counted by one database door, mailed to the payment email only, and shown nowhere else."""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from email.message import EmailMessage

import httpx

from dmbot import account_move
from dmbot.account_move import AccountMoves, MoveError
from dmbot.db import Database
from dmbot.mail import Mailer
from dmbot.web import sessions
from dmbot.web.accounts import delete_person
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.moving import EmailedMoveCodes, forget_old_asks
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_email_signin import LINK_SECRET, SMTP
from tests.test_web_role import ensure_web_role, web_url

HEADERS = {"X-DMbot-Request": "1"}
PAYMENT_EMAIL = "pays@example.org"  # not Alice's Discord email: a code goes to this one only
NEW_ACCOUNT = 7007


class Outbox:
    """The mail server: keeps each message whole."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = False

    async def __call__(self, message: EmailMessage) -> None:
        if self.fail:
            raise RuntimeError("the mail server is down")
        self.sent.append(message)

    def code(self) -> str:
        body = str(self.sent[-1].get_content())
        found = body.split("move code: ")[1][:9]
        return found.replace("-", "")


class WebMoveCode(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.outbox = Outbox()
        self.mailer = Mailer(
            self.web,
            SMTP,
            transport=self.outbox,
            site_url=SITE,
            link_secret=LINK_SECRET,
            clock=lambda: float(self.now),
        )
        self.codes = EmailedMoveCodes(self.web, self.mailer, clock=lambda: float(self.now))
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30)
        await self.give_plan(ALICE, "table", "active", "sub_1")
        async with self.web.user(ALICE.id) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = %s, payment_email_at = 1 WHERE user_id = %s",
                (PAYMENT_EMAIL, ALICE.id),
            )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def give_plan(
        self, user: DiscordUser, plan: str, status: str, subscription: str | None
    ) -> None:
        async with self.db.plan_writer(user.id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (user.id,))
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, plan_changed_at,"
                " provider, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%s, %s, %s, 18, 0, 1, %s, %s, %s, 0, 'fake', %s, 0, 0)",
                (
                    user.id,
                    plan,
                    status,
                    self.now - 100,
                    self.now + 86400,
                    self.now + 86400 if status == "grace" else None,
                    subscription,
                ),
            )

    async def stored(self) -> list[dict[str, object]]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT user_id, expires_at FROM move_codes")
            return list(await cur.fetchall())

    async def test_the_code_goes_to_the_payment_email_only_and_works_for_the_bot(self) -> None:
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "sent")
        self.assertEqual([str(m["To"]) for m in self.outbox.sent], [PAYMENT_EMAIL])
        code = self.outbox.code()
        # The bot's own side takes the code the website made: the two halves agree.
        bot = AccountMoves(self.db, clock=lambda: self.now)
        self.assertEqual(await bot.find(NEW_ACCOUNT, code), ALICE.id)
        (row,) = await self.stored()
        self.assertEqual(row["expires_at"], self.now + account_move.CODE_LIFETIME_S)

    async def test_the_code_is_in_the_email_and_nowhere_else(self) -> None:
        await self.codes.send_code(ALICE.id, "a" * 64)
        code = self.outbox.code()
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT kind FROM sent_emails")
            kinds = [r["kind"] for r in await cur.fetchall()]
        (kind,) = kinds
        self.assertRegex(kind, r"^move-code:\d+:[0-9a-f]{8}$")
        self.assertNotIn(code, kind)
        self.assertNotIn(account_move.hash_code(code)[:8], kind)  # nothing derived from the code
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT * FROM move_codes")
            self.assertNotIn(code, repr(await cur.fetchall()))  # only a hash is stored

    async def test_no_payment_email_sends_nothing_and_uses_no_ask(self) -> None:
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("UPDATE web_users SET payment_email = NULL")
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "no_email")
        self.assertEqual((self.outbox.sent, await self.stored()), ([], []))

    async def test_only_an_active_paid_plan_gets_a_code(self) -> None:
        for plan, status, sub in (
            ("try-it", "active", None),
            ("table", "grace", "sub_1"),
            ("table", "active", None),  # a grant, not a payment
        ):
            await self.give_plan(ALICE, plan, status, sub)
            self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "not_paid", plan)
        self.assertEqual((self.outbox.sent, await self.stored()), ([], []))

    async def test_a_move_in_the_last_30_days_blocks_a_new_code(self) -> None:
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT dmbot_id FROM web_users WHERE user_id = %s", (ALICE.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO account_moves (old_user_id, new_user_id, dmbot_id, moved_at)"
                " VALUES (%s, %s, %s, %s)",
                (9009, ALICE.id, row["dmbot_id"], self.now - 86400),
            )
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "too_soon")
        self.now += 31 * 86400
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "sent")

    async def test_three_codes_an_hour_for_an_account_kept_in_the_database(self) -> None:
        results = [await self.codes.send_code(ALICE.id, f"{n}" * 64) for n in range(4)]
        self.assertEqual(results, ["sent", "sent", "sent", "slow_down"])
        self.assertEqual(len(self.outbox.sent), 3)
        # A new process with a new limiter in memory is no way round it: the count is in the
        # database.
        fresh = EmailedMoveCodes(self.web, self.mailer, clock=lambda: float(self.now))
        self.assertEqual(await fresh.send_code(ALICE.id, "9" * 64), "slow_down")
        self.now += 3601
        self.assertEqual(await self.codes.send_code(ALICE.id, "5" * 64), "sent")

    async def test_five_codes_an_hour_for_a_connection_across_accounts(self) -> None:
        others = []
        for n in range(6):
            user = DiscordUser(id=3000 + n, name=f"P{n}", email=f"p{n}@example.com")
            await sessions.sign_in(self.web, user, [THURSDAY], now=self.now, days=30)
            await self.give_plan(user, "table", "active", f"sub_{n}")
            async with self.web.user(user.id) as conn:
                await conn.execute(
                    "UPDATE web_users SET payment_email = %s, payment_email_at = 1",
                    (f"pay{n}@example.org",),
                )
            others.append(user)
        results = [await self.codes.send_code(u.id, "c" * 64) for u in others]
        self.assertEqual(results, ["sent"] * 5 + ["slow_down"])
        self.assertEqual(await self.codes.send_code(ALICE.id, "d" * 64), "sent")  # another place

    async def test_a_new_code_replaces_the_old_one(self) -> None:
        await self.codes.send_code(ALICE.id, "a" * 64)
        first = self.outbox.code()
        await self.codes.send_code(ALICE.id, "a" * 64)
        second = self.outbox.code()
        self.assertNotEqual(first, second)
        bot = AccountMoves(self.db, clock=lambda: self.now)
        with self.assertRaises(MoveError):
            await bot.find(NEW_ACCOUNT, first)
        self.assertEqual(await bot.find(NEW_ACCOUNT, second), ALICE.id)

    async def test_a_failed_email_leaves_no_code_waiting(self) -> None:
        self.outbox.fail = True
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "failed")
        self.assertEqual(await self.stored(), [])

    async def test_the_website_role_cannot_read_or_write_the_tables_itself(self) -> None:
        for statement in (
            "SELECT * FROM move_codes",
            "SELECT * FROM account_moves",
            "SELECT * FROM move_code_asks",
            "INSERT INTO move_codes (user_id, code_hash, created_at, expires_at)"
            " VALUES (1, repeat('a', 64), 0, 0)",
        ):
            with self.assertRaises(Exception, msg=statement):  # noqa: B017 (permission denied)
                async with self.web.unscoped() as conn:
                    await conn.execute(statement)

    async def test_the_limits_in_the_door_match_the_bots(self) -> None:
        # The door's numbers are written in SQL; the bot's are Python constants. Same rules.
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT prosrc FROM pg_proc WHERE proname = 'dmbot_web_move_code'"
                " AND pronamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        assert row is not None
        body = " ".join(str(row["prosrc"]).split())
        self.assertIn(f">= {account_move.ASKS} ", body)
        self.assertIn(f"now - {account_move.ASK_WINDOW_S}", body)
        self.assertIn(f"now - {account_move.MOVE_GAP_S}", body)
        self.assertIn(f"now + {account_move.CODE_LIFETIME_S}", body)

    async def test_asks_at_once_from_one_connection_still_stop_at_five(self) -> None:
        users = []
        for n in range(8):
            user = DiscordUser(id=4000 + n, name=f"Q{n}", email=f"q{n}@example.com")
            await sessions.sign_in(self.web, user, [THURSDAY], now=self.now, days=30)
            await self.give_plan(user, "table", "active", f"sub_q{n}")
            async with self.web.user(user.id) as conn:
                await conn.execute(
                    "UPDATE web_users SET payment_email = %s, payment_email_at = 1",
                    (f"payq{n}@example.org",),
                )
            users.append(user)
        results = await asyncio.gather(*(self.codes.send_code(u.id, "e" * 64) for u in users))
        self.assertEqual(results.count("sent"), 5)
        self.assertEqual(results.count("slow_down"), 3)

    async def asks(self) -> int:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM move_code_asks")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_failed_emails_do_not_use_up_the_asks(self) -> None:
        self.outbox.fail = True
        for _ in range(3):
            self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "failed")
        self.assertEqual(await self.asks(), 0)
        self.outbox.fail = False
        self.assertEqual(await self.codes.send_code(ALICE.id, "a" * 64), "sent")

    async def test_the_sweep_removes_old_asks(self) -> None:
        await self.codes.send_code(ALICE.id, "a" * 64)
        self.assertEqual(await forget_old_asks(self.web, now=self.now + 60), 0)
        self.assertEqual(await self.asks(), 1)
        self.assertEqual(await forget_old_asks(self.web, now=self.now + 3601), 1)
        self.assertEqual(await self.asks(), 0)

    async def test_deleting_the_account_leaves_no_asks(self) -> None:
        await self.codes.send_code(ALICE.id, "a" * 64)
        await delete_person(self.web, ALICE.id, now=self.now)
        self.assertEqual(await self.asks(), 0)

    async def test_a_failed_mail_cancels_only_its_own_code(self) -> None:
        # Ask A makes code A; before A's mail fails, ask B replaces it and is mailed. Cancelling
        # A must leave B working.
        await self.codes.send_code(ALICE.id, "a" * 64)
        second = self.outbox.code()
        stale = account_move.hash_code("AAAA2222")
        await self.codes._cancel(ALICE.id, stale, self.now)
        bot = AccountMoves(self.db, clock=lambda: self.now)
        self.assertEqual(await bot.find(NEW_ACCOUNT, second), ALICE.id)


class MoveCodeRoute(WebMoveCode):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            mailer=self.mailer,
            move_codes=self.codes,
            clock=lambda: self.now,
        )

    async def signed_in(self) -> httpx.AsyncClient:
        token = await sessions.sign_in_by_email(self.web, ALICE.id, now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        return client

    async def test_the_route_emails_the_code_and_never_shows_it(self) -> None:
        async with await self.signed_in() as client:
            answer = await client.post("/me/move-code", headers=HEADERS)
        self.assertEqual(answer.status_code, 202)
        code = self.outbox.code()
        self.assertNotIn(code, answer.text)
        self.assertNotIn(PAYMENT_EMAIL, answer.text)

    async def test_the_route_says_why_in_short_words(self) -> None:
        async with await self.signed_in() as client:
            async with self.web.user(ALICE.id) as conn:
                await conn.execute("UPDATE web_users SET payment_email = NULL")
            none = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual((none.status_code, none.json()), (409, {"error": "no_email"}))
            self.outbox.fail = True
            async with self.web.user(ALICE.id) as conn:
                await conn.execute("UPDATE web_users SET payment_email = %s", (PAYMENT_EMAIL,))
            failed = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual((failed.status_code, failed.json()), (502, {"error": "email_failed"}))

    async def test_the_route_maps_the_other_refusals(self) -> None:
        async with await self.signed_in() as client:
            await self.give_plan(ALICE, "table", "grace", "sub_1")
            grace = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual((grace.status_code, grace.json()), (403, {"error": "not_paid"}))
            await self.give_plan(ALICE, "table", "active", "sub_1")
            for _ in range(3):
                await self.codes.send_code(ALICE.id, "f" * 64)
            slow = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual((slow.status_code, slow.json()), (429, {"error": "slow_down"}))

    async def test_the_connection_is_stored_as_a_keyed_hash_not_a_plain_one(self) -> None:
        async with await self.signed_in() as client:
            await client.post("/me/move-code", headers=HEADERS)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT source FROM move_code_asks")
            (row,) = await cur.fetchall()
        # httpx's test connection has no real address; whatever it is, a plain sha256 of it
        # (which anyone could compute for every IPv4 address) is not what is stored.
        plain = hashlib.sha256(b"move-code:unknown").hexdigest()
        self.assertEqual(len(row["source"]), 64)
        self.assertNotEqual(row["source"], plain)

    async def test_no_code_in_the_logs(self) -> None:
        async with await self.signed_in() as client:
            with self.assertLogs("dmbot", level="DEBUG") as logs:
                await client.post("/me/move-code", headers=HEADERS)
        self.assertNotIn(self.outbox.code(), "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
