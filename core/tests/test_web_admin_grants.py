"""The admin page's free access routes (#773): only a signed-in admin with the CSRF token
may list, give, change or revoke; every write goes through dmbot.web.grants and is logged;
bad input is refused with a code the site words; the server's free list shows but can't be
revoked here."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from dmbot.web import grants
from dmbot.web.admin import AdminSessions, FailedTries
from dmbot.web.app import create_app
from tests.pg import DatabaseTest
from tests.test_web_admin import ADMIN, HASH, HEADERS, PASSWORD
from tests.test_web_api import API, FakeDiscord, settings

FRIEND = "123456789012345678"
OTHER = "223456789012345678"
ALWAYS = 323456789012345678
NOW = 1_800_000_000  # 2027-01-15 08:00 UTC


class AdminGrants(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = NOW
        app = create_app(
            settings(
                admin_emails=(ADMIN,),
                admin_password_hash=HASH,
                client_ip_header="CF-Connecting-IP",
                free_users=frozenset({ALWAYS}),
            ),
            self.db,
            FakeDiscord(),
            admin_sessions=AdminSessions(clock=lambda: 0.0),
            admin_tries=FailedTries(clock=lambda: 0.0),
            clock=lambda: self.now,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self) -> str:
        signed = await self.client.post(
            "/admin/auth/password", json={"email": ADMIN, "password": PASSWORD}, headers=HEADERS
        )
        self.assertEqual(signed.status_code, 204)
        return cast(str, (await self.client.get("/admin/me")).json()["csrf"])

    async def give(self, csrf: str, **body: Any) -> httpx.Response:
        return await self.client.post(
            "/admin/grants",
            json={"discordId": FRIEND, "level": "guild", **body},
            headers={**HEADERS, "X-Admin-CSRF": csrf},
        )

    async def revoke(self, csrf: str, discord_id: str) -> httpx.Response:
        return await self.client.post(
            f"/admin/grants/{discord_id}/revoke", headers={**HEADERS, "X-Admin-CSRF": csrf}
        )

    async def test_without_an_admin_session_or_csrf_every_route_is_refused(self) -> None:
        self.assertEqual((await self.client.get("/admin/grants")).status_code, 401)
        self.assertEqual((await self.give("anything")).status_code, 401)
        self.assertEqual((await self.revoke("anything", FRIEND)).status_code, 401)
        await self.sign_in()
        self.assertEqual((await self.give("forged")).status_code, 403)
        self.assertEqual((await self.revoke("forged", FRIEND)).status_code, 403)
        self.assertEqual(await grants.grants(self.db), [])

    async def test_give_change_and_revoke_are_listed_and_logged(self) -> None:
        csrf = await self.sign_in()
        first = await self.give(csrf, note="  a   playtester ", endsOn="2027-01-31")
        self.assertEqual(first.json(), {"action": "grant"})
        self.now += 60
        changed = await self.give(csrf, level="unlimited", endsOn=None)
        self.assertEqual(changed.json(), {"action": "change"})
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual(listed["free"], [str(ALWAYS)])
        (row,) = listed["grants"]
        self.assertEqual(
            row,
            {
                "discordId": FRIEND,
                "level": "unlimited",
                "endsAt": None,
                "note": "",
                "grantedBy": ADMIN,
                "grantedAt": NOW + 60,
            },
        )
        self.now += 60
        self.assertEqual((await self.revoke(csrf, FRIEND)).status_code, 204)
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual(listed["grants"], [])  # revoked: only in the history now
        self.assertEqual(
            [(e["action"], e["discordId"], e["by"]) for e in listed["log"]],
            [("revoke", FRIEND, ADMIN), ("change", FRIEND, ADMIN), ("grant", FRIEND, ADMIN)],
        )

    async def test_an_end_date_lasts_all_that_day(self) -> None:
        csrf = await self.sign_in()
        await self.give(csrf, endsOn="2027-01-31")
        (row,) = await grants.grants(self.db)
        self.assertEqual(row.ends_at, 1_801_440_000)  # 2027-02-01 00:00 UTC

    async def test_bad_input_is_refused_with_what_to_fix(self) -> None:
        csrf = await self.sign_in()
        cases: list[tuple[dict[str, Any], str]] = [
            ({"discordId": "12345"}, "bad_id"),
            ({"discordId": "12345678901234567x"}, "bad_id"),
            ({"discordId": 123456789012345678}, "bad_id"),
            ({"level": "everything"}, "bad_level"),
            ({"note": "x" * 201}, "long_note"),
            ({"endsOn": "31/01/2027"}, "bad_date"),
            ({"endsOn": "2027-02-30"}, "bad_date"),
            ({"endsOn": "2027-01-14"}, "past_date"),
        ]
        for body, code in cases:
            answer = await self.give(csrf, **body)
            self.assertEqual((answer.status_code, answer.json()), (400, {"error": code}), body)
        self.assertEqual(await grants.grants(self.db), [])

    async def test_the_free_list_shows_but_cant_be_revoked(self) -> None:
        csrf = await self.sign_in()
        self.assertEqual((await self.client.get("/admin/grants")).json()["free"], [str(ALWAYS)])
        answer = await self.revoke(csrf, str(ALWAYS))
        self.assertEqual((answer.status_code, answer.json()), (404, {"error": "no_grant"}))
        missing = await self.revoke(csrf, OTHER)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual((await self.revoke(csrf, "not-an-id")).status_code, 400)

    async def test_the_server_logs_name_the_id_only(self) -> None:
        csrf = await self.sign_in()
        with self.assertLogs("dmbot.web.admin_api", level=logging.INFO) as logs:
            await self.give(csrf, note="secret note")
            await self.revoke(csrf, FRIEND)
        text = "\n".join(logs.output)
        self.assertIn(FRIEND, text)
        self.assertNotIn("secret note", text)
        self.assertNotIn(ADMIN, text)
