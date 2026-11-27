"""What is left of the website's move-code side (#1246) after the codes were retired (#2533 item 5):
the route answers with where to move now and sends nothing, the website's role still cannot touch
the move tables, and the hourly sweep and an account's deletion still clear old asks."""

from __future__ import annotations

from email.message import EmailMessage

import httpx

from dmbot import account_move
from dmbot.db import Database
from dmbot.mail import Mailer
from dmbot.web import sessions
from dmbot.web.accounts import delete_person
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.moving import forget_old_asks
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

    async def ask(self, source: str = "a" * 64) -> str:
        """One ask at the database door, as the old website code made it (the door stays for
        accounts already moved): the sweep and the deletion still have its rows to clear."""
        async with self.web.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_move_code(%s, %s, %s, %s, %s) AS outcome",
                (ALICE.id, source, self.now, account_move.hash_code("AAAA2222"), self.now + 1800),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["outcome"])

    async def asks(self) -> int:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM move_code_asks")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

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

    async def test_the_sweep_removes_old_asks(self) -> None:
        self.assertEqual(await self.ask(), "ok")
        self.assertEqual(await forget_old_asks(self.web, now=self.now + 60), 0)
        self.assertEqual(await self.asks(), 1)
        self.assertEqual(await forget_old_asks(self.web, now=self.now + 3601), 1)
        self.assertEqual(await self.asks(), 0)

    async def test_deleting_the_account_leaves_no_asks(self) -> None:
        await self.ask()
        await delete_person(self.web, ALICE.id, now=self.now)
        self.assertEqual(await self.asks(), 0)


class MoveCodeRoute(WebMoveCode):
    async def test_the_route_sends_no_email_and_says_where_to_move(self) -> None:
        app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            mailer=self.mailer,
            clock=lambda: self.now,
        )
        token = await sessions.sign_in_by_email(self.web, ALICE.id, now=self.now, days=30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        ) as client:
            client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
            answer = await client.post("/me/move-code", headers=HEADERS)
        self.assertEqual(
            (answer.status_code, answer.json()["error"]), (410, "moved_to_account_page")
        )
        self.assertIn(
            "add the new Discord account, then remove the old one", answer.json()["message"]
        )
        self.assertEqual(self.outbox.sent, [])
        self.assertEqual(await self.asks(), 0)
        self.assertEqual(await self.stored(), [])
