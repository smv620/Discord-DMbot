"""Changing the email DMbot uses (#2349): a link to the NEW address, confirmed by the same signed-in
account, one use and 15 minutes; a payment email is changed in Stripe; an address another account
uses is refused; the old address is told. As the website's own database role."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import psycopg

from dmbot.db import Database
from dmbot.mail import Mailer
from dmbot.web import email_change, email_signin, sessions
from dmbot.web.app import create_app
from dmbot.web.limits import LIMITS, RateLimit
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_email_signin import (
    BOB,
    HEADERS,
    LINK_SECRET,
    SMTP,
    FakeMoveCodes,
    FakeTransport,
    email_of,
)
from tests.test_web_role import ensure_web_role, web_url

NEW = "new-address@example.org"


def confirm_token(link: str) -> str:
    found = re.search(r"#email-confirm=([\w-]+)$", link)
    assert found is not None, link
    return found.group(1)


class Changing(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.transport = FakeTransport()
        self.sender = Mailer(
            self.web,
            SMTP,
            transport=self.transport,
            site_url=SITE,
            link_secret=LINK_SECRET,
            clock=lambda: float(self.now),
        )
        limits = {
            name: RateLimit(group.seconds, clock=lambda: float(self.now), count=group.count)
            for name, group in LIMITS.items()
        }
        self.app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=limits,
            mailer=self.sender,
            move_codes=FakeMoveCodes(),
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def person(self, user: Any) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def exist(self, *users: Any) -> None:
        for user in users:
            await sessions.sign_in(self.web, user, [THURSDAY], now=self.now, days=30)

    async def email_of(self, user: Any) -> str | None:
        async with self.db.user(user.id) as conn:
            cur = await conn.execute("SELECT email FROM web_users WHERE user_id = %s", (user.id,))
            row = await cur.fetchone()
        return None if row is None else row["email"]

    async def test_the_page_reads_the_address_and_where_it_came_from(self) -> None:
        async with await self.person(ALICE) as client:
            got = (await client.get("/me/email")).json()
            self.assertEqual(got, {"address": email_of(ALICE), "source": "signin", "pending": None})
            async with self.db.user(ALICE.id) as conn:
                await conn.execute(
                    "UPDATE web_users SET payment_email = 'pays@example.org' WHERE user_id = %s",
                    (ALICE.id,),
                )
            got = (await client.get("/me/email")).json()
            self.assertEqual((got["address"], got["source"]), ("pays@example.org", "payment"))

    async def test_nothing_changes_until_the_link_is_confirmed(self) -> None:
        async with await self.person(ALICE) as client:
            asked = await client.put("/me/email", json={"address": NEW.upper()})
            self.assertEqual((asked.status_code, asked.json()), (200, {"pending": NEW}))
            self.assertEqual(await self.email_of(ALICE), email_of(ALICE))  # still the old one
            self.assertEqual([to for to, _ in self.transport.sent], [NEW])
            self.assertEqual((await client.get("/me/email")).json()["pending"], NEW)
            token = confirm_token(self.transport.sent[0][1])
            done = await client.post("/me/email/confirm", json={"token": token})
            self.assertEqual((done.status_code, done.json()), (200, {"address": NEW}))
            self.assertEqual(await self.email_of(ALICE), NEW)
            # the old address is told, and the link is spent
            self.assertEqual(self.transport.sent[-1][0], email_of(ALICE))
            again = await client.post("/me/email/confirm", json={"token": token})
            self.assertEqual((again.status_code, again.json()["error"]), (400, "bad_link"))

    async def test_a_link_is_for_the_account_that_asked_and_ends_in_15_minutes(self) -> None:
        async with await self.person(ALICE) as alice, await self.person(BOB) as bob:
            await alice.put("/me/email", json={"address": NEW})
            token = confirm_token(self.transport.sent[0][1])
            wrong = await bob.post("/me/email/confirm", json={"token": token})
            self.assertEqual((wrong.status_code, wrong.json()["error"]), (400, "bad_link"))
            self.assertEqual(await self.email_of(ALICE), email_of(ALICE))
            self.now += email_signin.LINK_SECONDS + 1
            late = await alice.post("/me/email/confirm", json={"token": token})
            self.assertEqual((late.status_code, late.json()["error"]), (400, "bad_link"))
            self.assertEqual(await self.email_of(ALICE), email_of(ALICE))

    async def test_a_new_ask_replaces_the_waiting_one_and_cancel_stops_it(self) -> None:
        async with await self.person(ALICE) as client:
            await client.put("/me/email", json={"address": NEW})
            first = confirm_token(self.transport.sent[0][1])
            await client.put("/me/email", json={"address": "other-new@example.org"})
            old = await client.post("/me/email/confirm", json={"token": first})
            self.assertEqual(old.status_code, 400)  # the first link no longer works
            cancelled = await client.delete("/me/email/pending")
            self.assertEqual(cancelled.status_code, 204)
            self.assertIsNone((await client.get("/me/email")).json()["pending"])
            second = confirm_token(self.transport.sent[1][1])
            self.assertEqual(
                (await client.post("/me/email/confirm", json={"token": second})).status_code, 400
            )

    async def test_bad_addresses_a_payment_email_and_another_accounts_address_are_refused(
        self,
    ) -> None:
        async with await self.person(ALICE) as alice, await self.person(BOB) as bob:
            bad = await alice.put("/me/email", json={"address": "not an email"})
            self.assertEqual((bad.status_code, bad.json()["error"]), (400, "bad_email"))
            taken = await alice.put("/me/email", json={"address": email_of(BOB)})
            self.assertEqual((taken.status_code, taken.json()["error"]), (409, "in_use"))
            same = await alice.put("/me/email", json={"address": email_of(ALICE)})
            self.assertEqual((same.status_code, same.json()), (200, {"pending": None}))
            async with self.db.user(BOB.id) as conn:
                await conn.execute(
                    "UPDATE web_users SET payment_email = 'pays@example.org' WHERE user_id = %s",
                    (BOB.id,),
                )
            paid = await bob.put("/me/email", json={"address": NEW})
            self.assertEqual((paid.status_code, paid.json()["error"]), (403, "not_allowed"))
            self.assertEqual(self.transport.sent, [])  # none of these sent anything

    async def test_a_person_cannot_use_a_signed_out_request_or_see_another_address(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        ) as nobody:
            for call in (
                nobody.get("/me/email"),
                nobody.put("/me/email", json={"address": NEW}),
                nobody.post("/me/email/confirm", json={"token": "x"}),
                nobody.delete("/me/email/pending"),
            ):
                self.assertEqual((await call).status_code, 401)
        async with await self.person(ALICE) as alice, await self.person(BOB) as bob:
            await bob.put("/me/email", json={"address": NEW})
            self.assertIsNone((await alice.get("/me/email")).json()["pending"])
            self.assertNotIn(NEW, (await alice.get("/me/email")).text)

    async def test_the_asks_are_limited(self) -> None:
        async with await self.person(ALICE) as client:
            codes = []
            for n in range(8):
                got = await client.put("/me/email", json={"address": f"try{n}@example.org"})
                codes.append(got.status_code)
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 200)

    async def test_a_mail_that_cannot_be_sent_leaves_nothing_waiting(self) -> None:
        self.transport.fail = True
        async with await self.person(ALICE) as client:
            got = await client.put("/me/email", json={"address": NEW})
            self.assertEqual((got.status_code, got.json()["error"]), (502, "email_failed"))
            self.assertIsNone((await client.get("/me/email")).json()["pending"])

    async def test_a_discord_sign_in_keeps_the_address_the_person_chose(self) -> None:
        async with await self.person(ALICE) as client:
            await client.put("/me/email", json={"address": NEW})
            await client.post(
                "/me/email/confirm", json={"token": confirm_token(self.transport.sent[0][1])}
            )
        await sessions.sign_in(self.web, ALICE, [THURSDAY], now=self.now + 60, days=30)
        self.assertEqual(await self.email_of(ALICE), NEW)  # Discord's address is not put back
        await sessions.sign_in(self.web, BOB, [THURSDAY], now=self.now + 60, days=30)
        self.assertEqual(await self.email_of(BOB), email_of(BOB))  # unchosen: Discord's, as before

    async def test_two_accounts_cannot_confirm_the_same_address_at_once(self) -> None:
        await self.exist(ALICE, BOB)
        asked = await email_change.ask(self.web, BOB.id, NEW, now=self.now)
        assert isinstance(asked, tuple)
        # Alice is half way through taking the address (checked, not yet written) when Bob's
        # confirm starts: Bob must wait for her and then find it taken.
        async with self.web.user(ALICE.id) as conn:
            free = await conn.execute("SELECT dmbot_email_taken(%s) AS t", (NEW,))
            self.assertIs((await free.fetchone())["t"], False)
            bobs = asyncio.create_task(
                email_change.confirm(self.web, BOB.id, asked[1], now=self.now)
            )
            await asyncio.sleep(0.5)
            await conn.execute(
                "UPDATE web_users SET email = %s, email_chosen_at = %s WHERE user_id = %s",
                (NEW, self.now, ALICE.id),
            )
        self.assertEqual(await bobs, "in_use")
        self.assertEqual(await self.email_of(BOB), email_of(BOB))

    async def test_an_account_sees_and_changes_nothing_of_anothers_waiting_change(self) -> None:
        await self.exist(ALICE, BOB)
        await email_change.ask(self.web, BOB.id, NEW, now=self.now)
        async with self.web.user(ALICE.id) as conn:
            seen = await conn.execute("SELECT 1 FROM email_changes WHERE user_id = %s", (BOB.id,))
            self.assertEqual(await seen.fetchall(), [])
            moved = await conn.execute(
                "UPDATE email_changes SET new_email = 'x@example.org' WHERE user_id = %s",
                (BOB.id,),
            )
            self.assertEqual(moved.rowcount, 0)
            gone = await conn.execute("DELETE FROM email_changes WHERE user_id = %s", (BOB.id,))
            self.assertEqual(gone.rowcount, 0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.web.user(ALICE.id) as conn:
                await conn.execute(
                    "INSERT INTO email_changes (user_id, new_email, token_hash, created_at,"
                    " expires_at) VALUES (%s, 'y@example.org', 'h', 1, 2)",
                    (BOB.id,),
                )
        self.assertEqual((await email_change.read(self.web, BOB.id, now=self.now)).pending, NEW)

    async def test_the_taken_check_needs_a_signed_in_person_and_ignores_their_own_address(
        self,
    ) -> None:
        await self.exist(ALICE, BOB)
        async with self.web.anonymous() as conn:
            cur = await conn.execute("SELECT dmbot_email_taken(%s) AS t", (email_of(BOB),))
            self.assertIsNone((await cur.fetchone())["t"])
        async with self.web.user(ALICE.id) as conn:
            mine = await conn.execute("SELECT dmbot_email_taken(%s) AS t", (email_of(ALICE),))
            self.assertIs((await mine.fetchone())["t"], False)
            theirs = await conn.execute("SELECT dmbot_email_taken(%s) AS t", (email_of(BOB),))
            self.assertIs((await theirs.fetchone())["t"], True)

    async def test_confirming_ends_the_sign_in_links_already_sent(self) -> None:
        await self.exist(ALICE, BOB)
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO email_sign_in_links (token_hash, user_id, created_at, expires_at)"
                " VALUES ('old-link', %s, %s, %s)",
                (ALICE.id, self.now, self.now + 900),
            )
        asked = await email_change.ask(self.web, ALICE.id, NEW, now=self.now)
        assert isinstance(asked, tuple)
        await email_change.confirm(self.web, ALICE.id, asked[1], now=self.now)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM email_sign_in_links WHERE user_id = %s", (ALICE.id,)
            )
            self.assertEqual((await cur.fetchone())["n"], 0)

    async def test_a_waiting_change_is_gone_from_the_page_after_15_minutes(self) -> None:
        async with await self.person(ALICE) as client:
            await client.put("/me/email", json={"address": NEW})
            self.now += email_signin.LINK_SECONDS + 1
            self.assertIsNone((await client.get("/me/email")).json()["pending"])
