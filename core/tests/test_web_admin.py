"""The admin sign-in (#772): only an admin email gets in, by Google or by password; wrong
tries lock; the session is a strict cookie with idle and absolute ends and a CSRF token.
No database: the admin routes never touch it."""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from dmbot.config import ConfigError
from dmbot.db import Database
from dmbot.web import tokens
from dmbot.web.admin import (
    AdminError,
    AdminSessions,
    FailedTries,
    HttpGoogle,
    UsedStates,
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
        self.app = app
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
        if not start.headers["location"].startswith("https://accounts.google.com"):
            return start  # refused before Google
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

    async def test_wrong_email_and_wrong_password_get_the_same_answer(self) -> None:
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

    async def test_a_locked_try_skips_the_hash_and_gets_the_same_answer(self) -> None:
        for n in range(5):
            await self.password(password="wrong wrong wrong wrong", ip=f"198.51.100.{n}")
        with unittest.mock.patch("dmbot.web.admin_api.password_matches") as hasher:
            locked = await self.password(ip="192.0.2.9")
        hasher.assert_not_called()
        self.assertEqual((locked.status_code, locked.json()), (401, {"error": "wrong_sign_in"}))

    async def test_a_right_sign_in_clears_the_count(self) -> None:
        for _ in range(4):
            await self.password(password="wrong wrong wrong wrong")
        self.assertEqual((await self.password()).status_code, 204)
        for _ in range(4):
            await self.password(password="wrong wrong wrong wrong")
        self.assertEqual((await self.password()).status_code, 204)  # 4 + 4, never 5 in a row

    async def test_a_locked_connection_cant_use_google_either(self) -> None:
        for n in range(5):
            await self.password(email=f"guess{n}@example.com")
        self.client.headers["CF-Connecting-IP"] = "203.0.113.5"  # the same connection
        refused = await self.google_sign_in()
        self.assertEqual(refused.headers["location"], f"{SITE}/admin?signin=failed")

    async def test_signing_in_again_ends_the_old_session(self) -> None:
        await self.password()
        old = self.client.cookies.get("__Host-dmbot_admin")
        await self.password()
        self.assertNotEqual(self.client.cookies.get("__Host-dmbot_admin"), old)
        self.client.cookies.set("__Host-dmbot_admin", str(old))
        self.assertEqual((await self.me()).status_code, 401)

    async def test_a_non_ascii_csrf_header_is_a_plain_refusal(self) -> None:
        await self.password()
        response = await self.client.post(
            "/admin/auth/logout",
            headers=[
                (b"X-DMbot-Request", b"1"),
                (b"Origin", SITE.encode()),
                (b"X-Admin-CSRF", b"caf\xe9"),
            ],
        )
        self.assertEqual(response.status_code, 403)

    async def test_signing_out_of_an_ended_session_clears_its_cookie(self) -> None:
        await self.password()
        self.ticks += 2 * 3600
        out = await self.client.post("/admin/auth/logout", headers=HEADERS)
        self.assertEqual(out.status_code, 204)
        self.assertIn("__host-dmbot_admin=", out.headers["set-cookie"].lower())

    async def test_the_api_keeps_answering_while_a_password_is_checked(self) -> None:
        def slow(_hash: str, _password: str) -> bool:
            time.sleep(0.5)
            return False

        with unittest.mock.patch("dmbot.web.admin_api.password_matches", slow):
            trying = asyncio.create_task(self.password())
            await asyncio.sleep(0.05)
            started = time.monotonic()
            self.assertEqual((await self.client.get("/health")).status_code, 200)
            self.assertLess(time.monotonic() - started, 0.45)  # the check takes 0.5
            await trying

    async def test_checks_run_one_at_a_time_and_a_flood_is_refused_at_once(self) -> None:
        running = [0, 0, 0]  # now, most at once, hashes in all

        def slow(_hash: str, _password: str) -> bool:
            running[0] += 1
            running[1] = max(running[:2])
            running[2] += 1
            time.sleep(0.05)
            running[0] -= 1
            return False

        with unittest.mock.patch("dmbot.web.admin_api.password_matches", slow):
            answers = await asyncio.gather(
                *(self.password(ip=f"2001:db8:{n}::1") for n in range(12))
            )
        self.assertEqual(running[1], 1)
        self.assertLess(running[2], 12)  # the flood's tail isn't hashed
        codes = [a.status_code for a in answers]
        self.assertEqual(set(codes), {401, 503})
        # Too many at once says "busy", never that the password is wrong.
        busy = next(a for a in answers if a.status_code == 503)
        self.assertEqual(busy.json(), {"error": "busy"})

    async def test_a_wrong_email_costs_the_same_hash_as_a_wrong_password(self) -> None:
        checked: list[str] = []

        def counted(_hash: str, password: str) -> bool:
            checked.append(password)
            return password_matches(_hash, password)

        with unittest.mock.patch("dmbot.web.admin_api.password_matches", counted):
            await self.password(email="someone@example.com")
            await self.password(password="wrong wrong wrong wrong")
        self.assertEqual(len(checked), 2)

    async def test_google_still_works_while_the_email_is_locked(self) -> None:
        for _ in range(5):
            await self.password(password="wrong wrong wrong wrong", ip="198.51.100.7")
        self.assertEqual((await self.password(ip="198.51.100.8")).status_code, 401)
        done = await self.google_sign_in()
        self.assertEqual(done.headers["location"], f"{SITE}/admin")

    async def test_old_failures_fall_out_and_a_lock_isnt_stretched(self) -> None:
        for _ in range(4):
            await self.password(password="wrong wrong wrong wrong")
        self.ticks += 15 * 60
        await self.password(password="wrong wrong wrong wrong")
        self.assertEqual((await self.password()).status_code, 204)  # 1 recent, not 5
        await self.client.post("/admin/auth/logout", headers=HEADERS)
        self.client.cookies.clear()
        for _ in range(5):
            await self.password(password="wrong wrong wrong wrong")
        for _ in range(3):
            self.ticks += 4 * 60
            await self.password()  # trying while locked doesn't extend it
        self.ticks += 3 * 60 + 1
        self.assertEqual((await self.password()).status_code, 204)

    async def test_one_ipv6_network_is_one_connection(self) -> None:
        for n in range(5):
            await self.password(email=f"guess{n}@example.com", ip=f"2001:db8:1:2::{n + 1}")
        self.assertEqual((await self.password(ip="2001:db8:1:2::ff")).status_code, 401)
        self.assertEqual((await self.password(ip="2001:db8:1:3::1")).status_code, 204)

    async def test_bad_sign_in_bodies_are_bad_requests(self) -> None:
        for content in (b"not json", b"[]", b'{"password": "x"}', b'{"email": "a@b.c"}'):
            answer = await self.client.post(
                "/admin/auth/password",
                content=content,
                headers={**HEADERS, "Content-Type": "application/json"},
            )
            self.assertEqual(answer.status_code, 400, content)
        for body in (
            {"email": ADMIN, "password": "\ud800abc"},
            {"email": "\ud800@x.com", "password": PASSWORD},
        ):
            answer = await self.client.post(
                "/admin/auth/password",
                content=json.dumps(body).encode(),
                headers={**HEADERS, "Content-Type": "application/json"},
            )
            self.assertEqual(answer.status_code, 400, body)
        long = await self.password(password="x" * 1025)
        self.assertEqual(long.status_code, 400)
        huge = await self.client.post(
            "/admin/auth/password",
            content=b"x" * 5000,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        self.assertEqual(huge.status_code, 413)

    async def test_with_no_password_set_only_google_works(self) -> None:
        app = create_app(
            settings(admin_emails=(ADMIN,), client_ip_header="CF-Connecting-IP"),
            cast(Database, None),
            FakeDiscord(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API
        ) as client:
            answer = await client.post(
                "/admin/auth/password",
                json={"email": ADMIN, "password": PASSWORD},
                headers=HEADERS,
            )
        self.assertEqual((answer.status_code, answer.json()), (401, {"error": "wrong_sign_in"}))

    async def test_google_refuses_an_old_sign_in_or_no_code_without_asking_google(self) -> None:
        start = await self.client.get("/admin/auth/google/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        no_code = await self.client.get("/admin/auth/google/callback", params={"state": state})
        self.assertEqual(no_code.headers["location"], f"{SITE}/admin?signin=failed")
        start = await self.client.get("/admin/auth/google/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.now += 601
        old = await self.client.get(
            "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
        )
        self.assertEqual(old.headers["location"], f"{SITE}/admin?signin=failed")
        self.assertEqual(self.google.asked, [])

    async def test_google_failing_counts_as_a_wrong_try(self) -> None:
        self.google.fail = True
        for _ in range(5):
            await self.google_sign_in()
        self.assertEqual(len(self.google.asked), 5)
        await self.google_sign_in()
        self.assertEqual(len(self.google.asked), 5)  # locked: Google isn't asked

    async def test_a_strange_cookie_is_signed_out_not_an_error(self) -> None:
        response = await self.client.get(
            "/admin/me", headers=[(b"Cookie", b"__Host-dmbot_admin=caf\xe9")]
        )
        self.assertEqual(response.status_code, 401)

    async def test_sign_out_deletes_the_cookie_the_browser_way(self) -> None:
        await self.password()
        csrf = (await self.me()).json()["csrf"]
        out = await self.client.post(
            "/admin/auth/logout", headers={**HEADERS, "X-Admin-CSRF": csrf}
        )
        cookie = out.headers["set-cookie"].lower()
        self.assertIn("max-age=0", cookie)
        for part in ("secure", "path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("domain=", cookie)

    async def test_five_wrong_tries_lock_the_connection_too(self) -> None:
        for n in range(5):
            await self.password(email=f"guess{n}@example.com")
        self.assertEqual((await self.password()).status_code, 401)
        self.assertEqual((await self.password(ip="192.0.2.9")).status_code, 204)

    async def test_nothing_secret_is_logged(self) -> None:
        with self.assertLogs(level="INFO") as logs:
            await self.password(password="a wrong one, longer")
            await self.password()
            self.google.fail = True
            await self.google_sign_in()
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

    async def test_a_google_sign_in_works_once(self) -> None:
        self.google.claims_out = {}
        start = await self.client.get("/admin/auth/google/start")
        saved = dict(self.client.cookies)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        callback = {"state": state, "code": "g-code"}
        done = await self.client.get("/admin/auth/google/callback", params=callback)
        self.assertEqual(done.headers["location"], f"{SITE}/admin")
        self.client.cookies.clear()
        self.client.cookies.update(saved)
        again = await self.client.get("/admin/auth/google/callback", params=callback)
        self.assertEqual(again.headers["location"], f"{SITE}/admin?signin=failed")
        self.assertEqual(len(self.google.asked), 1)
        self.assertEqual((await self.me()).status_code, 401)

    async def test_a_sign_in_cookie_signed_with_another_key_is_refused(self) -> None:
        start = await self.client.get("/admin/auth/google/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        self.client.cookies.clear()
        forged = tokens.make(b"another key", "admin-google", state, now=self.now, seconds=600)
        self.client.cookies.set("__Host-dmbot_admin_signin", forged)
        refused = await self.client.get(
            "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
        )
        self.assertEqual(refused.headers["location"], f"{SITE}/admin?signin=failed")
        self.assertEqual(self.google.asked, [])

    async def test_a_flood_of_starts_cant_push_out_a_real_sign_in(self) -> None:
        self.google.claims_out = {}
        start = await self.client.get("/admin/auth/google/start")
        saved = dict(self.client.cookies)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        nonce = self.google.nonce
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        ) as flood:
            # The start keeps nothing on the server, so any number does the same; the
            # table this once filled held 10,000.
            for _ in range(1_000):
                await flood.get(
                    "/admin/auth/google/start", headers={"CF-Connecting-IP": "198.51.100.66"}
                )
        self.google.nonce = nonce  # the fake remembers the last start's
        self.client.cookies.clear()
        self.client.cookies.update(saved)
        done = await self.client.get(
            "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
        )
        self.assertEqual(done.headers["location"], f"{SITE}/admin")

    async def test_a_burst_of_callbacks_asks_google_at_most_five_times(self) -> None:
        self.google.fail = True
        starts = []
        for _ in range(20):
            start = await self.client.get("/admin/auth/google/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            starts.append((dict(self.client.cookies), state))
            self.client.cookies.clear()

        async def callback(cookies: dict[str, str], state: str) -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url=API,
                cookies=cookies,
                follow_redirects=False,
            ) as one:
                return await one.get(
                    "/admin/auth/google/callback", params={"state": state, "code": "made-up"}
                )

        answers = await asyncio.gather(*(callback(c, s) for c, s in starts))
        self.assertTrue(all("signin=failed" in a.headers["location"] for a in answers))
        self.assertLessEqual(len(self.google.asked), 5)
        # The owner's own sign-in from elsewhere still works.
        self.google.fail = False
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=API,
            headers={"CF-Connecting-IP": "198.51.100.20"},
            follow_redirects=False,
        ) as owner:
            start = await owner.get("/admin/auth/google/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            done = await owner.get(
                "/admin/auth/google/callback", params={"state": state, "code": "g-code"}
            )
        self.assertEqual(done.headers["location"], f"{SITE}/admin")

    async def test_a_google_success_lifts_the_connections_lock(self) -> None:
        # The google_sign_in helper sends no address header: it's the test client's own.
        here = "127.0.0.1"
        for _ in range(4):
            await self.password(password="wrong wrong wrong wrong", ip=here)
        done = await self.google_sign_in()  # counted first, so it sets the lock, then lifts it
        self.assertEqual(done.headers["location"], f"{SITE}/admin")
        self.client.cookies.clear()
        self.assertEqual((await self.password(ip=here)).status_code, 204)
        self.client.cookies.clear()
        start = await self.client.get("/admin/auth/google/start")
        self.assertTrue(start.headers["location"].startswith("https://accounts.google.com"))

    async def test_many_google_sign_ins_in_a_row_keep_working(self) -> None:
        for n in range(6):
            self.client.cookies.clear()
            done = await self.google_sign_in()
            self.assertEqual(done.headers["location"], f"{SITE}/admin", n)

    async def test_a_locked_connection_cant_start_google_either(self) -> None:
        for _ in range(5):
            await self.password(password="wrong wrong wrong wrong", ip="198.51.100.9")
        start = await self.client.get(
            "/admin/auth/google/start", headers={"CF-Connecting-IP": "198.51.100.9"}
        )
        self.assertEqual(start.headers["location"], f"{SITE}/admin?signin=failed")

    async def test_google_accepts_its_issuer_with_or_without_https(self) -> None:
        done = await self.google_sign_in(iss="accounts.google.com")
        self.assertEqual(done.headers["location"], f"{SITE}/admin")

    async def test_the_page_learns_which_sign_ins_are_set_up(self) -> None:
        both = await self.client.get("/admin/auth/ways")
        self.assertEqual(both.json(), {"google": True, "password": True})
        app = create_app(
            settings(admin_emails=(ADMIN,), admin_password_hash=HASH),
            cast(Database, None),
            FakeDiscord(),
            google=None,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API
        ) as client:
            only = await client.get("/admin/auth/ways")
        self.assertEqual(only.json(), {"google": False, "password": True})

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
            for path in ("/admin/me", "/admin/auth/ways", "/admin/auth/google/start"):
                self.assertEqual((await client.get(path)).status_code, 404, path)
            callback = await client.get(
                "/admin/auth/google/callback", params={"state": "s", "code": "c"}
            )
            self.assertEqual(callback.status_code, 404)
            for path in ("/admin/auth/password", "/admin/auth/logout"):
                answer = await client.post(
                    path, json={"email": ADMIN, "password": PASSWORD}, headers=HEADERS
                )
                self.assertEqual(answer.status_code, 404, path)


class Locks(unittest.TestCase):
    def test_the_maps_stay_bounded_and_keep_refusing_when_full(self) -> None:
        now = [0.0]
        tries = FailedTries(clock=lambda: now[0], most_keys=3)
        for n in range(10):
            for _ in range(5):
                tries.fail(f"addr:{n}")
            now[0] += 1
        self.assertLessEqual(len(tries._locked), 3)
        self.assertTrue(tries.locked("addr:9"))  # the newest lock always holds
        for n in range(10):
            tries.fail(f"email:{n}")
        self.assertLessEqual(len(tries._fails), 3)


class Used(unittest.TestCase):
    def test_a_state_is_spent_once_and_forgotten_after_ten_minutes(self) -> None:
        now = [0.0]
        used = UsedStates(clock=lambda: now[0])
        self.assertFalse(used.spent("state-1"))
        self.assertTrue(used.spend("state-1"))
        self.assertTrue(used.spent("state-1"))
        self.assertFalse(used.spend("state-1"))
        now[0] += 600
        self.assertTrue(used.spend("state-1"))  # its cookie has long expired by now

    def test_a_full_table_refuses_and_never_evicts_a_live_state(self) -> None:
        now = [0.0]
        used = UsedStates(clock=lambda: now[0], most=3)
        for n in range(3):
            self.assertTrue(used.spend(f"state-{n}"))
        self.assertFalse(used.spend("state-new"))  # refused, not evicting
        self.assertTrue(used.spent("state-0"))
        now[0] += 600
        self.assertTrue(used.spend("state-new"))  # expired ones make room


class SessionCap(unittest.TestCase):
    def test_the_oldest_session_goes_when_there_are_too_many(self) -> None:
        sessions = AdminSessions(clock=lambda: 0.0)
        first = sessions.start(ADMIN)
        for _ in range(20):
            sessions.start(ADMIN)
        self.assertIsNone(sessions.find(first))


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
        "WEB_CLIENT_IP_HEADER": "CF-Connecting-IP",
    }

    def test_reads_and_hides_the_admin_settings(self) -> None:
        loaded = load_web_settings(self.ENV)
        self.assertEqual(loaded.admin_emails, ("owner@example.com", "second@example.com"))
        self.assertNotIn(HASH, repr(loaded))
        self.assertNotIn("g-secret-1", repr(loaded))

    def test_refuses_bad_admin_settings(self) -> None:
        for change in (
            {"ADMIN_EMAILS": "not-an-email"},
            {"WEB_CLIENT_IP_HEADER": ""},
            {"ADMIN_PASSWORD_HASH": "plain-password"},
            {"GOOGLE_CLIENT_SECRET": ""},
            # Damaged: the right start, but not a whole argon2id hash.
            {"ADMIN_PASSWORD_HASH": base64.urlsafe_b64encode(b"$argon2id$x").decode()},
            # No way in at all.
            {"ADMIN_PASSWORD_HASH": "", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""},
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

    def test_the_server_only_ever_asks_googles_own_token_endpoint(self) -> None:
        # Skipping the ID token's signature check is sound only for this exact address.
        self.assertEqual(
            HttpGoogle(CLIENT_ID, "s")._token_url, "https://oauth2.googleapis.com/token"
        )
        main = (Path(__file__).parents[1] / "src/dmbot/web/__main__.py").read_text()
        self.assertIn("HttpGoogle(settings.google_client_id, settings.google_client_secret)", main)
        self.assertNotIn("token_url", main)

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
