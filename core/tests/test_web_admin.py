"""The admin sign-in (#772): only an admin email gets in, by Google or by password; wrong
tries lock; the session is a strict cookie with idle and absolute ends and a CSRF token.
No database: the admin routes never touch it."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import unittest
from typing import Any, ClassVar, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.config import ConfigError
from dmbot.db import Database
from dmbot.web.admin import (
    AdminError,
    AdminSessions,
    FailedTries,
    HttpGoogle,
    hash_password,
    password_matches,
    pkce_challenge,
)
from dmbot.web.app import create_app
from dmbot.web.settings import load_web_settings
from tests.test_web_api import API, SITE, FakeDiscord, settings
from tests.test_web_api import Settings as SettingsTest

ADMIN = "owner@example.com"
PASSWORD = "correct horse battery staple"
HASH = hash_password(PASSWORD)
HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}
CLIENT_ID = "admin-client.apps.googleusercontent.com"


def id_token(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


class FakeGoogle:
    def __init__(self, test: AdminTest) -> None:
        self.test = test
        self.claims_out: dict[str, Any] = {}
        self.fail = False
        self.asked: list[dict[str, str]] = []

    def authorize_url(self, *, state: str, nonce: str, challenge: str, redirect_uri: str) -> str:
        self.nonce = nonce
        self.challenge = challenge
        return f"https://accounts.google.com/auth?state={state}&redirect_uri={redirect_uri}"

    async def claims(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        self.asked.append({"code": code, "verifier": verifier, "redirect_uri": redirect_uri})
        if self.fail:
            raise AdminError("Google answered 400")
        return {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "exp": self.test.now + 300,
            "nonce": self.nonce,
            "email": ADMIN,
            "email_verified": True,
            **self.claims_out,
        }


class AdminTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = 1_800_000_000
        self.ticks = 0.0
        self.google = FakeGoogle(self)
        self.sessions = AdminSessions(clock=lambda: self.ticks)
        self.tries = FailedTries(clock=lambda: self.ticks)
        app = create_app(
            settings(
                admin_emails=(ADMIN,),
                admin_password_hash=HASH,
                google_client_id=CLIENT_ID,
                google_client_secret="g-secret",
                client_ip_header="CF-Connecting-IP",
            ),
            cast(Database, None),
            FakeDiscord(),
            google=self.google,
            admin_sessions=self.sessions,
            admin_tries=self.tries,
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def password(
        self, email: str = ADMIN, password: str = PASSWORD, ip: str = "203.0.113.5"
    ) -> httpx.Response:
        return await self.client.post(
            "/admin/auth/password",
            json={"email": email, "password": password},
            headers={**HEADERS, "CF-Connecting-IP": ip},
        )

    async def google_sign_in(self, **claims: Any) -> httpx.Response:
        self.google.claims_out = claims
        start = await self.client.get("/admin/auth/google/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        return await self.client.get(
            "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
        )

    async def me(self) -> httpx.Response:
        return await self.client.get("/admin/me")

    # Password

    async def test_the_right_password_signs_the_admin_in_with_a_strict_cookie(self) -> None:
        done = await self.password(email="  Owner@Example.com ")
        self.assertEqual(done.status_code, 204)
        cookie = done.headers["set-cookie"].lower()
        self.assertTrue(cookie.startswith("__host-dmbot_admin="))
        for part in ("httponly", "secure", "samesite=strict", "path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("domain=", cookie)
        self.assertEqual((await self.me()).json()["email"], ADMIN)

    async def test_wrong_email_wrong_password_and_lock_get_the_same_answer(self) -> None:
        wrong_password = await self.password(password="nope nope nope nope")
        wrong_email = await self.password(email="someone@example.com")
        for answer in (wrong_password, wrong_email):
            self.assertEqual((answer.status_code, answer.json()), (401, {"error": "wrong_sign_in"}))
        self.assertEqual((await self.me()).status_code, 401)

    async def test_five_wrong_tries_lock_the_email_for_15_minutes(self) -> None:
        for n in range(5):
            await self.password(password="wrong wrong wrong wrong", ip=f"198.51.100.{n}")
        # The right password from a new connection: still the same refusal, the email is locked.
        locked = await self.password(ip="192.0.2.9")
        self.assertEqual((locked.status_code, locked.json()), (401, {"error": "wrong_sign_in"}))
        self.ticks += 15 * 60
        self.assertEqual((await self.password(ip="192.0.2.9")).status_code, 204)

    async def test_five_wrong_tries_lock_the_connection_too(self) -> None:
        for n in range(5):
            await self.password(email=f"guess{n}@example.com")
        self.assertEqual((await self.password()).status_code, 401)
        self.assertEqual((await self.password(ip="192.0.2.9")).status_code, 204)

    async def test_nothing_secret_is_logged(self) -> None:
        with self.assertLogs("dmbot.web.admin_api", "INFO") as logs:
            await self.password(password="a wrong one, longer")
            await self.password()
        text = "\n".join(logs.output)
        for secret in (ADMIN, PASSWORD, "a wrong one", HASH):
            self.assertNotIn(secret, text)

    # Google

    async def test_google_signs_in_an_admin_with_a_verified_email(self) -> None:
        done = await self.google_sign_in()
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers["location"], f"{SITE}/admin")
        self.assertEqual((await self.me()).json()["email"], ADMIN)
        [asked] = self.google.asked
        self.assertEqual(asked["redirect_uri"], f"{API}/admin/auth/google/callback")
        self.assertEqual(pkce_challenge(asked["verifier"]), self.google.challenge)

    async def test_google_refuses_other_unverified_or_mismatched_sign_ins(self) -> None:
        for claims in (
            {"email": "someone@example.com"},
            {"email_verified": False},
            {"email_verified": "true"},
            {"aud": "someone-else"},
            {"iss": "https://evil.example"},
            {"nonce": "another sign-in"},
            {"exp": self.now - 1},
        ):
            refused = await self.google_sign_in(**claims)
            self.assertEqual(refused.headers["location"], f"{SITE}/admin?signin=failed", claims)
            self.assertEqual((await self.me()).status_code, 401, claims)

    async def test_google_refuses_a_wrong_state_or_a_missing_sign_in_cookie(self) -> None:
        start = await self.client.get("/admin/auth/google/start")
        self.assertIn("samesite=lax", start.headers["set-cookie"].lower())
        wrong = await self.client.get(
            "/admin/auth/google/callback", params={"state": "forged", "code": "g-code"}
        )
        self.assertEqual(wrong.headers["location"], f"{SITE}/admin?signin=failed")
        self.client.cookies.clear()
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        no_cookie = await self.client.get(
            "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
        )
        self.assertEqual(no_cookie.headers["location"], f"{SITE}/admin?signin=failed")
        self.assertEqual(self.google.asked, [])

    async def test_without_google_set_up_the_button_leads_back_with_words(self) -> None:
        app = create_app(
            settings(admin_emails=(ADMIN,)), cast(Database, None), FakeDiscord(), google=None
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        ) as client:
            answer = await client.get("/admin/auth/google/start")
        self.assertEqual(answer.headers["location"], f"{SITE}/admin?signin=off")

    async def test_google_failing_is_a_plain_refusal(self) -> None:
        self.google.fail = True
        refused = await self.google_sign_in()
        self.assertEqual(refused.headers["location"], f"{SITE}/admin?signin=failed")

    # The session

    async def test_the_session_ends_after_an_hour_idle(self) -> None:
        await self.password()
        self.ticks += 59 * 60
        self.assertEqual((await self.me()).status_code, 200)  # a visit keeps it alive
        self.ticks += 59 * 60
        self.assertEqual((await self.me()).status_code, 200)
        self.ticks += 60 * 60
        self.assertEqual((await self.me()).status_code, 401)

    async def test_the_session_ends_after_12_hours_however_busy(self) -> None:
        await self.password()
        for _ in range(12):
            self.ticks += 59 * 60
            await self.me()
        self.ticks += 13 * 60
        self.assertEqual((await self.me()).status_code, 401)

    async def test_sign_out_needs_the_csrf_token_and_ends_the_session(self) -> None:
        await self.password()
        csrf = (await self.me()).json()["csrf"]
        refused = await self.client.post("/admin/auth/logout", headers=HEADERS)
        self.assertEqual(refused.status_code, 403)
        forged = await self.client.post(
            "/admin/auth/logout", headers={**HEADERS, "X-Admin-CSRF": "forged"}
        )
        self.assertEqual(forged.status_code, 403)
        self.assertEqual((await self.me()).status_code, 200)
        out = await self.client.post(
            "/admin/auth/logout", headers={**HEADERS, "X-Admin-CSRF": csrf}
        )
        self.assertEqual(out.status_code, 204)
        self.assertEqual((await self.me()).status_code, 401)

    async def test_a_post_without_the_websites_header_is_refused(self) -> None:
        response = await self.client.post(
            "/admin/auth/password", json={"email": ADMIN, "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 403)

    async def test_with_no_admin_emails_the_routes_are_not_there(self) -> None:
        app = create_app(settings(), cast(Database, None), FakeDiscord())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API
        ) as client:
            self.assertEqual((await client.get("/admin/me")).status_code, 404)
            self.assertEqual((await client.get("/admin/auth/google/start")).status_code, 404)


class Passwords(unittest.TestCase):
    def test_hash_round_trip_and_short_passwords(self) -> None:
        self.assertTrue(password_matches(HASH, PASSWORD))
        self.assertFalse(password_matches(HASH, PASSWORD + "!"))
        self.assertFalse(password_matches("", PASSWORD))
        self.assertFalse(password_matches("not-a-hash", PASSWORD))
        self.assertNotIn("$", HASH)  # safe in .env without quotes
        with self.assertRaises(ValueError):
            hash_password("fifteen chars!!")


class AdminSettings(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        **SettingsTest.ENV,
        "ADMIN_EMAILS": "Owner@Example.com, second@example.com ",
        "ADMIN_PASSWORD_HASH": HASH,
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": "g-secret-1",
    }

    def test_reads_and_hides_the_admin_settings(self) -> None:
        loaded = load_web_settings(self.ENV)
        self.assertEqual(loaded.admin_emails, ("owner@example.com", "second@example.com"))
        self.assertNotIn(HASH, repr(loaded))
        self.assertNotIn("g-secret-1", repr(loaded))

    def test_refuses_bad_admin_settings(self) -> None:
        for change in (
            {"ADMIN_EMAILS": "not-an-email"},
            {"ADMIN_PASSWORD_HASH": "plain-password"},
            {"GOOGLE_CLIENT_SECRET": ""},
        ):
            with self.subTest(change=change), self.assertRaises(ConfigError):
                load_web_settings({**self.ENV, **change})


class GoogleClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.forms: list[dict[str, str]] = []

        async def token(request: web.Request) -> web.Response:
            form = {k: str(v) for k, v in (await request.post()).items()}
            self.forms.append(form)
            if form["code"] != "good":
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response({"id_token": id_token({"email": ADMIN})})

        app = web.Application()
        app.router.add_post("/token", token)
        self.server = TestServer(app)
        await self.server.start_server()
        self.google = HttpGoogle(
            CLIENT_ID, "g-secret", token_url=str(self.server.make_url("/token"))
        )

    async def asyncTearDown(self) -> None:
        await self.google.close()
        await self.server.close()

    async def test_it_exchanges_the_code_with_pkce_and_reads_the_claims(self) -> None:
        claims = await self.google.claims(
            code="good", verifier="v" * 43, redirect_uri="https://x/cb"
        )
        self.assertEqual(claims, {"email": ADMIN})
        self.assertEqual(self.forms[0]["code_verifier"], "v" * 43)
        self.assertEqual(self.forms[0]["client_secret"], "g-secret")

    async def test_a_refused_code_is_an_admin_error_without_secrets(self) -> None:
        with self.assertRaises(AdminError) as caught:
            await self.google.claims(code="bad", verifier="v" * 43, redirect_uri="https://x/cb")
        self.assertNotIn("g-secret", str(caught.exception))

    def test_the_authorize_link_asks_only_for_openid_email_with_pkce(self) -> None:
        url = self.google.authorize_url(state="s", nonce="n", challenge="c", redirect_uri="r")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["scope"], ["openid email"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual((query["state"], query["nonce"]), (["s"], ["n"]))


class HashCommand(unittest.TestCase):
    def run_it(self, typed: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "dmbot.web.hash_admin_password"],
            input=typed,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_it_prints_a_hash_that_matches_and_never_the_password(self) -> None:
        done = self.run_it(PASSWORD + "\n")
        self.assertEqual(done.returncode, 0)
        printed = done.stdout.strip()
        self.assertTrue(password_matches(printed, PASSWORD))
        self.assertNotIn(PASSWORD, done.stdout + done.stderr)

    def test_a_short_password_is_refused(self) -> None:
        done = self.run_it("short\n")
        self.assertEqual((done.returncode, done.stdout), (2, ""))
