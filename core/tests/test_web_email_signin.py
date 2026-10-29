"""Website sign-in by emailed link, the account number and personal links (#1231), as the
website's own database role. What counts as an email needs no database. The DMbot ID and the
link tokens are tested with their own code (#1228)."""

from __future__ import annotations

import asyncio
import re
import unittest
from email.message import EmailMessage

import httpx

from dmbot.account_id import to_dmbot_id
from dmbot.db import Database
from dmbot.entitlements import Entitlement
from dmbot.link_tokens import make_link_token
from dmbot.mail import Mailer, MailSettings
from dmbot.web import email_signin
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordUser
from dmbot.web.limits import LIMITS, RateLimit
from dmbot.web.moving import MoveOutcome, can_move
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
HEADERS = {"X-DMbot-Request": "1"}


def email_of(user: DiscordUser) -> str:
    assert user.email is not None
    return user.email


SAME_ANSWER = {"message": "If that email belongs to a DMbot account, we've sent a link"}


class CleanEmail(unittest.TestCase):
    def test_an_address_is_trimmed_and_lowered(self) -> None:
        self.assertEqual(email_signin.clean_email("  Alice@Example.COM "), "alice@example.com")

    def test_what_cannot_be_an_address_is_none(self) -> None:
        for raw in (
            None,
            5,
            "",
            "alice",
            "a@b",
            "@example.com",
            "a@@example.com",
            "a b@example.org",
        ):
            self.assertIsNone(email_signin.clean_email(raw), repr(raw))
        self.assertIsNone(email_signin.clean_email("a@" + "x" * 260 + ".example.org"))


class CanMove(unittest.TestCase):
    """One rule for who may ask for a move code (`/me` and the route both use it)."""

    def plan(self, plan: str, status: str = "active", end: int = 2_000) -> Entitlement:
        return Entitlement(
            user_id=1,
            plan=plan,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            hours_cap=18,
            extra_hours=0,
            campaign_cap=1,
            period_start=0,
            period_end=end,
            grace_ends_at=None,
            lapsed_at=None,
            plan_changed_at=0,
        )

    def test_an_active_paid_plan_may(self) -> None:
        for paid in ("table", "two-tables", "guild"):
            self.assertTrue(can_move(self.plan(paid), 1_000), paid)

    def test_try_it_and_its_extension_may_not(self) -> None:
        # An extension is more days of the same `try-it` plan (#1229), so it fails the same way.
        self.assertFalse(can_move(self.plan("try-it"), 1_000))
        self.assertFalse(can_move(self.plan("try-it", end=10_000_000), 1_000))

    def test_no_plan_a_lapsed_or_grace_or_ended_plan_may_not(self) -> None:
        self.assertFalse(can_move(None, 1_000))
        self.assertFalse(can_move(self.plan("table", "lapsed"), 1_000))
        self.assertFalse(can_move(self.plan("table", "grace"), 1_000))
        self.assertFalse(can_move(self.plan("table"), 10_000_000_000))


class FakeTransport:
    """The mail server: records (address, link) for what would have been sent. Addresses in
    tests are example.com or example.org only."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False
        self.gate: asyncio.Event | None = None  # when set, sending waits for it

    async def __call__(self, message: EmailMessage) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("the mail server is down")
        body = message.get_content()
        link = re.search(r"https://\S+", body)
        assert link is not None
        self.sent.append((str(message["To"]), link.group(0)))


LINK_SECRET = b"s" * 40
SMTP = MailSettings(
    host="smtp.example.org",
    user="dmbot@example.org",
    app_password="app-password",
    sender="dmbot@example.org",
)


class FakeMoveCodes:
    def __init__(self) -> None:
        self.asked: list[int] = []

    async def send_code(self, user_id: int, source: str) -> MoveOutcome:
        self.asked.append(user_id)
        return "sent"


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
        self.mailer = FakeTransport()
        self.sender = Mailer(
            self.web,
            SMTP,
            transport=self.mailer,
            site_url=SITE,
            link_secret=LINK_SECRET,
            clock=lambda: float(self.now),
        )
        self.moves = FakeMoveCodes()
        self.limits = {
            name: RateLimit(group.seconds, clock=lambda: float(self.now), count=group.count)
            for name, group in LIMITS.items()
        }
        self.email_limit = RateLimit(3600, clock=lambda: float(self.now), count=3)
        self.app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=self.limits,
            email_limit=self.email_limit,
            mailer=self.sender,
            move_codes=self.moves,
            clock=lambda: self.now,
        )

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    def browser(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )

    async def account(self, user: DiscordUser) -> None:
        """The person has signed in with Discord before: that is what makes an account."""
        await sessions_sign_in(self.web, user, self.now)

    async def settle(self) -> None:
        """The link is made and mailed after the answer goes: wait for that work to finish."""
        await asyncio.gather(*list(self.app.state.email_tasks))

    async def ask(self, client: httpx.AsyncClient, email: str) -> httpx.Response:
        answer = await client.post("/auth/email/request", json={"email": email}, headers=HEADERS)
        await self.settle()
        return answer

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

    async def set_payment_email(self, user: DiscordUser, address: str) -> None:
        async with self.db.user(user.id) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = %s WHERE user_id = %s", (address, user.id)
            )

    async def test_the_payment_email_signs_in_too(self) -> None:
        # The address the payment company has may differ from Discord's (#1242).
        await self.account(ALICE)
        await self.set_payment_email(ALICE, "pays@example.org")
        async with self.browser() as client:
            answer = await self.ask(client, "Pays@Example.org")
        self.assertEqual(answer.json(), SAME_ANSWER)
        self.assertEqual([to for to, _ in self.mailer.sent], ["pays@example.org"])

    async def test_a_discord_email_that_is_anothers_payment_email_finds_neither(self) -> None:
        await self.account(ALICE)
        await self.account(BOB)
        await self.set_payment_email(BOB, email_of(ALICE))
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
        self.assertEqual(self.mailer.sent, [])  # two accounts: no link to the wrong person

    async def test_an_address_two_accounts_share_gets_no_link(self) -> None:
        await self.account(ALICE)
        await self.account(DiscordUser(id=3003, name="Twin", email="twin@example.com"))
        # (a Discord sign-in no longer brings in a taken address, #2366; this is an old shared one)
        async with self.db.user(3003) as conn:
            await conn.execute(
                "UPDATE web_users SET email = %s WHERE user_id = 3003", (email_of(ALICE),)
            )
        async with self.browser() as client:
            answer = await self.ask(client, email_of(ALICE))
        self.assertEqual(answer.json(), SAME_ANSWER)
        self.assertEqual(self.mailer.sent, [])

    async def test_the_answer_does_not_wait_for_the_mail(self) -> None:
        # Mailing takes time only for an address with an account, so the answer must not wait
        # for it, or the speed would tell anyone whether an address has an account.
        await self.account(ALICE)
        self.mailer.gate = asyncio.Event()
        async with self.browser() as client:
            answer = await asyncio.wait_for(
                client.post(
                    "/auth/email/request", json={"email": email_of(ALICE)}, headers=HEADERS
                ),
                timeout=5,
            )
            self.assertEqual(answer.json(), SAME_ANSWER)
            self.mailer.gate.set()
            await self.settle()
        self.assertEqual(len(self.mailer.sent), 1)

    async def test_a_full_limiter_does_not_stop_links_for_everyone(self) -> None:
        await self.account(ALICE)
        tiny = RateLimit(3600, clock=lambda: float(self.now), count=3, most=1)
        tiny.take("someone-else")  # the limiter is full of another address
        app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            email_limit=tiny,
            mailer=self.sender,
            clock=lambda: self.now,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        ) as c:
            answer = await c.post(
                "/auth/email/request", json={"email": email_of(ALICE)}, headers=HEADERS
            )
            await asyncio.gather(*list(app.state.email_tasks))
        self.assertEqual(answer.json(), SAME_ANSWER)
        self.assertEqual(len(self.mailer.sent), 1)  # the link went anyway

    async def test_no_link_is_made_when_there_is_no_mailer(self) -> None:
        await self.account(ALICE)
        app = create_app(
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: self.now,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        ) as c:
            answer = await c.post(
                "/auth/email/request", json={"email": email_of(ALICE)}, headers=HEADERS
            )
            await asyncio.gather(*list(app.state.email_tasks))
        self.assertEqual(answer.json(), SAME_ANSWER)
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM email_sign_in_links")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # nothing could be sent, so nothing was made

    async def test_two_asks_at_once_leave_one_live_link(self) -> None:
        await self.account(ALICE)
        async with self.browser() as one, self.browser() as two:
            await asyncio.gather(
                one.post("/auth/email/request", json={"email": email_of(ALICE)}, headers=HEADERS),
                two.post("/auth/email/request", json={"email": email_of(ALICE)}, headers=HEADERS),
            )
            await self.settle()
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM email_sign_in_links WHERE used_at IS NULL"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # the row lock made the second ask wait for the first

    async def test_asking_again_spends_the_earlier_link(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            await self.ask(client, email_of(ALICE))
            first, second = (token_in(link) for _to, link in self.mailer.sent)
            old = await client.post("/auth/email/verify", json={"token": first}, headers=HEADERS)
            self.assertEqual(old.status_code, 400)  # only the newest link is live
            new = await client.post("/auth/email/verify", json={"token": second}, headers=HEADERS)
            self.assertEqual(new.status_code, 204)

    async def test_each_link_is_its_own_email_in_the_log_and_no_address_is_kept(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            await self.ask(client, email_of(ALICE))
            self.now += 60
            await self.ask(client, email_of(ALICE))
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT user_id, kind FROM sent_emails ORDER BY sent_at")
            rows = await cur.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["kind"].startswith("sign-in:") for r in rows))
        self.assertNotEqual(rows[0]["kind"], rows[1]["kind"])
        self.assertTrue(all(r["user_id"] == ALICE.id for r in rows))
        self.assertNotIn("example.com", repr(rows))  # ids and kinds only, never an address

    async def test_a_chunked_or_huge_body_is_just_no_email(self) -> None:
        await self.account(ALICE)
        async with self.browser() as client:
            big = await client.post(
                "/auth/email/request",
                content=b'{"email": "' + b"a" * 5000 + b'"}',
                headers=HEADERS | {"Content-Type": "application/json"},
            )
        self.assertEqual((big.status_code, big.json()), (202, SAME_ANSWER))
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
        token = make_link_token(uid, self.now, LINK_SECRET)
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
        stolen = make_link_token(to_dmbot_id(BOB.id), self.now, LINK_SECRET)
        for changes in (
            {"t": ""},
            {"t": "1.2"},
            {"t": stolen},  # Bob's code on Alice's number
            {"t": "1.é"},  # not even ASCII
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
                    "t": make_link_token(to_dmbot_id(ALICE.id), 1_800_000_000, LINK_SECRET),
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
            settings(link_token_secret=LINK_SECRET),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            mailer=self.sender,
            clock=lambda: self.now,
        )
        async with self.browser() as signing_in:
            await self.sign_in_by_email(signing_in, ALICE)  # a browser of the main app
            cookies = signing_in.cookies
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            cookies=cookies,
        ) as client:
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
