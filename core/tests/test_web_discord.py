"""Talking to Discord for sign-in (#435), against a pretend Discord over real HTTP."""

from __future__ import annotations

import unittest
from base64 import b64encode
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.web.discord import DiscordError, HttpDiscord

# The app sign-in Discord expects on its token calls: client id "1", secret "secret".
APP_AUTH = "Basic " + b64encode(b"1:secret").decode()


class PretendDiscord:
    def __init__(self) -> None:
        self.scope = "identify email guilds"
        self.revoked: list[str] = []
        self.user_status = 200

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth2/token", self.token)
        app.router.add_post("/oauth2/token/revoke", self.revoke)
        app.router.add_get("/users/@me", self.me)
        app.router.add_get("/users/@me/guilds", self.guilds)
        return app

    async def token(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("code") != "good" or request.headers.get("Authorization") != APP_AUTH:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "tok", "scope": self.scope})

    async def revoke(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != APP_AUTH:
            return web.json_response({"error": "invalid_client"}, status=401)
        self.revoked.append(str((await request.post()).get("token")))
        return web.Response(status=200)

    async def me(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer tok":
            return web.json_response({}, status=401)
        body: dict[str, Any] = {
            "id": "1001",
            "username": "belleros",
            "global_name": "Belleros",
            "email": "b@example.com",
            "verified": True,
        }
        return web.json_response(body, status=self.user_status)

    async def guilds(self, request: web.Request) -> web.Response:
        return web.json_response(
            [
                {"id": "111", "name": "Thursday Table", "owner": True, "permissions": "0"},
                {"id": "333", "name": "Gorrak's Hall", "owner": False, "permissions": "1024"},
            ]
        )


class HttpDiscordTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pretend = PretendDiscord()
        self.server = TestServer(self.pretend.app())
        await self.server.start_server()
        self.discord = HttpDiscord("1", "secret", api=str(self.server.make_url("")))

    async def asyncTearDown(self) -> None:
        await self.discord.close()
        await self.server.close()

    async def test_a_sign_in_reads_the_person_and_their_servers(self) -> None:
        token = await self.discord.exchange("good", "https://api.example/cb")
        user = await self.discord.user(token)
        guilds = await self.discord.guilds(token)
        await self.discord.revoke(token)
        self.assertEqual((user.id, user.name, user.email), (1001, "Belleros", "b@example.com"))
        self.assertEqual([(g.id, g.manage) for g in guilds], [(111, True), (333, False)])
        self.assertEqual(self.pretend.revoked, ["tok"])

    async def test_fewer_scopes_than_asked_is_refused(self) -> None:
        self.pretend.scope = "identify"
        with self.assertRaisesRegex(DiscordError, "fewer scopes"):
            await self.discord.exchange("good", "https://api.example/cb")

    async def test_a_refused_code_is_a_discord_error_without_details(self) -> None:
        with self.assertRaises(DiscordError) as caught:
            await self.discord.exchange("bad", "https://api.example/cb")
        self.assertNotIn("bad", str(caught.exception).replace("answered", ""))

    async def test_an_error_answer_is_a_discord_error(self) -> None:
        self.pretend.user_status = 500
        with self.assertRaises(DiscordError):
            await self.discord.user("tok")

    def test_the_sign_in_link_asks_only_for_three_scopes(self) -> None:
        url = self.discord.authorize_url("st", "https://api.example/cb")
        self.assertIn("scope=identify+email+guilds", url)
        self.assertIn("state=st", url)
