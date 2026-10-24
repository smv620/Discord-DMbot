"""The unsubscribe link in offer emails (#1229), as the website's own database role: a good
signed link records the choice, anything else records nothing and reveals nothing."""

from __future__ import annotations

from dataclasses import replace

import httpx

from dmbot import account_id, link_tokens, mail
from dmbot.db import Database
from dmbot.web.app import create_app
from dmbot.web.limits import LIMITS, RateLimit
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import SITE, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

SECRET = b"u" * 40
HEADERS = {"X-DMbot-Request": "1"}
USER = 1_234_567_890_123_456_789
ID = account_id.to_dmbot_id(USER)


class UnsubscribeRoute(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        limits = {
            name: RateLimit(group.seconds, clock=lambda: float(self.now), count=group.count)
            for name, group in LIMITS.items()
        }
        self.app = create_app(
            replace(settings(), link_token_secret=SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=limits,
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def post(self, content: bytes | dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        ) as client:
            if isinstance(content, dict):
                return await client.post("/email/unsubscribe", json=content, headers=HEADERS)
            return await client.post("/email/unsubscribe", content=content, headers=HEADERS)

    async def test_a_good_link_records_the_choice_and_a_repeat_is_the_same(self) -> None:
        token = link_tokens.make_unsubscribe_token(ID, SECRET)
        first = await self.post({"uid": ID, "t": token})
        again = await self.post({"uid": ID, "t": token})
        self.assertEqual((first.status_code, first.json()), (200, {"ok": True}))
        self.assertEqual((again.status_code, again.json()), (200, {"ok": True}))
        made = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: self.now)
        self.assertTrue(await made.is_unsubscribed(USER))

    async def test_a_bad_link_records_nothing_and_looks_the_same_whatever_is_wrong(self) -> None:
        good = link_tokens.make_unsubscribe_token(ID, SECRET)
        other = account_id.to_dmbot_id(42)
        bad = [
            {"uid": ID, "t": good[:-1] + ("0" if good[-1] != "0" else "1")},
            {"uid": other, "t": good},
            {"uid": "not an id", "t": good},
            {"uid": ID},
            {"t": good},
        ]
        seen = set()
        for body in bad:
            out = await self.post(body)
            seen.add((out.status_code, out.json()["error"]))
        self.assertEqual(seen, {(400, "bad_link")})
        for raw in (b"[1, 2]", b"nonsense", b'"text"', b"null"):
            self.assertEqual((await self.post(raw)).status_code, 400)
        made = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: self.now)
        self.assertFalse(await made.is_unsubscribed(USER))
        self.assertFalse(await made.is_unsubscribed(42))

    async def test_a_huge_body_is_refused(self) -> None:
        out = await self.post(b"x" * 5000)
        self.assertEqual(out.status_code, 413)


class WebRoleAndEmailTable(UnsubscribeRoute):
    """The website's role sends its own account emails through the door and can touch no row
    of the email history."""

    async def test_the_website_can_use_the_door_but_not_read_or_write_the_table(self) -> None:
        import psycopg

        async with self.web.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_claim_email(%s, %s, false, 400, 300) AS status", (USER, "sign-in:1")
            )
            row = await cur.fetchone()
            self.assertEqual((row or {})["status"], "sent")
            cur = await conn.execute(
                "SELECT dmbot_claim_email(%s, %s, false, 400, 300) AS status", (USER, "sign-in:1")
            )
            row = await cur.fetchone()
            self.assertEqual((row or {})["status"], "duplicate")  # a kind goes once
        for statement in (
            "SELECT * FROM sent_emails",
            "SELECT dmbot_release_email(1, 'x')",  # the bot's door, not the website's
            "INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (1, 'x', 0)",
            "DELETE FROM sent_emails",
            "SELECT * FROM email_unsubscribed",
        ):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=statement):
                async with self.web.unscoped() as conn:
                    await conn.execute(statement)


class MailerOnTheWebsiteRole(UnsubscribeRoute):
    async def test_a_sign_in_email_sent_through_the_mailer_on_the_websites_role(self) -> None:
        from dmbot.mail import Mailer, load_mail_settings
        from tests.test_mail import ENV, Fake

        out = Fake()
        made = Mailer(self.web, load_mail_settings(ENV), transport=out, clock=lambda: 1_800_000_000)
        result = await made.send(
            user_id=USER, kind="sign-in:9", to="p@example.com", subject="Hi", body="Hello"
        )
        self.assertEqual(result, "sent")
        self.assertEqual(len(out.sent), 1)
