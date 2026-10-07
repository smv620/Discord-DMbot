"""Adding DMbot to a server, linking a server, and deleting the account (#435)."""

from __future__ import annotations

import unittest
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot import entitlements, install
from dmbot.web import tokens
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.payments import FakeProvider, PaymentError
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, API, GORRAK, QUILLON, SECRET, SITE, FakeDiscord, settings

HEADERS = {"X-DMbot-Request": "1"}


class Accounts(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.discord = FakeDiscord()
        self.provider = FakeProvider(b"h" * 32, SITE)
        app = create_app(
            settings(), self.db, self.discord, payments=self.provider, clock=lambda: self.now
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        await self.sign_in()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def sign_in(self) -> None:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def install(self, server: int, *, added_to: int | None = None) -> httpx.Response:
        start = await self.client.get("/install", params={"server_id": str(server)})
        if start.headers["location"].startswith(SITE):
            return start  # refused before Discord
        query = parse_qs(urlsplit(start.headers["location"]).query)
        self.discord.installed_guild = server if added_to is None else added_to
        return await self.client.get(
            "/install/callback",
            params={"state": query["state"][0], "code": "install-code", "guild_id": str(server)},
        )

    async def me(self) -> dict[str, Any]:
        response = await self.client.get("/me")
        result: dict[str, Any] = response.json()
        return result

    # Install

    async def test_installing_asks_discord_for_this_server_with_dmbots_permissions(self) -> None:
        start = await self.client.get("/install", params={"server_id": str(QUILLON.id)})
        query = parse_qs(urlsplit(start.headers["location"]).query)
        self.assertEqual(query["guild_id"], [str(QUILLON.id)])
        self.assertEqual(query["permissions"], [str(install.permissions().value)])
        self.assertEqual(query["redirect_uri"], [f"{API}/install/callback"])

    async def test_an_install_is_recorded_with_its_installer(self) -> None:
        done = await self.install(QUILLON.id)
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=done")
        self.assertEqual(self.discord.revoked[-1], "install-token")
        me = await self.me()
        self.assertEqual(
            [(i["serverId"], i["via"]) for i in me["installs"]],
            [(str(QUILLON.id), "site")],
        )
        server = next(s for s in me["servers"] if s["id"] == str(QUILLON.id))
        self.assertEqual((server["hasDmbot"], server["installedByYou"]), (True, True))

    async def test_the_server_comes_from_discord_not_the_address(self) -> None:
        # The person picked Quillon's Corner, but Discord added DMbot somewhere else.
        done = await self.install(QUILLON.id, added_to=999)
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=failed")
        self.assertEqual((await self.me())["installs"], [])

    async def test_only_servers_the_person_manages(self) -> None:
        done = await self.install(GORRAK.id)  # a member, not a manager
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=not_allowed")
        done = await self.install(777)  # not in their servers at all
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=not_allowed")

    async def test_an_old_sign_in_must_sign_in_again_first(self) -> None:
        self.now += 25 * 3600
        done = await self.install(QUILLON.id)
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=sign_in_again")

    async def test_an_install_state_from_another_person_or_purpose_is_refused(self) -> None:
        await self.client.get("/install", params={"server_id": str(QUILLON.id)})
        self.discord.installed_guild = QUILLON.id
        forged = tokens.make(SECRET, "install", f"424242-{QUILLON.id}", now=self.now, seconds=600)
        delete = tokens.make(SECRET, "delete", str(ALICE.id), now=self.now, seconds=600)
        for state in (forged, delete):
            self.client.cookies.set("__Host-dmbot_install", state, domain="api.dmbot.example")
            done = await self.client.get(
                "/install/callback", params={"state": state, "code": "install-code"}
            )
            self.assertEqual(done.headers["location"], f"{SITE}/account?install=failed")

    async def test_whoever_approved_on_discord_must_be_the_person_signed_in(self) -> None:
        self.discord.user_info = DiscordUser(id=31337, name="Someone", email=None)
        done = await self.install(QUILLON.id)
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=failed")
        self.assertEqual((await self.me())["installs"], [])

    async def test_a_state_not_matching_this_browsers_cookie_is_refused(self) -> None:
        start = await self.client.get("/install", params={"server_id": str(QUILLON.id)})
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        other = tokens.make(
            SECRET, "install", f"{ALICE.id}-{QUILLON.id}", now=self.now, seconds=600
        )
        self.client.cookies.set("__Host-dmbot_install", other, domain="api.dmbot.example")
        self.discord.installed_guild = QUILLON.id
        done = await self.client.get(
            "/install/callback", params={"state": state, "code": "install-code"}
        )
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=failed")

    async def test_no_code_or_discord_refusing_is_a_plain_failure(self) -> None:
        start = await self.client.get("/install", params={"server_id": str(QUILLON.id)})
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        no_code = await self.client.get("/install/callback", params={"state": state})
        self.assertEqual(no_code.headers["location"], f"{SITE}/account?install=failed")
        start = await self.client.get("/install", params={"server_id": str(QUILLON.id)})
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        refused = await self.client.get(
            "/install/callback", params={"state": state, "code": "wrong-code"}
        )
        self.assertEqual(refused.headers["location"], f"{SITE}/account?install=failed")

    async def test_coming_back_signed_out_is_a_page_not_an_error(self) -> None:
        self.client.cookies.delete("__Host-dmbot_session")
        done = await self.client.get("/install/callback", params={"state": "x", "code": "y"})
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=signed_out")

    async def test_reinstalling_over_someone_elses_install_keeps_them(self) -> None:
        async with self.db.user(5) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (5, 0, 0)"
            )
        async with self.db.user(5, install_guild=QUILLON.id) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, 5, 0, 'site')",
                (QUILLON.id,),
            )
        done = await self.install(QUILLON.id)
        self.assertEqual(done.headers["location"], f"{SITE}/account?install=already_linked")

    async def test_installing_a_server_dmbot_joined_by_link_records_it_as_yours(self) -> None:
        await self.joined_by_link(QUILLON.id)
        await self.install(QUILLON.id)
        installs = (await self.me())["installs"]
        self.assertEqual(
            [(i["serverId"], i["serverName"], i["via"]) for i in installs],
            [(str(QUILLON.id), QUILLON.name, "site")],
        )

    # Link this server

    async def link(self, server: int) -> httpx.Response:
        return await self.client.post(f"/servers/{server}/link", headers=HEADERS)

    async def joined_by_link(self, server: int) -> None:
        async with self.db.guild(server) as conn:  # what the bot records when it joins
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, NULL, 0, 'link')",
                (server,),
            )

    async def test_linking_a_server_dmbot_joined_by_link(self) -> None:
        await self.joined_by_link(QUILLON.id)
        server = next(s for s in (await self.me())["servers"] if s["id"] == str(QUILLON.id))
        self.assertTrue(server["canLink"])
        self.assertEqual((await self.link(QUILLON.id)).status_code, 204)
        server = next(s for s in (await self.me())["servers"] if s["id"] == str(QUILLON.id))
        self.assertEqual((server["canLink"], server["installedByYou"]), (False, True))

    async def test_linking_needs_dmbot_there_and_no_other_installer(self) -> None:
        response = await self.link(QUILLON.id)
        self.assertEqual((response.status_code, response.json()), (409, {"error": "not_installed"}))
        await self.joined_by_link(QUILLON.id)
        async with self.db.user(5) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (5, 0, 0)"
            )
        async with self.db.user(5, install_guild=QUILLON.id) as conn:
            await conn.execute(
                "UPDATE installs SET installed_by_user_id = 5 WHERE guild_id = %s", (QUILLON.id,)
            )
        response = await self.link(QUILLON.id)
        self.assertEqual(
            (response.status_code, response.json()), (409, {"error": "already_linked"})
        )

    async def test_linking_only_servers_the_person_manages(self) -> None:
        await self.joined_by_link(GORRAK.id)
        self.assertEqual((await self.link(GORRAK.id)).status_code, 403)
        self.assertEqual((await self.link(int("9" * 18))).status_code, 403)
        self.assertEqual(
            (await self.client.post("/servers/abc/link", headers=HEADERS)).status_code, 404
        )

    # Delete the account

    async def delete(self, token: str | None = None) -> httpx.Response:
        if token is None:
            return await self.client.post("/account/delete/request", headers=HEADERS)
        return await self.client.post(
            "/account/delete/confirm", headers=HEADERS, json={"confirm_token": token}
        )

    async def test_delete_takes_two_steps(self) -> None:
        first = await self.delete()
        self.assertEqual(first.status_code, 200)
        self.assertEqual((await self.client.get("/me")).status_code, 200)  # nothing gone yet
        second = await self.delete(first.json()["confirm_token"])
        self.assertEqual(second.status_code, 204)
        self.assertEqual((await self.client.get("/me")).status_code, 401)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_users")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_delete_needs_a_real_fresh_token_for_this_person(self) -> None:
        for bad in (
            "nonsense",
            tokens.make(SECRET, "delete", "424242", now=self.now, seconds=600),
            tokens.make(SECRET, "install", str(ALICE.id), now=self.now, seconds=600),
            tokens.make(SECRET, "delete", str(ALICE.id), now=self.now - 700, seconds=600),
        ):
            response = await self.delete(bad)
            self.assertEqual(
                (response.status_code, response.json()), (403, {"error": "confirm_again"})
            )
        self.assertEqual((await self.client.get("/me")).status_code, 200)

    async def test_delete_stops_the_subscription_first(self) -> None:
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap,"
                " period_start, period_end, plan_changed_at, provider, provider_subscription_id,"
                " last_event_at, updated_at) VALUES (%s, 'table', 'active', 18, 1, 0, %s, 0,"
                " 'fake', 'sub_9', 0, 0)",
                (ALICE.id, self.now + 86400),
            )
        token = (await self.delete()).json()["confirm_token"]
        self.assertEqual((await self.delete(token)).status_code, 204)
        self.assertEqual(self.provider.cancelled, ["sub_9"])
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))

    async def test_delete_keeps_the_install_without_the_person(self) -> None:
        await self.install(QUILLON.id)
        token = (await self.delete()).json()["confirm_token"]
        self.assertEqual((await self.delete(token)).status_code, 204)
        async with self.db.guild(QUILLON.id) as conn:
            cur = await conn.execute("SELECT installed_by_user_id FROM installs")
            self.assertEqual([r["installed_by_user_id"] for r in await cur.fetchall()], [None])

    async def test_a_link_shows_in_the_persons_installs(self) -> None:
        await self.joined_by_link(QUILLON.id)
        await self.link(QUILLON.id)
        installs = (await self.me())["installs"]
        self.assertEqual([(i["serverName"], i["via"]) for i in installs], [(QUILLON.name, "link")])

    async def paid_plan(self, provider: str = "fake") -> None:
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap,"
                " period_start, period_end, plan_changed_at, provider, provider_subscription_id,"
                " last_event_at, updated_at) VALUES (%s, 'table', 'active', 18, 1, 0, %s, 0,"
                " %s, 'sub_9', 0, 0)",
                (ALICE.id, self.now + 86400, provider),
            )

    async def test_delete_is_refused_when_payments_cant_be_stopped(self) -> None:
        await self.paid_plan(provider="paddle")  # not the company this API talks to
        token = (await self.delete()).json()["confirm_token"]
        response = await self.delete(token)
        self.assertEqual(
            (response.status_code, response.json()), (503, {"error": "payments_unavailable"})
        )
        self.assertIsNotNone(await entitlements.get(self.db, ALICE.id))

    async def test_delete_is_refused_when_cancelling_fails(self) -> None:
        await self.paid_plan()

        async def broken(*, subscription_id: str) -> None:
            raise PaymentError("down")

        self.provider.cancel = broken  # type: ignore[method-assign]
        token = (await self.delete()).json()["confirm_token"]
        self.assertEqual((await self.delete(token)).status_code, 502)
        self.assertIsNotNone(await entitlements.get(self.db, ALICE.id))

    async def test_a_delete_confirmation_works_only_in_its_own_session(self) -> None:
        token = (await self.delete()).json()["confirm_token"]
        self.client.cookies.clear()
        await self.sign_in()  # the same person, a new session
        response = await self.delete(token)
        self.assertEqual((response.status_code, response.json()), (403, {"error": "confirm_again"}))

    async def test_confirming_with_a_broken_body_deletes_nothing(self) -> None:
        response = await self.client.post(
            "/account/delete/confirm",
            headers={**HEADERS, "content-type": "application/json"},
            content=b"{",
        )
        self.assertEqual((response.status_code, response.json()), (403, {"error": "confirm_again"}))
        self.assertEqual((await self.client.get("/me")).status_code, 200)

    async def test_the_old_single_delete_path_is_gone(self) -> None:
        response = await self.client.post("/account/delete", headers=HEADERS)
        self.assertIn(response.status_code, (404, 405))

    async def test_a_server_dmbot_left_is_offered_again(self) -> None:
        await self.joined_by_link(QUILLON.id)
        async with self.db.guild(QUILLON.id) as conn:  # the bot leaving (contract, 0013)
            await conn.execute("UPDATE installs SET left_at = 1 WHERE guild_id = %s", (QUILLON.id,))
        server = next(s for s in (await self.me())["servers"] if s["id"] == str(QUILLON.id))
        self.assertFalse(server["hasDmbot"])
        await self.install(QUILLON.id)  # back through the website
        async with self.db.guild(QUILLON.id) as conn:
            cur = await conn.execute("SELECT installed_by_user_id, via, left_at FROM installs")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            (row["installed_by_user_id"], row["via"], row["left_at"]), (ALICE.id, "site", None)
        )

    async def test_delete_needs_a_sign_in_from_the_last_15_minutes(self) -> None:
        self.now += 16 * 60
        response = await self.delete()
        self.assertEqual((response.status_code, response.json()), (403, {"error": "sign_in_again"}))

    async def test_delete_needs_a_recent_sign_in(self) -> None:
        self.now += 25 * 3600
        response = await self.delete()
        self.assertEqual((response.status_code, response.json()), (403, {"error": "sign_in_again"}))


class Tokens(unittest.TestCase):
    def test_a_token_reads_back_only_for_its_purpose_and_key(self) -> None:
        made = tokens.make(SECRET, "delete", "42", now=100, seconds=60)
        self.assertEqual(tokens.read(SECRET, "delete", made, now=159), "42")
        self.assertIsNone(tokens.read(SECRET, "delete", made, now=160))
        self.assertIsNone(tokens.read(SECRET, "install", made, now=100))
        self.assertIsNone(tokens.read(b"x" * 40, "delete", made, now=100))
        for bad in ("", "a.b", "é.1.2.3.4", made + "x", made.replace("42", "43")):
            self.assertIsNone(tokens.read(SECRET, "delete", bad, now=100))
