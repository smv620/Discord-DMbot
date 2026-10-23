"""Handing a campaign over by email (#2532): the offer is saved with only a hash of the link's
token and no address, the email goes out, the owner learns nothing about the address, one offer is
open per campaign (a new one takes back the old one), five a day per owner, and a failed send leaves
nothing waiting. As the website's own database role, through the route the account page uses."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import httpx

from dmbot import handover_email
from dmbot.campaigns import CampaignStore
from dmbot.db import Database
from dmbot.mail import Mailer
from dmbot.web import sessions
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
)
from tests.test_web_role import ensure_web_role, web_url

NEW = "new.owner@example.org"
OTHER_NEW = "someone.else@example.com"


class OfferBase(DatabaseTest):
    enforce_plans = False

    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = int(time.time())  # the mail log stamps with the database clock
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
            settings(link_token_secret=LINK_SECRET, enforce_plans=self.enforce_plans),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE),
            route_limits=limits,
            mailer=self.sender,
            move_codes=FakeMoveCodes(),
            clock=lambda: self.now,
        )
        await sessions.sign_in(self.web, BOB, [THURSDAY], now=self.now, days=30)
        self.alice = await self.person(ALICE)
        self.campaign = await CampaignStore(self.db, clock=lambda: self.now).create(
            THURSDAY.id, "Frostmaiden", ALICE.id
        )
        self.ref = f"{THURSDAY.id}.{self.campaign.id}"

    async def asyncTearDown(self) -> None:
        await self.alice.aclose()
        await self.web.close()
        await super().asyncTearDown()

    async def person(self, user: Any) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def offer(self, email: str = NEW, client: httpx.AsyncClient | None = None) -> Any:
        return await (client or self.alice).post(
            f"/me/campaigns/{self.ref}/handover", json={"email": email}
        )

    async def offers(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaign_handover_offers WHERE campaign_id = %s ORDER BY id",
                (self.campaign.id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    def token_of(self, link: str) -> str:
        """What the page sends: the link's whole fragment, `<server>-<offer>-<secret>`."""
        found = re.search(r"/handover#([\w-]+)$", link)
        assert found is not None, link
        return found.group(1)

    def secret_of(self, link: str) -> str:
        return self.token_of(link).split("-", 2)[2]


class Offering(OfferBase):
    async def test_the_offer_is_emailed_and_the_owner_is_told_only_that(self) -> None:
        got = await self.offer()
        self.assertEqual((got.status_code, got.json()), (200, {"status": "emailed"}))
        [(to, link)] = self.transport.sent
        self.assertEqual(to, NEW)
        self.assertTrue(link.startswith(f"{SITE}/handover#"))
        # the reply says nothing about the address (same words for any address)
        again = await self.offer(OTHER_NEW)
        self.assertEqual(again.json(), got.json())

    async def test_the_offer_holds_a_hash_of_the_token_and_no_address(self) -> None:
        await self.offer()
        [row] = await self.offers()
        token = self.secret_of(self.transport.sent[0][1])
        self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual((row["via_email"], row["to_user_id"], row["to_name"]), (True, 0, ""))
        self.assertEqual((row["status"], row["from_user_id"]), ("open", ALICE.id))
        self.assertNotIn(NEW, str(row))  # the address is nowhere in it
        self.assertNotIn(token, str(row))

    async def test_a_new_offer_takes_back_the_old_one(self) -> None:
        await self.offer()
        await self.offer(OTHER_NEW)
        first, second = await self.offers()
        self.assertEqual((first["status"], second["status"]), ("withdrawn", "open"))
        self.assertNotEqual(first["token_hash"], second["token_hash"])

    async def test_five_offers_a_day_per_owner(self) -> None:
        async with self.db.unscoped() as conn:  # (the day's count is the mail log's: start clean)
            await conn.execute("DELETE FROM sent_emails")
        other = await CampaignStore(self.db, clock=lambda: self.now).create(
            THURSDAY.id, "Second", ALICE.id
        )
        for n in range(handover_email.OFFERS_PER_DAY):
            got = await self.offer(f"person{n}@example.org")
            self.assertEqual(got.status_code, 200, got.text)
        sixth = await self.alice.post(
            f"/me/campaigns/{THURSDAY.id}.{other.id}/handover", json={"email": NEW}
        )  # (a different campaign: the day's count is the owner's)
        self.assertEqual((sixth.status_code, sixth.json()["error"]), (429, "too_soon"))
        self.assertEqual(len(self.transport.sent), handover_email.OFFERS_PER_DAY)
        self.now += handover_email.DAY_S + 3600  # the next day (the log stamps with real time)
        self.assertEqual((await self.offer("late@example.org")).status_code, 200)

    async def test_a_bad_address_is_refused_and_nothing_is_offered(self) -> None:
        for bad in ("", "nobody", "a b@example.org", "two@@example.org", 5):
            got = await self.alice.post(f"/me/campaigns/{self.ref}/handover", json={"email": bad})
            self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_email"), bad)
        self.assertEqual(await self.offers(), [])
        self.assertEqual(self.transport.sent, [])

    async def test_a_failed_send_leaves_no_offer_waiting(self) -> None:
        self.transport.fail = True
        got = await self.offer()
        self.assertEqual((got.status_code, got.json()["error"]), (502, "email_failed"))
        [row] = await self.offers()
        self.assertEqual(row["status"], "withdrawn")  # taken back: no one was told of it

    async def test_only_the_owner_can_offer_and_only_in_their_own_servers(self) -> None:
        async with await self.person(BOB) as bob:
            got = await self.offer(client=bob)
            self.assertEqual((got.status_code, got.json()["error"]), (404, "not_found"))
        nowhere = await self.alice.post(
            f"/me/campaigns/999.{self.campaign.id}/handover", json={"email": NEW}
        )
        self.assertEqual(nowhere.status_code, 404)
        garbage = await self.alice.post("/me/campaigns/nonsense/handover", json={"email": NEW})
        self.assertEqual(garbage.status_code, 404)
        self.assertEqual(await self.offers(), [])
        self.assertEqual(self.transport.sent, [])

    async def test_the_account_page_lists_it_without_a_name(self) -> None:
        await self.offer()
        me = (await self.alice.get("/me")).json()
        [out] = me["offers"]["outgoing"]
        self.assertEqual((out["viaEmail"], out["personName"]), (True, ""))
        self.assertEqual(out["campaignName"], "Frostmaiden")
        self.assertNotIn(NEW, str(me))

    async def test_the_email_has_the_plans_steps_and_says_nothing_of_an_account(self) -> None:
        subject, body = handover_email.email_text(
            owner="Belleros",
            campaign="Frostmaiden",
            server="Thursday Table",
            link=f"{SITE}/handover#x",
        )
        for step in ("Sign in", "paid plan", "Try It", "Accept", "/dmbot start", "7 days"):
            self.assertIn(step, body)
        self.assertIn("plan's hours", body)  # what it costs the new owner
        self.assertTrue(body.startswith("The person named"))  # a chosen name is never first
        self.assertNotIn("account already", body.lower())
        self.assertIn("Belleros", subject)
