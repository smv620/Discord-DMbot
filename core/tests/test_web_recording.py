"""Where DMbot may record you, and Stop recording me, on the website (#1151), as the website's
own database role: the person's own consent only, in their own servers, and the bot stopped
at once through the notification."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import errors

from dmbot import consent as consent_module
from dmbot.consent import ConsentStore
from dmbot.consent_site import SiteStops
from dmbot.db import Database
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.me import build_me
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
HEADERS = {"X-DMbot-Request": "1"}


class Recording(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.bot = ConsentStore(self.db)  # the bot's side, with its cache
        await self.bot.grant(THURSDAY.id, ALICE.id)
        await self.bot.grant(THURSDAY.id, BOB.id)
        await self.bot.grant(GORRAK.id, ALICE.id)
        await self.bot.grant(680000000000000444, ALICE.id)  # a server that isn't in her list
        self.discord = FakeDiscord()
        self.app = create_app(
            settings(),
            self.web,
            self.discord,
            payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        await self.sign_in(self.client)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.web.close()
        await super().asyncTearDown()

    async def sign_in(self, client: httpx.AsyncClient) -> None:
        start = await client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await client.get("/auth/discord/callback", params={"state": state, "code": "good-code"})

    async def still_agreed(self, guild_id: int, user_id: int) -> bool:
        return await self.bot.granted_at(guild_id, user_id) is not None

    async def test_me_lists_where_the_person_may_be_recorded(self) -> None:
        recording = (await self.client.get("/me")).json()["recording"]
        self.assertEqual(
            sorted((r["serverName"], r["on"]) for r in recording),
            sorted([(THURSDAY.name, True), (GORRAK.name, True)]),
        )
        self.assertTrue(all(r["agreedOn"] for r in recording))
        # Not a server outside her list, and not Bob's consent.
        self.assertNotIn("680000000000000444", str(recording))
        self.assertEqual(len(recording), 2)

    async def test_wording_from_before_is_listed_as_off(self) -> None:
        async with self.db.guild(GORRAK.id) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE guild_id = %s",
                (consent_module.TERMS_VERSION - 1, GORRAK.id),
            )
        recording = (await self.client.get("/me")).json()["recording"]
        self.assertEqual({r["serverName"]: r["on"] for r in recording}[GORRAK.name], False)

    async def test_stopping_removes_only_her_own_consent_in_that_server(self) -> None:
        stopped = await self.client.post(f"/me/recording/{THURSDAY.id}/stop", headers=HEADERS)
        self.assertEqual(stopped.status_code, 204)
        self.assertFalse(await self.still_agreed(THURSDAY.id, ALICE.id))
        self.assertTrue(await self.still_agreed(THURSDAY.id, BOB.id))  # not someone else's
        self.assertTrue(await self.still_agreed(GORRAK.id, ALICE.id))  # not another server
        names = {r["serverName"] for r in (await self.client.get("/me")).json()["recording"]}
        self.assertEqual(names, {GORRAK.name})

    async def test_stopping_twice_is_fine(self) -> None:
        url = f"/me/recording/{THURSDAY.id}/stop"
        self.assertEqual((await self.client.post(url, headers=HEADERS)).status_code, 204)
        self.assertEqual((await self.client.post(url, headers=HEADERS)).status_code, 204)

    async def test_a_server_that_is_not_hers_is_refused_and_untouched(self) -> None:
        refused = await self.client.post("/me/recording/680000000000000444/stop", headers=HEADERS)
        self.assertEqual((refused.status_code, refused.json()), (404, {"error": "not_found"}))
        self.assertTrue(await self.still_agreed(680000000000000444, ALICE.id))
        junk = await self.client.post("/me/recording/not-a-number/stop", headers=HEADERS)
        self.assertEqual(junk.status_code, 404)

    async def test_it_needs_the_site_header_and_a_signed_in_person(self) -> None:
        plain = await self.client.post(f"/me/recording/{THURSDAY.id}/stop")
        self.assertEqual(plain.status_code, 403)
        self.assertTrue(await self.still_agreed(THURSDAY.id, ALICE.id))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        ) as stranger:
            out = await stranger.post(f"/me/recording/{THURSDAY.id}/stop", headers=HEADERS)
        self.assertEqual(out.status_code, 401)
        self.assertTrue(await self.still_agreed(THURSDAY.id, ALICE.id))

    async def test_the_role_cannot_add_or_change_a_yes(self) -> None:
        session = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now, days=30),
            now=self.now,
        )
        assert session is not None
        async with self.web.user(ALICE.id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, %s)",
                    (THURSDAY.id, ALICE.id, self.now),
                )
        async with self.web.user(ALICE.id, session=session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("UPDATE consent SET granted_at = granted_at + 1")

    async def test_a_bare_delete_removes_only_her_own_row_in_the_server_set(self) -> None:
        # The database holds the website's role, whatever the code asks: signed in as Alice
        # with Thursday set, DELETE with no WHERE removes her Thursday row and nothing else.
        async with self.web.user(ALICE.id, session=await self.alice_hash()) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute("DELETE FROM consent RETURNING user_id, guild_id")
            self.assertEqual(
                [(r["user_id"], r["guild_id"]) for r in await cur.fetchall()],
                [(ALICE.id, THURSDAY.id)],
            )
        self.assertFalse(await self.still_agreed(THURSDAY.id, ALICE.id))
        self.assertTrue(await self.still_agreed(THURSDAY.id, BOB.id))  # someone else's
        self.assertTrue(await self.still_agreed(GORRAK.id, ALICE.id))  # another of her servers
        self.assertTrue(
            await self.still_agreed(680000000000000444, ALICE.id)
        )  # a server not on her list

    async def test_a_delete_in_a_server_not_on_her_list_removes_nothing(self) -> None:
        async with self.web.user(ALICE.id, session=await self.alice_hash()) as conn:
            await conn.execute(
                "SELECT set_config('dmbot.guild_id', %s, true)", ("680000000000000444",)
            )
            cur = await conn.execute("DELETE FROM consent RETURNING user_id")
            self.assertEqual(await cur.fetchall(), [])
        self.assertTrue(await self.still_agreed(680000000000000444, ALICE.id))

    async def alice_hash(self) -> str:
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY, GORRAK], now=self.now, days=30)
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        return found.id_hash

    async def test_a_stop_on_the_website_stops_the_running_bot_at_once(self) -> None:
        # The bot has Alice in its consent cache, as in a live session.
        await self.bot.consenting(THURSDAY.id)
        self.assertTrue(self.bot.has_consent(THURSDAY.id, ALICE.id))
        seen: list[bool] = []
        stopped = asyncio.Event()
        listening = asyncio.Event()

        async def withdraw(guild_id: int, user_id: int) -> None:
            # What the bot's withdraw path sees: still held, so the DM would be told.
            seen.append(self.bot.has_consent(guild_id, user_id))
            self.bot.stop_now(guild_id, user_id)
            stopped.set()

        def spawn(work: Awaitable[None], name: str) -> object:
            task = asyncio.ensure_future(work)
            if name == "consent-recheck":  # the connect check: the listener is now in effect
                task.add_done_callback(lambda _t: listening.set())
            return task

        follower = SiteStops(
            is_ours=lambda guild_id: guild_id == THURSDAY.id,
            consent=self.bot,
            stop=withdraw,
            spawn=spawn,
        )
        task = asyncio.ensure_future(follower.follow(self.db.listen))
        try:
            await asyncio.wait_for(listening.wait(), 10)
            # The website's process has its own consent store; this bot's memory is only told
            # through the notification, which names the server and the bot checks the database.
            done = await self.client.post(f"/me/recording/{THURSDAY.id}/stop", headers=HEADERS)
            self.assertEqual(done.status_code, 204)
            await asyncio.wait_for(stopped.wait(), 10)
            self.assertEqual(seen, [True])
            self.assertFalse(self.bot.has_consent(THURSDAY.id, ALICE.id))
            self.assertTrue(self.bot.has_consent(THURSDAY.id, BOB.id))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_build_me_has_no_recording_for_someone_who_never_agreed(self) -> None:
        token = await sessions.sign_in(
            self.web,
            DiscordUser(id=3003, name="Nobody", email=None),
            [QUILLON],
            now=self.now,
            days=30,
        )
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        me = await build_me(self.web, found, now=self.now)
        self.assertEqual(me["recording"], [])
