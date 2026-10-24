"""Sign out everywhere (#1163), as the website's own database role: every sign-in of the
person goes, this one too, and nobody else's is touched."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot.db import Database
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.limits import LIMITS, RateLimit
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
HEADERS = {"X-DMbot-Request": "1"}


class SignOutEverywhere(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.discord = FakeDiscord()
        self.limits = {
            name: RateLimit(group.seconds, clock=lambda: float(self.now), count=group.count)
            for name, group in LIMITS.items()
        }
        self.app = create_app(
            settings(),
            self.web,
            self.discord,
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=self.limits,
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def browser(self, user: DiscordUser) -> httpx.AsyncClient:
        """A browser signed in as this person (its own cookie)."""
        self.discord.user_info = user
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        start = await client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await client.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
        self.assertEqual((await client.get("/me")).status_code, 200)
        return client

    async def test_every_sign_in_of_hers_goes_and_his_stays(self) -> None:
        phone = await self.browser(ALICE)
        laptop = await self.browser(ALICE)
        bob = await self.browser(BOB)
        try:
            ended = await phone.post("/me/sign-out-everywhere", headers=HEADERS)
            self.assertEqual(ended.status_code, 204)
            self.assertEqual((await phone.get("/me")).status_code, 401)  # this one too
            self.assertEqual((await laptop.get("/me")).status_code, 401)  # and the other
            self.assertEqual((await bob.get("/me")).status_code, 200)  # not his
        finally:
            for client in (phone, laptop, bob):
                await client.aclose()

    async def test_the_database_holds_the_role_to_her_own_sessions(self) -> None:
        # Called with Alice's id, only her rows can go, whatever the code asks.
        a = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30)
        b = await sessions.sign_in(self.web, BOB, [THURSDAY], now=self.now, days=30)
        self.assertEqual(await sessions.sign_out_everywhere(self.web, ALICE.id), 1)
        self.assertIsNone(await sessions.find(self.web, a, now=self.now))
        self.assertIsNotNone(await sessions.find(self.web, b, now=self.now))
        self.assertEqual(await sessions.sign_out_everywhere(self.web, ALICE.id), 0)

    async def test_it_needs_a_sign_in_and_the_site_header(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        ) as stranger:
            out = await stranger.post("/me/sign-out-everywhere", headers=HEADERS)
        self.assertEqual(out.status_code, 401)
        phone = await self.browser(ALICE)
        try:
            plain = await phone.post("/me/sign-out-everywhere")  # no site header
            self.assertEqual(plain.status_code, 403)
            self.assertEqual((await phone.get("/me")).status_code, 200)
        finally:
            await phone.aclose()

    async def test_it_is_limited_to_a_few_a_minute(self) -> None:
        phone = await self.browser(ALICE)
        try:
            tries = LIMITS["sign-out-everywhere"].count + 1
            answers = [
                await phone.post("/me/sign-out-everywhere", headers=HEADERS) for _ in range(tries)
            ]
            codes = [a.status_code for a in answers]
            # The first call ends the sign-in (and the cookie goes), the next ones are 401, and
            # the one over the limit is told to wait.
            self.assertEqual(codes[0], 204)
            self.assertEqual(codes[1:-1], [401] * (tries - 2))
            self.assertEqual(codes[-1], 429)
            self.assertIn("retry-after", answers[-1].headers)
        finally:
            await phone.aclose()

    async def test_a_raw_delete_for_someone_else_removes_nothing(self) -> None:
        # The database, not the code, holds the role: as Alice, a delete aimed at Bob's rows
        # finds none to remove.
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30)
        b = await sessions.sign_in(self.web, BOB, [THURSDAY], now=self.now, days=30)
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("DELETE FROM web_sessions WHERE user_id = %s", (BOB.id,))
            self.assertEqual(cur.rowcount, 0)
        self.assertIsNotNone(await sessions.find(self.web, b, now=self.now))
