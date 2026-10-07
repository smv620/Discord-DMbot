"""The website's API (#435): Discord sign-in, sessions, /me and the request guards,
against a fake Discord and a real database."""

from __future__ import annotations

import time
import unittest
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot.campaigns.store import CampaignStore
from dmbot.config import ConfigError
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordError, DiscordGuild, DiscordUser, guild_from_json
from dmbot.web.settings import WebSettings, load_web_settings
from tests.pg import DatabaseTest

SITE = "https://dmbot.example"
API = "https://api.dmbot.example"
SECRET = b"k" * 40
ALICE = DiscordUser(id=1001, name="Belleros", email="belleros@example.com")
THURSDAY = DiscordGuild(id=111, name="Thursday Table", manage=True)
QUILLON = DiscordGuild(id=222, name="Quillon's Corner", manage=True)
GORRAK = DiscordGuild(id=333, name="Gorrak's Hall", manage=False)


class FakeDiscord:
    def __init__(self) -> None:
        self.user_info = ALICE
        self.guild_list = [THURSDAY, QUILLON, GORRAK]
        self.revoked: list[str] = []
        self.fail = False
        self.installed_guild: int | None = None  # what Discord says DMbot was added to

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://discord.com/oauth2/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange(self, code: str, redirect_uri: str) -> str:
        if self.fail or code != "good-code":
            raise DiscordError("refused")
        return "discord-access-token"

    async def user(self, token: str) -> DiscordUser:
        return self.user_info

    async def guilds(self, token: str) -> list[DiscordGuild]:
        return self.guild_list

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)

    def install_url(self, state: str, redirect_uri: str, guild_id: int, permissions: int) -> str:
        return (
            f"https://discord.com/oauth2/authorize?state={state}&guild_id={guild_id}"
            f"&permissions={permissions}&redirect_uri={redirect_uri}"
        )

    async def exchange_install(self, code: str, redirect_uri: str) -> tuple[str, int | None]:
        if self.fail or code != "install-code":
            raise DiscordError("refused")
        return "install-token", self.installed_guild


def settings(**changes: object) -> WebSettings:
    base: dict[str, object] = {
        "database_url": "unused",
        "discord_client_id": "123",
        "discord_client_secret": "shh",
        "secret_key": SECRET,
        "site_url": SITE,
        "api_url": API,
    }
    base.update(changes)
    return WebSettings(**base)  # type: ignore[arg-type]


class WebApi(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.discord = FakeDiscord()
        self.app = create_app(settings(), self.db, self.discord, clock=lambda: self.now)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def sign_in(self) -> httpx.Response:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        return await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    # Sign-in

    async def test_sign_in_sets_a_safe_session_cookie_and_goes_to_the_account_page(self) -> None:
        done = await self.sign_in()
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers["location"], f"{SITE}/account")
        cookie = next(
            h for h in done.headers.get_list("set-cookie") if h.startswith("__Host-dmbot_session=")
        )
        for part in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            self.assertIn(part.lower(), cookie.lower())
        self.assertNotIn("domain=", cookie.lower())
        # The Discord token was used once and given back, never stored or sent on.
        self.assertEqual(self.discord.revoked, ["discord-access-token"])
        self.assertNotIn("discord-access-token", str(done.headers))

    async def test_start_asks_discord_for_the_sign_in_with_a_browser_bound_state(self) -> None:
        start = await self.client.get("/auth/discord/start")
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        self.assertEqual(query["redirect_uri"], [f"{API}/auth/discord/callback"])
        state_cookie = start.cookies.get("__Host-dmbot_signin")
        self.assertEqual(state_cookie, query["state"][0])

    async def test_a_state_from_another_browser_is_refused(self) -> None:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.client.cookies.clear()  # a different browser: no state cookie
        done = await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )
        self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")
        self.assertEqual((await self.client.get("/me")).status_code, 401)

    async def test_a_forged_or_expired_state_is_refused(self) -> None:
        forged = sessions.new_state(b"x" * 40, self.now)
        self.client.cookies.set("__Host-dmbot_signin", forged, domain="api.dmbot.example")
        done = await self.client.get(
            "/auth/discord/callback", params={"state": forged, "code": "good-code"}
        )
        self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")
        old = sessions.new_state(SECRET, self.now - sessions.STATE_SECONDS - 1)
        self.assertFalse(sessions.state_ok(SECRET, old, old, self.now))

    async def test_a_malformed_state_is_a_plain_failure_not_an_error(self) -> None:
        for cookie, bad in (
            ("x", "é.1.x"),
            ("a.b", "a.b"),
            ("n.9999999999.A", "n.9999999999.A"),
            ("", ""),
            ("n.1.@@@", "n.1.@@@"),
        ):
            self.client.cookies.set("__Host-dmbot_signin", cookie, domain="api.dmbot.example")
            done = await self.client.get(
                "/auth/discord/callback", params={"state": bad, "code": "good-code"}
            )
            self.assertEqual(done.status_code, 302, bad)
            self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")
        # Text a browser could never send as a cookie, checked directly.
        for state, cookie in (("é.1.x", "é.1.x"), ("n.9.A", "n.9.A"), ("n.9.AAAAA", "n.9.AAAAA")):
            self.assertFalse(sessions.state_ok(SECRET, state, cookie, self.now))

    async def test_the_token_is_revoked_even_when_reading_the_person_fails(self) -> None:
        async def broken(token: str) -> DiscordUser:
            raise DiscordError("bad answer")

        self.discord.user = broken  # type: ignore[method-assign]
        done = await self.sign_in()
        self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")
        self.assertEqual(self.discord.revoked, ["discord-access-token"])

    async def test_an_unexpected_failure_still_sends_the_person_back_plainly(self) -> None:
        async def odd(token: str) -> list[DiscordGuild]:
            raise KeyError("id")

        self.discord.guilds = odd  # type: ignore[method-assign]
        done = await self.sign_in()
        self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")

    async def test_discord_refusing_sends_the_person_back_with_a_plain_failure(self) -> None:
        self.discord.fail = True
        done = await self.sign_in()
        self.assertEqual(done.headers["location"], f"{SITE}/account?signin=failed")

    # /me and sessions

    async def test_me_needs_a_session(self) -> None:
        response = await self.client.get("/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "signed_out"})

    async def test_me_shows_the_person_their_campaigns_and_their_servers(self) -> None:
        store = CampaignStore(self.db, clock=lambda: self.now)
        mine = await store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        await store.create(THURSDAY.id, "Someone Else's Game", 9999)
        await store.create(444, "Not In My Servers", ALICE.id)  # Alice isn't in server 444
        await self.sign_in()
        me = (await self.client.get("/me")).json()
        self.assertEqual(me["user"], {"id": "1001", "name": "Belleros"})
        self.assertIsNone(me["plan"])
        self.assertEqual(
            [(c["id"], c["name"], c["serverName"]) for c in me["campaigns"]],
            [(mine.id, "The Brynwater Crossing", "Thursday Table")],
        )
        # Servers Alice manages; Gorrak's Hall isn't one. DMbot is in Thursday Table.
        self.assertEqual(
            me["servers"],
            [
                {
                    "id": "111",
                    "name": "Thursday Table",
                    "hasDmbot": True,
                    "canLink": False,
                    "installedByYou": False,
                },
                {
                    "id": "222",
                    "name": "Quillon's Corner",
                    "hasDmbot": False,
                    "canLink": False,
                    "installedByYou": False,
                },
            ],
        )

    async def test_me_shows_the_plan(self) -> None:
        await self.sign_in()
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, plan_changed_at,"
                " provider, last_event_at, updated_at) VALUES (%s, 'table', 'grace', 18, 10,"
                " 1, %s, %s, %s, 0, 'fake', 0, 0)",
                (ALICE.id, self.now - 100, self.now + 86400, self.now + 3600),
            )
        plan = (await self.client.get("/me")).json()["plan"]
        self.assertEqual(plan["id"], "table")
        self.assertEqual(plan["status"], "grace")
        self.assertEqual((plan["hoursUsed"], plan["hoursCap"]), (0, 28))
        self.assertRegex(plan["renewsOn"], r"^\d{4}-\d{2}-\d{2}$")

    async def test_an_expired_session_is_signed_out(self) -> None:
        await self.sign_in()
        self.now += 31 * 86400  # the API's clock
        self.assertEqual((await self.client.get("/me")).status_code, 401)

    async def test_the_database_hides_and_sweeps_expired_sessions(self) -> None:
        # Signed in 31 days ago by the real clock; the API's clock stays at that moment, so
        # only the database's own expiry check (its clock) can refuse the session.
        self.now = int(time.time()) - 31 * 86400
        await self.sign_in()
        self.assertEqual((await self.client.get("/me")).status_code, 401)
        self.assertEqual(await sessions.delete_expired(self.db), 1)
        self.assertEqual(await sessions.delete_expired(self.db), 0)

    async def test_signing_out_one_browser_leaves_the_others(self) -> None:
        await self.sign_in()
        first = self.client.cookies.get("__Host-dmbot_session")
        assert first is not None
        self.client.cookies.clear()
        await self.sign_in()
        await self.client.post("/auth/logout", headers={"X-DMbot-Request": "1"})
        self.client.cookies.set("__Host-dmbot_session", first, domain="api.dmbot.example")
        self.assertEqual((await self.client.get("/me")).status_code, 200)

    async def test_signing_out_always_works(self) -> None:
        response = await self.client.post("/auth/logout", headers={"X-DMbot-Request": "1"})
        self.assertEqual(response.status_code, 204)

    async def test_a_plan_past_its_period_shows_as_lapsed(self) -> None:
        await self.sign_in()
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap,"
                " period_start, period_end, plan_changed_at, provider, last_event_at,"
                " updated_at) VALUES (%s, 'try-it', 'active', 8, 1, %s, %s, 0, 'fake', 0, 0)",
                (ALICE.id, self.now - 40 * 86400, self.now - 10 * 86400),
            )
        self.assertEqual((await self.client.get("/me")).json()["plan"]["status"], "lapsed")

    async def test_sign_out_ends_the_session(self) -> None:
        await self.sign_in()
        token = self.client.cookies.get("__Host-dmbot_session")
        assert token is not None
        out = await self.client.post("/auth/logout", headers={"X-DMbot-Request": "1"})
        self.assertEqual(out.status_code, 204)
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.assertEqual((await self.client.get("/me")).status_code, 401)

    async def test_the_database_keeps_only_the_cookies_hash(self) -> None:
        await self.sign_in()
        token = self.client.cookies.get("__Host-dmbot_session")
        assert token is not None
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT id_hash FROM web_sessions")
            rows = await cur.fetchall()
        self.assertEqual([r["id_hash"] for r in rows], [sessions.hash_token(token)])

    # Guards

    async def test_changes_need_the_request_header(self) -> None:
        await self.sign_in()
        response = await self.client.post("/auth/logout")
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.get("/me")).status_code, 200)

    async def test_changes_from_another_site_are_refused(self) -> None:
        await self.sign_in()
        response = await self.client.post(
            "/auth/logout",
            headers={"X-DMbot-Request": "1", "Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    async def test_refusals_carry_cors_headers_so_the_site_can_read_them(self) -> None:
        refused = await self.client.post("/auth/logout", headers={"Origin": SITE})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.headers.get("access-control-allow-origin"), SITE)
        self.assertEqual(refused.json(), {"error": "not_allowed"})

    async def test_cors_allows_only_the_website(self) -> None:
        ours = await self.client.options(
            "/me",
            headers={"Origin": SITE, "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(ours.headers.get("access-control-allow-origin"), SITE)
        self.assertEqual(ours.headers.get("access-control-allow-credentials"), "true")
        theirs = await self.client.get("/health", headers={"Origin": "https://evil.example"})
        self.assertIsNone(theirs.headers.get("access-control-allow-origin"))

    async def test_responses_are_never_cached(self) -> None:
        response = await self.client.get("/health")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual((await self.client.get("/docs")).status_code, 404)
        self.assertEqual((await self.client.get("/openapi.json")).status_code, 404)


class Settings(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        "DATABASE_URL": "postgresql://x",
        "DISCORD_CLIENT_ID": "1",
        "DISCORD_CLIENT_SECRET": "s",
        "WEB_SECRET_KEY": "k" * 40,
        "WEB_SITE_URL": "https://dmbot.example/",
        "WEB_API_URL": "https://api.dmbot.example",
    }

    def test_reads_the_settings(self) -> None:
        loaded = load_web_settings(self.ENV)
        self.assertEqual(loaded.site_origin, "https://dmbot.example")
        self.assertEqual(
            loaded.oauth_redirect_uri, "https://api.dmbot.example/auth/discord/callback"
        )
        self.assertTrue(loaded.secure_cookies)
        self.assertNotIn("shh", repr(loaded))
        self.assertNotIn("k" * 40, repr(loaded))

    def test_names_missing_settings(self) -> None:
        with self.assertRaisesRegex(ConfigError, "DISCORD_CLIENT_SECRET, WEB_SECRET_KEY"):
            load_web_settings(
                {
                    k: v
                    for k, v in self.ENV.items()
                    if k not in ("DISCORD_CLIENT_SECRET", "WEB_SECRET_KEY")
                }
            )

    def test_refuses_plain_http_except_on_localhost(self) -> None:
        with self.assertRaisesRegex(ConfigError, "WEB_API_URL must start with https://"):
            load_web_settings({**self.ENV, "WEB_API_URL": "http://api.dmbot.example"})
        local = load_web_settings({**self.ENV, "WEB_API_URL": "http://localhost:8080"})
        self.assertFalse(local.secure_cookies)

    def test_refuses_a_short_secret(self) -> None:
        with self.assertRaisesRegex(ConfigError, "at least 32"):
            load_web_settings({**self.ENV, "WEB_SECRET_KEY": "short"})


class DiscordShapes(unittest.TestCase):
    def test_who_can_add_dmbot_to_a_server(self) -> None:
        self.assertTrue(guild_from_json({"id": "1", "name": "a", "owner": True}).manage)
        self.assertTrue(guild_from_json({"id": "1", "name": "a", "permissions": "32"}).manage)
        self.assertTrue(guild_from_json({"id": "1", "name": "a", "permissions": "8"}).manage)
        self.assertFalse(guild_from_json({"id": "1", "name": "a", "permissions": "1024"}).manage)
