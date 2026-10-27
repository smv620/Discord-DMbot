"""The admin page's free access routes (#773): only a signed-in admin with the CSRF token
may list, give, change or revoke; every write goes through dmbot.web.grants and is logged;
bad input is refused with a code the site words; the server's free list shows but can't be
revoked here."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from dmbot.web import grants, sessions
from dmbot.web.admin import AdminSessions, FailedTries
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from tests.pg import DatabaseTest
from tests.test_web_admin import ADMIN, HASH, HEADERS, PASSWORD
from tests.test_web_api import FakeDiscord, settings

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
        self.app = app
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
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
        self.assertEqual(first.json(), {"action": "grant", "name": None})
        self.now += 60
        changed = await self.give(csrf, level="unlimited", endsOn=None)
        self.assertEqual(changed.json(), {"action": "change", "name": None})
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual(listed["free"], [{"discordId": str(ALWAYS), "name": None}])
        (row,) = listed["grants"]
        self.assertEqual(
            row,
            {
                "discordId": FRIEND,
                "name": None,
                "level": "unlimited",
                "endsAt": None,
                "note": "",
                "grantedBy": ADMIN,
                "grantedAt": NOW + 60,
                "alsoFree": False,
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
            ({"discordId": "99999999999999999999"}, "bad_id"),  # past the 64-bit range
            ({"discordId": "00000000000000000"}, "bad_id"),
            ({"discordId": str(ALWAYS)}, "already_free"),
            ({"note": "a\u0000b"}, "bad_note"),
            ({"note": "a\ud800b"}, "bad_note"),
            ({"endsOn": "9999-12-31"}, "bad_date"),
            ({"level": "everything"}, "bad_level"),
            ({"note": "x" * 201}, "long_note"),
            ({"endsOn": "31/01/2027"}, "bad_date"),
            ({"endsOn": "2027-02-30"}, "bad_date"),
            ({"endsOn": "2027-01-14"}, "past_date"),
            ({"endsOn": "2027-01-15"}, "past_date"),  # today: "after today" means after
            ({"note": 5}, "bad_note"),
            ({"level": None}, "bad_level"),
        ]
        for body, code in cases:
            answer = await self.client.post(
                "/admin/grants",
                content=json.dumps({"discordId": FRIEND, "level": "guild", **body}).encode(),
                headers={**HEADERS, "X-Admin-CSRF": csrf, "Content-Type": "application/json"},
            )
            self.assertEqual((answer.status_code, answer.json()), (400, {"error": code}), body)
        not_json = await self.client.post(
            "/admin/grants",
            content=b"[]",
            headers={**HEADERS, "X-Admin-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(not_json.status_code, 400)
        self.assertEqual(await grants.grants(self.db), [])

    async def test_edges_that_are_fine(self) -> None:
        csrf = await self.sign_in()
        self.assertEqual((await self.client.get("/admin/grants")).status_code, 200)  # no CSRF
        # 200 letters once spaces are squeezed; and tomorrow (UTC) as the last day.
        self.assertEqual((await self.give(csrf, note="x" * 200 + "    ")).status_code, 200)
        self.assertEqual((await self.give(csrf, endsOn="2027-01-16")).status_code, 200)

    async def test_an_ended_grant_still_lists_until_revoked(self) -> None:
        csrf = await self.sign_in()
        await self.give(csrf, endsOn="2027-01-16")
        self.now += 2 * 86400
        (row,) = (await self.client.get("/admin/grants")).json()["grants"]
        self.assertLess(row["endsAt"], self.now)  # the page marks it "Ended"
        self.assertEqual((await self.revoke(csrf, FRIEND)).status_code, 204)

    async def test_the_free_list_shows_but_cant_be_revoked(self) -> None:
        csrf = await self.sign_in()
        free = (await self.client.get("/admin/grants")).json()["free"]
        self.assertEqual(free, [{"discordId": str(ALWAYS), "name": None}])
        answer = await self.revoke(csrf, str(ALWAYS))
        self.assertEqual((answer.status_code, answer.json()), (404, {"error": "no_grant"}))
        missing = await self.revoke(csrf, OTHER)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual((await self.revoke(csrf, "not-an-id")).status_code, 400)

    async def test_a_grant_for_someone_now_on_the_free_list_is_flagged_and_can_be_removed(
        self,
    ) -> None:
        # The grant came first; the owner added the person to the server's free list later (#2196).
        csrf = await self.sign_in()
        await self.give(csrf)
        await self.give(csrf, discordId=OTHER)
        later = create_app(
            settings(
                admin_emails=(ADMIN,),
                admin_password_hash=HASH,
                client_ip_header="CF-Connecting-IP",
                free_users=frozenset({ALWAYS, int(FRIEND)}),
            ),
            self.db,
            FakeDiscord(),
            admin_sessions=AdminSessions(clock=lambda: 0.0),
            admin_tries=FailedTries(clock=lambda: 0.0),
            clock=lambda: self.now,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=later),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.addAsyncCleanup(client.aclose)
        signed = await client.post(
            "/admin/auth/password", json={"email": ADMIN, "password": PASSWORD}, headers=HEADERS
        )
        self.assertEqual(signed.status_code, 204)
        csrf = (await client.get("/admin/me")).json()["csrf"]
        listed = (await client.get("/admin/grants")).json()
        self.assertEqual({f["discordId"] for f in listed["free"]}, {str(ALWAYS), FRIEND})
        flags = {g["discordId"]: g["alsoFree"] for g in listed["grants"]}
        self.assertEqual(flags, {FRIEND: True, OTHER: False})
        # "Remove it" is the ordinary revoke, so it is logged and the other grant stays.
        gone = await client.post(
            f"/admin/grants/{FRIEND}/revoke", headers={**HEADERS, "X-Admin-CSRF": csrf}
        )
        self.assertEqual(gone.status_code, 204)
        after = (await client.get("/admin/grants")).json()
        self.assertEqual([g["discordId"] for g in after["grants"]], [OTHER])
        self.assertEqual(after["log"][0]["action"], "revoke")
        self.assertEqual(after["log"][0]["discordId"], FRIEND)

    async def fill_log(self, csrf: str, entries: int) -> None:
        """`entries` log lines: a grant for one person, then changes to it."""
        await self.give(csrf)
        for n in range(entries - 1):
            self.now += 1
            await self.give(csrf, note=f"change {n}")

    async def test_the_first_page_of_changes_is_the_newest_twenty_with_the_page_count(self) -> None:
        csrf = await self.sign_in()
        await self.fill_log(csrf, 45)
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual((len(listed["log"]), listed["logPage"], listed["logPages"]), (20, 1, 3))
        self.assertEqual(listed["log"][0]["at"], NOW + 44)  # newest first
        second = (await self.client.get("/admin/grants/log?page=2")).json()
        self.assertEqual((len(second["log"]), second["page"], second["pages"]), (20, 2, 3))
        self.assertEqual(second["log"][0]["at"], NOW + 24)
        last = (await self.client.get("/admin/grants/log?page=3")).json()
        self.assertEqual(len(last["log"]), 5)  # the remainder
        self.assertEqual(last["log"][-1]["action"], "grant")  # the very first change is last
        # The pages together are every entry once, in order, with the same shape as the first.
        times = [e["at"] for p in (listed, second, last) for e in p["log"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(times), 45)
        self.assertEqual(set(last["log"][0]), set(listed["log"][0]))

    async def test_an_empty_log_is_one_empty_page(self) -> None:
        await self.sign_in()
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual((listed["log"], listed["logPage"], listed["logPages"]), ([], 1, 1))
        first = (await self.client.get("/admin/grants/log?page=1")).json()
        self.assertEqual(first, {"log": [], "page": 1, "pages": 1})

    async def test_a_page_that_is_not_there_or_not_a_number_is_a_bad_page(self) -> None:
        csrf = await self.sign_in()
        await self.fill_log(csrf, 21)  # two pages
        for bad in (
            "0",
            "-1",
            "3",
            "abc",
            "",
            "1.5",
            "+2",
            "%202",
            "2%20",
            "2%0A",
            "%D9%A2",
            "%C2%B2",
            "999999999",
            "99999999999999999999",
        ):
            answer = await self.client.get(f"/admin/grants/log?page={bad}")
            self.assertEqual((answer.status_code, answer.json()), (400, {"error": "bad_page"}), bad)
        self.assertEqual((await self.client.get("/admin/grants/log?page=2")).status_code, 200)
        # Leading zeros are the same number.
        self.assertEqual((await self.client.get("/admin/grants/log?page=002")).json()["page"], 2)
        # No page given is page 1.
        self.assertEqual((await self.client.get("/admin/grants/log")).json()["page"], 1)

    async def test_a_full_page_has_no_empty_page_after_it(self) -> None:
        csrf = await self.sign_in()
        await self.fill_log(csrf, 20)
        listed = (await self.client.get("/admin/grants")).json()
        self.assertEqual((len(listed["log"]), listed["logPages"]), (20, 1))
        self.assertEqual((await self.client.get("/admin/grants/log?page=2")).status_code, 400)
        await self.give(csrf, note="the twenty-first")
        self.assertEqual((await self.client.get("/admin/grants")).json()["logPages"], 2)
        self.assertEqual(len((await self.client.get("/admin/grants/log?page=2")).json()["log"]), 1)
        await self.fill_log(csrf, 19)  # 40 in all
        self.assertEqual((await self.client.get("/admin/grants")).json()["logPages"], 2)
        self.assertEqual((await self.client.get("/admin/grants/log?page=3")).status_code, 400)

    async def test_the_log_pages_need_an_admin_sign_in(self) -> None:
        self.assertEqual((await self.client.get("/admin/grants/log?page=1")).status_code, 401)

    async def test_the_server_logs_name_the_id_only(self) -> None:
        csrf = await self.sign_in()
        with self.assertLogs("dmbot.web.admin_api", level=logging.INFO) as logs:
            await self.give(csrf, note="secret note")
            await self.revoke(csrf, FRIEND)
        text = "\n".join(logs.output)
        self.assertIn(FRIEND, text)
        self.assertNotIn("secret note", text)
        self.assertNotIn(ADMIN, text)

    async def test_a_body_that_is_too_big_or_not_an_object_or_has_no_level_is_refused(self) -> None:
        csrf = await self.sign_in()
        for content in (b"[]", b'"text"', b'{"discordId": "' + FRIEND.encode() + b'"}'):
            answer = await self.client.post(
                "/admin/grants",
                content=content,
                headers={**HEADERS, "X-Admin-CSRF": csrf, "Content-Type": "application/json"},
            )
            self.assertEqual(answer.status_code, 400, content)
        huge = await self.client.post(
            "/admin/grants",
            content=b'{"note": "' + b"x" * 5000 + b'"}',
            headers={**HEADERS, "X-Admin-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(huge.status_code, 413)
        self.assertEqual(await grants.grants(self.db), [])

    async def test_a_refused_write_leaves_a_grant_and_the_log_alone(self) -> None:
        csrf = await self.sign_in()
        await self.give(csrf, note="keep")
        before = (await grants.grants(self.db), await grants.log_entries(self.db))
        # No session, then a session with a forged token: nothing changes, either way.
        other = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://web-api.internal.test"
        )
        self.addAsyncCleanup(other.aclose)
        for answer in (
            await other.post(
                "/admin/grants", json={"discordId": FRIEND, "level": "unlimited"}, headers=HEADERS
            ),
            await other.post(f"/admin/grants/{FRIEND}/revoke", headers=HEADERS),
            await self.give("forged", level="unlimited", note="changed"),
            await self.revoke("forged", FRIEND),
        ):
            self.assertIn(answer.status_code, (401, 403))
        self.assertEqual((await grants.grants(self.db), await grants.log_entries(self.db)), before)

    async def test_names_show_for_people_who_signed_in_lately_and_only_those(self) -> None:
        csrf = await self.sign_in()
        user = DiscordUser(id=int(FRIEND), name="Sam", email=None)
        await sessions.sign_in(self.db, user, [], now=NOW - 100, days=30)
        await sessions.sign_in(
            self.db,
            DiscordUser(id=int(OTHER), name="Old", email=None),
            [],
            now=NOW - 40 * 86400,
            days=30,
        )
        gave = await self.give(csrf)
        self.assertEqual(gave.json(), {"action": "grant", "name": "Sam"})
        await self.give(csrf, discordId=OTHER)  # a session that has expired: no name
        listed = (await self.client.get("/admin/grants")).json()
        names = {g["discordId"]: g["name"] for g in listed["grants"]}
        self.assertEqual(names, {FRIEND: "Sam", OTHER: None})
        self.assertEqual({e["discordId"]: e["name"] for e in listed["log"]}, names)
        # Only the ids on the page are looked up: nobody else's name is read.
        stranger = DiscordUser(id=int(ALWAYS) + 1, name="Stranger", email=None)
        await sessions.sign_in(self.db, stranger, [], now=NOW - 100, days=30)
        text = (await self.client.get("/admin/grants")).text
        self.assertNotIn("Stranger", text)

    async def test_every_grants_route_is_not_there_while_the_admin_page_is_off(self) -> None:
        app = create_app(settings(), self.db, FakeDiscord())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        ) as client:
            self.assertEqual((await client.get("/admin/grants")).status_code, 404)
            for path in ("/admin/grants", f"/admin/grants/{FRIEND}/revoke"):
                answer = await client.post(path, json={}, headers=HEADERS)
                self.assertEqual((answer.status_code, answer.json()), (404, {"error": "not_found"}))
