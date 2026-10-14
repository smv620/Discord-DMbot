"""Website sign-in by emailed link, the account number and personal links (#1231), as the
website's own database role. The pure parts (the DMbot ID, the signed link code, what counts as
an email) need no database."""

from __future__ import annotations

import re
import unittest

import httpx

from dmbot.db import Database
from dmbot.web import email_signin
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.limits import LIMITS, RateLimit
from dmbot.web.payments import FakeProvider
from dmbot.web.personal import (
    check_link_token,
    from_dmbot_id,
    link_secret,
    make_link_token,
    to_dmbot_id,
)
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, SECRET, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
HEADERS = {"X-DMbot-Request": "1"}


def email_of(user: DiscordUser) -> str:
    assert user.email is not None
    return user.email


SAME_ANSWER = {"message": "If that email belongs to a DMbot account, we've sent a link"}


class DmbotId(unittest.TestCase):
    def test_a_discord_id_round_trips(self) -> None:
        for number in (1, 35, 36, 1001, 1_500_000_000_000_000_000):
            self.assertEqual(from_dmbot_id(to_dmbot_id(number)), number)
        self.assertEqual(to_dmbot_id(36), "10")
        self.assertEqual(to_dmbot_id(35), "Z")

    def test_todays_largest_ids_fit_in_twelve_characters(self) -> None:
        self.assertLessEqual(len(to_dmbot_id(1_500_000_000_000_000_000)), 12)
        self.assertEqual(len(to_dmbot_id(36**12 - 1)), 12)
        self.assertEqual(len(to_dmbot_id(36**12)), 13)

    def test_what_is_not_an_id_reads_as_none(self) -> None:
        for text in ("", "abc", "AB-C", "A" * 13, "0", "000"):
            self.assertIsNone(from_dmbot_id(text), text)

    def test_zero_and_negative_have_no_id(self) -> None:
        for number in (0, -5):
            with self.assertRaises(ValueError):
                to_dmbot_id(number)


class LinkTokens(unittest.TestCase):
    secret = link_secret(SECRET)

    def test_a_fresh_token_for_its_id_is_ok(self) -> None:
        token = make_link_token(self.secret, "ABC123", 1000)
        self.assertTrue(check_link_token(self.secret, "ABC123", token, 1001))

    def test_another_id_a_tampered_expiry_or_another_key_is_not(self) -> None:
        token = make_link_token(self.secret, "ABC123", 1000)
        self.assertFalse(check_link_token(self.secret, "ABC124", token, 1001))
        expires, mac = token.split(".")
        later = f"{int(expires) + 10_000_000}.{mac}"  # a longer life with the old signature
        self.assertFalse(check_link_token(self.secret, "ABC123", later, 1001))
        self.assertFalse(check_link_token(link_secret(b"z" * 40), "ABC123", token, 1001))

    def test_it_expires_after_thirty_days(self) -> None:
        token = make_link_token(self.secret, "ABC123", 1000)
        self.assertTrue(check_link_token(self.secret, "ABC123", token, 1000 + 30 * 86400 - 1))
        self.assertFalse(check_link_token(self.secret, "ABC123", token, 1000 + 30 * 86400))

    def test_junk_is_just_no(self) -> None:
        for junk in ("", ".", "x.y", "1.2.3", "99999999999.", "not a token"):
            self.assertFalse(check_link_token(self.secret, "ABC123", junk, 1001), junk)


class CleanEmail(unittest.TestCase):
    def test_an_address_is_trimmed_and_lowered(self) -> None:
        self.assertEqual(email_signin.clean_email("  Alice@Example.COM "), "alice@example.com")

    def test_what_cannot_be_an_address_is_none(self) -> None:
        for raw in (None, 5, "", "alice", "a@b", "@example.com", "a@@example.com", "a b@x.com"):
            self.assertIsNone(email_signin.clean_email(raw), repr(raw))
        self.assertIsNone(email_signin.clean_email("a@" + "x" * 260 + ".com"))


class FakeMailer:
    """Records what would have been sent: (address, link). Tests use example.com only."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    async def send_sign_in_link(self, to: str, link: str) -> None:
        if self.fail:
            raise RuntimeError("the mail server is down")
        self.sent.append((to, link))


class FakeMoveCodes:
    def __init__(self) -> None:
        self.asked: list[int] = []

    async def send_code(self, user_id: int) -> None:
        self.asked.append(user_id)


def token_in(link: str) -> str:
    found = re.search(r"#email-link=([\w-]+)$", link)
    assert found is not None, link
    return found.group(1)


class EmailSignIn(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.mailer = FakeMailer()
        self.moves = FakeMoveCodes()
        self.limits = {
            name: RateLimit(group.seconds, clock=lambda: float(self.now), count=group.count)
            for name, group in LIMITS.items()
        }
        self.email_limit = RateLimit(3600, clock=lambda: float(self.now), count=3)
        self.app = create_app(
            settings(),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=self.limits,
            email_limit=self.email_limit,
            mailer=self.mailer,
            move_codes=self.moves,
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    def browser(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )

    async def account(self, user: DiscordUser) -> None:
        """The person has signed in with Discord before: that is what makes an account."""
        await sessions_sign_in(self.web, user, self.now)

    async def ask(self, client: httpx.AsyncClient, email: str) -> httpx.Response:
        return await client.post("/auth/email/request", json={"email": email}, headers=HEADERS)

    async def test_a_known_address_gets_a_link_and_an_unknown_one_gets_the_same_words(
        self,
    ) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            known = await self.ask(client, "Belleros@example.com")  # any case
            unknown = await self.ask(client, "nobody@example.com")
            junk = await self.ask(client, "not an email")
        self.assertEqual((known.status_code, known.json()), (202, SAME_ANSWER))
        self.assertEqual((unknown.status_code, unknown.json()), (202, SAME_ANSWER))
        self.assertEqual((junk.status_code, junk.json()), (202, SAME_ANSWER))
        self.assertEqual([to for to, _ in self.mailer.sent], ["belleros@example.com"])
        self.assertTrue(self.mailer.sent[0][1].startswith(f"{SITE}/account#email-link="))

    async def test_an_address_two_accounts_share_gets_no_link(self) -> None:
        await self.account(ALICE)
        await self.account(DiscordUser(id=3003, name="Twin", email=email_of(ALICE)))
        async with self.browser() as client:
            answer = await self.ask(client, email_of(ALICE))
        self.assertEqual(answer.json(), SAME_ANSWER)
        self.assertEqual(self.mailer.sent, [])

    async def test_a_mail_error_still_gets_the_same_words(self) -> None:
        await self.account(ALICE)
        self.mailer.fail = True
        async with self.browser() as client:
            answer = await self.ask(client, email_of(ALICE))
        self.assertEqual((answer.status_code, answer.json()), (202, SAME_ANSWER))

    async def test_the_link_signs_in_once_with_the_same_cookie_as_discord(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            token = token_in(self.mailer.sent[0][1])
            used = await client.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
            self.assertEqual(used.status_code, 204)
            me = (await client.get("/me")).json()
            self.assertEqual(me["user"], {"id": str(ALICE.id), "name": ALICE.name})
            self.assertEqual(me["servers"], [])  # no Discord token, so no server list
            again = await client.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
            self.assertEqual((again.status_code, again.json()), (400, {"error": "bad_link"}))

    async def test_two_tabs_racing_with_one_link_get_one_winner(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            token = token_in(self.mailer.sent[0][1])
        async with self.browser() as one, self.browser() as two:
            first = await one.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
            second = await two.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
        self.assertEqual(sorted([first.status_code, second.status_code]), [204, 400])

    async def test_a_link_ends_after_fifteen_minutes(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            token = token_in(self.mailer.sent[0][1])
            self.now += 15 * 60
            late = await client.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
            self.assertEqual(late.status_code, 400)
            self.assertEqual((await client.get("/me")).status_code, 401)

    async def test_a_wrong_link_signs_nobody_in(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            for token in ("", "nope", "x" * 500, None, 5):
                answer = await client.post(
                    "/auth/email/verify", json={"token": token}, headers=HEADERS
                )
                self.assertEqual(answer.status_code, 400, repr(token))
            self.assertEqual((await client.get("/me")).status_code, 401)

    async def test_her_link_signs_in_her_and_his_signs_in_him(self) -> None:
        await self.account(ALICE)
        await self.account(BOB)
        async with self.browser() as a, self.browser() as b:
            await self.ask(a, email_of(ALICE))
            await self.ask(b, email_of(BOB))
            by_address = dict(self.mailer.sent)
            await a.post(
                "/auth/email/verify",
                json={"token": token_in(by_address[email_of(ALICE)])},
                headers=HEADERS,
            )
            await b.post(
                "/auth/email/verify",
                json={"token": token_in(by_address[email_of(BOB)])},
                headers=HEADERS,
            )
            self.assertEqual((await a.get("/me")).json()["user"]["id"], str(ALICE.id))
            self.assertEqual((await b.get("/me")).json()["user"]["id"], str(BOB.id))

    async def test_three_asks_an_hour_for_one_address_then_silence_in_the_same_words(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            answers = [await self.ask(client, email_of(ALICE)) for _ in range(5)]
        self.assertTrue(all(a.status_code == 202 and a.json() == SAME_ANSWER for a in answers))
        self.assertEqual(len(self.mailer.sent), 3)  # the 4th and 5th were quietly not sent
        self.now += 3600
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
        self.assertEqual(len(self.mailer.sent), 4)

    async def test_one_connection_is_limited_to_ten_asks_in_ten_minutes(self) -> None:
        async with self.browser() as client:
            answers = [
                await self.ask(client, f"person{n}@example.com")
                for n in range(LIMITS["email-sign-in"].count + 1)
            ]
        self.assertEqual([a.status_code for a in answers[:-1]], [202] * 10)
        self.assertEqual(answers[-1].status_code, 429)
        self.assertIn("retry-after", answers[-1].headers)

    async def test_it_needs_the_site_header(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            plain = await client.post("/auth/email/request", json={"email": email_of(ALICE)})
            token_less = await client.post("/auth/email/verify", json={"token": "x"})
        self.assertEqual((plain.status_code, token_less.status_code), (403, 403))
        self.assertEqual(self.mailer.sent, [])

    async def test_the_database_shows_nobody_anyone_elses_links(self) -> None:
        await self.account(ALICE)
        await self.account(BOB)
        await email_signin.make_link(self.web, email_of(BOB), now=self.now)
        async with self.web.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM email_sign_in_links")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # Bob's is not hers to see
            cur = await conn.execute(
                "UPDATE email_sign_in_links SET used_at = 1 WHERE user_id = %s", (BOB.id,)
            )
            self.assertEqual(cur.rowcount, 0)
        async with self.web.anonymous() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM email_sign_in_links")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # with no one set, nothing shows

    async def test_the_website_cannot_open_the_email_door_for_itself(self) -> None:
        # The lookup policy is for the function's owner only: the website's role setting the
        # same setting still sees no one's email, with nobody set or with someone set.
        await self.account(ALICE)
        await self.account(BOB)
        async with self.web.anonymous() as conn:
            await conn.execute("SELECT set_config('dmbot.email_lookup', 'on', true)")
            cur = await conn.execute("SELECT count(*) AS n FROM web_users")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)
        async with self.web.user(ALICE.id) as conn:
            await conn.execute("SELECT set_config('dmbot.email_lookup', 'on', true)")
            cur = await conn.execute("SELECT user_id FROM web_users")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE.id])

    async def test_only_the_hash_is_kept(self) -> None:
        await self.account(ALICE)
        made = await email_signin.make_link(self.web, email_of(ALICE), now=self.now)
        assert made is not None
        _user_id, token = made
        async with self.db.user(ALICE.id) as conn:  # her own row (forced row-level security)
            cur = await conn.execute("SELECT token_hash FROM email_sign_in_links")
            rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(token, rows[0]["token_hash"])
        self.assertEqual(len(rows[0]["token_hash"]), 64)

    async def test_old_and_spent_links_are_cleared_when_a_new_one_is_made(self) -> None:
        await self.account(ALICE)
        await email_signin.make_link(self.web, email_of(ALICE), now=self.now)
        self.now += 20 * 60
        await email_signin.make_link(self.web, email_of(ALICE), now=self.now)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM email_sign_in_links")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)

    async def test_me_has_the_account_number_and_experience_points(self) -> None:
        await self.account(ALICE)
        for month, minutes in ((1000, 95), (2000, 40)):
            async with self.db.meter(THURSDAY.id, ALICE.id) as conn:
                await conn.execute(
                    "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                    " VALUES (%s, %s, %s)",
                    (ALICE.id, month, minutes),
                )
        async with self.db.meter(THURSDAY.id, BOB.id) as conn:
            await conn.execute(
                "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                " VALUES (%s, 1000, 700)",
                (BOB.id,),
            )
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            token = token_in(self.mailer.sent[0][1])
            await client.post("/auth/email/verify", json={"token": token}, headers=HEADERS)
            me = (await client.get("/me")).json()
        self.assertEqual(me["dmbotId"], to_dmbot_id(ALICE.id))
        self.assertEqual(me["experiencePoints"], 135)  # hers, all months, never his 700
        self.assertFalse(me["canMove"])  # no paid plan

    async def personal(self, user: DiscordUser, **changes: str) -> httpx.Response:
        uid = to_dmbot_id(user.id)
        token = make_link_token(link_secret(SECRET), uid, self.now)
        query = {"uid": uid, "t": token} | changes
        async with self.browser() as client:
            return await client.get("/personal", params=query)

    async def test_a_valid_personal_link_shows_the_name_and_points(self) -> None:
        await self.account(ALICE)
        async with self.db.meter(THURSDAY.id, ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                " VALUES (%s, 1000, 61)",
                (ALICE.id,),
            )
        answer = await self.personal(ALICE)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"name": ALICE.name, "experiencePoints": 61})

    async def test_every_wrong_personal_link_is_the_same_404(self) -> None:
        await self.account(ALICE)
        await self.account(BOB)
        stolen = make_link_token(link_secret(SECRET), to_dmbot_id(BOB.id), self.now)
        for changes in (
            {"t": ""},
            {"t": "1.2"},
            {"t": stolen},  # Bob's code on Alice's number
            {"uid": "ZZZZZZZZZZZZZ"},  # not an ID at all
            {"uid": to_dmbot_id(9_999_999)},  # a number with no account (and a bad code)
        ):
            answer = await self.personal(ALICE, **changes)
            self.assertEqual((answer.status_code, answer.json()), (404, {"error": "not_found"}))
        self.now += 31 * 86400
        async with self.browser() as client:
            old = await client.get(
                "/personal",
                params={
                    "uid": to_dmbot_id(ALICE.id),
                    "t": make_link_token(link_secret(SECRET), to_dmbot_id(ALICE.id), 1_800_000_000),
                },
            )
        self.assertEqual(old.status_code, 404)  # a code from 31 days ago

    async def test_a_signed_code_for_an_account_that_isnt_there_is_still_404(self) -> None:
        answer = await self.personal(DiscordUser(id=5555, name="Ghost", email="g@example.com"))
        self.assertEqual(answer.status_code, 404)

    async def give_plan(self, user: DiscordUser, plan: str, status: str) -> None:
        async with self.db.plan_writer(user.id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, plan_changed_at,"
                " provider, last_event_at, updated_at) VALUES (%s, %s, %s, 18, 0,"
                " 1, %s, %s, NULL, 0, 'fake', 0, 0)",
                (user.id, plan, status, self.now - 100, self.now + 86400),
            )

    async def sign_in_by_email(self, client: httpx.AsyncClient, user: DiscordUser) -> None:
        await self.ask(client, email_of(user))
        token = token_in(self.mailer.sent[-1][1])
        await client.post("/auth/email/verify", json={"token": token}, headers=HEADERS)

    async def test_a_move_code_is_for_an_active_paid_plan_only(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.sign_in_by_email(client, ALICE)
            none = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual((none.status_code, none.json()), (403, {"error": "not_paid"}))
            await self.give_plan(ALICE, "try-it", "active")
            tried = await client.post("/me/move-code", headers=HEADERS)
            self.assertEqual(tried.status_code, 403)
        self.assertEqual(self.moves.asked, [])

    async def test_a_move_code_is_asked_for_by_an_active_paid_plan(self) -> None:
        await self.account(ALICE)
        await self.give_plan(ALICE, "table", "active")
        async with self.browser() as client:
            await self.sign_in_by_email(client, ALICE)
            self.assertTrue((await client.get("/me")).json()["canMove"])
            answer = await client.post("/me/move-code", headers=HEADERS)
        self.assertEqual(answer.status_code, 202)
        self.assertNotIn("code", answer.json())  # it is emailed, never shown
        self.assertEqual(self.moves.asked, [ALICE.id])

    async def test_a_move_code_needs_a_sign_in_and_the_header(self) -> None:
        await self.account(ALICE)
        await self.give_plan(ALICE, "table", "active")
        async with self.browser() as client:
            self.assertEqual((await client.post("/me/move-code", headers=HEADERS)).status_code, 401)
            await self.sign_in_by_email(client, ALICE)
            self.assertEqual((await client.post("/me/move-code")).status_code, 403)
        self.assertEqual(self.moves.asked, [])

    async def test_with_no_move_codes_set_up_it_says_so(self) -> None:
        await self.account(ALICE)
        await self.give_plan(ALICE, "table", "active")
        app = create_app(
            settings(),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            mailer=self.mailer,
            clock=lambda: self.now,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API
        ) as client:
            await self.sign_in_by_email(client, ALICE)
            answer = await client.post("/me/move-code", headers=HEADERS)
        self.assertEqual((answer.status_code, answer.json()), (503, {"error": "move_off"}))

    async def test_a_discord_sign_in_keeps_the_name_for_personal_links(self) -> None:
        await self.account(ALICE)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT display_name FROM web_users WHERE user_id = %s", (ALICE.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["display_name"], ALICE.name)


async def sessions_sign_in(db: Database, user: DiscordUser, now: int) -> None:
    from dmbot.web import sessions

    await sessions.sign_in(db, user, [THURSDAY], now=now, days=30)
