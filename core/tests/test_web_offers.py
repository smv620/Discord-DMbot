"""Answering a campaign hand-over on the website (#614), as the website's own database
role: the same store rules as the bot's buttons, held to the signed-in person's own
offers and servers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import errors

from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.web import entitlements_writer, offers, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild, DiscordUser
from dmbot.web.payments import FakeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, API, GORRAK, QUILLON, SITE, THURSDAY, FakeDiscord, settings
from tests.test_web_role import ensure_web_role, web_url

BOB = DiscordUser(id=2002, name="Quillon", email="quillon@example.com")
DAY = 86400


class WebOffers(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.now = 1_800_000_000
        self.store = CampaignStore(self.db, clock=lambda: self.now)  # the bot's side
        self.campaign = await self.store.create(THURSDAY.id, "The Brynwater Crossing", ALICE.id)
        self.alice = await self.sign_in(ALICE, [THURSDAY, QUILLON])
        self.bob = await self.sign_in(BOB, [THURSDAY, GORRAK])
        started = await entitlements_writer.start_try_it(self.web, BOB.id, now=self.now)
        self.assertTrue(started.started)
        offer = await self.store.offer_handover(
            THURSDAY.id, self.campaign.id, ALICE.id, BOB.id, self.now
        )
        self.ref = offers.offer_ref(THURSDAY.id, offer.id)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def sign_in(self, user: DiscordUser, guilds: list[DiscordGuild]) -> sessions.Session:
        token = await sessions.sign_in(self.web, user, guilds, now=self.now, days=30)
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        return found

    async def owner_and_dms(self) -> tuple[int | None, set[int]]:
        campaign = await self.store.get(THURSDAY.id, self.campaign.id)
        assert campaign is not None
        return campaign.owner_user_id, set(campaign.dm_user_ids)

    async def test_accept_makes_them_the_owner_and_a_dm(self) -> None:
        outcome = await offers.answer(self.web, self.bob, self.ref, "accept", now=self.now)
        self.assertEqual(outcome, "accepted")
        owner, dms = await self.owner_and_dms()
        self.assertEqual(owner, BOB.id)
        self.assertEqual(dms, {ALICE.id, BOB.id})  # the old owner stays a DM

    async def test_only_the_person_offered_can_accept(self) -> None:
        self.assertEqual(
            await offers.answer(self.web, self.alice, self.ref, "accept", now=self.now), "gone"
        )
        self.assertEqual((await self.owner_and_dms())[0], ALICE.id)

    async def test_an_offer_outside_the_sessions_servers_or_garbled_is_gone(self) -> None:
        elsewhere = await self.sign_in(BOB, [GORRAK])  # signed in without that server
        for session, ref in [(elsewhere, self.ref), (self.bob, "nonsense"), (self.bob, "1-2-3")]:
            with self.subTest(ref=ref):
                outcome = await offers.answer(self.web, session, ref, "accept", now=self.now)
                self.assertEqual(outcome, "gone")
        self.assertEqual((await self.owner_and_dms())[0], ALICE.id)

    async def test_no_free_slot_is_checked_when_accepting(self) -> None:
        async with self.db.plan_writer(BOB.id) as conn:  # the plan stopped since the offer
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', lapsed_at = %s WHERE user_id = %s",
                (self.now, BOB.id),
            )
        outcome = await offers.answer(self.web, self.bob, self.ref, "accept", now=self.now)
        self.assertEqual(outcome, "no_free_slot")
        self.assertEqual((await self.owner_and_dms())[0], ALICE.id)

    async def test_an_expired_offer_accepts_nothing(self) -> None:
        later = self.now + 7 * DAY
        self.assertEqual(
            await offers.answer(self.web, self.bob, self.ref, "accept", now=later), "gone"
        )

    async def test_decline_and_withdraw_end_it(self) -> None:
        self.assertEqual(
            await offers.answer(self.web, self.alice, self.ref, "decline", now=self.now), "gone"
        )  # only the person offered declines
        self.assertEqual(
            await offers.answer(self.web, self.bob, self.ref, "decline", now=self.now), "declined"
        )
        self.assertEqual(
            await offers.answer(self.web, self.bob, self.ref, "accept", now=self.now), "gone"
        )
        again = await self.store.offer_handover(
            THURSDAY.id, self.campaign.id, ALICE.id, BOB.id, self.now
        )
        ref = offers.offer_ref(THURSDAY.id, again.id)
        self.assertEqual(
            await offers.answer(self.web, self.bob, ref, "withdraw", now=self.now), "gone"
        )
        self.assertEqual(
            await offers.answer(self.web, self.alice, ref, "withdraw", now=self.now), "withdrawn"
        )

    async def test_others_in_the_same_server_cant_see_the_offer(self) -> None:
        carol = await self.sign_in(
            DiscordUser(id=3003, name="Mirelle", email="m@example.com"), [THURSDAY]
        )
        for session, seen in [(carol, 0), (self.alice, 1), (self.bob, 1)]:
            scoped = self.web.as_person(session.user_id, session.id_hash)
            async with scoped.guild(THURSDAY.id) as conn:
                cur = await conn.execute("SELECT count(*) AS n FROM campaign_handover_offers")
                row = await cur.fetchone()
            self.assertEqual(row and row["n"], seen, session.user_id)

    async def test_the_database_allows_the_owner_and_dm_writes_only_with_an_open_offer(
        self,
    ) -> None:
        # Decision 7 on #437: checked by the database, not only by the store's code.
        scoped = self.web.as_person(BOB.id, self.bob.id_hash)

        async def take_it() -> int:
            async with scoped.guild(THURSDAY.id) as conn:
                cur = await conn.execute(
                    "UPDATE campaigns SET owner_user_id = %s WHERE id = %s",
                    (BOB.id, self.campaign.id),
                )
                return cur.rowcount

        async def join_it() -> None:
            async with scoped.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)",
                    (self.campaign.id, THURSDAY.id, BOB.id),
                )

        self.assertEqual(
            await offers.answer(self.web, self.bob, self.ref, "decline", now=self.now), "declined"
        )
        self.assertEqual(await take_it(), 0)  # no open offer: the row isn't there to change
        with self.assertRaises(errors.InsufficientPrivilege):
            await join_it()
        self.assertEqual(await self.owner_and_dms(), (ALICE.id, {ALICE.id}))
        # Nor can it make someone else the owner, even with an offer open to them.
        await self.store.offer_handover(THURSDAY.id, self.campaign.id, ALICE.id, BOB.id, self.now)
        with self.assertRaises(errors.InsufficientPrivilege):
            async with scoped.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "UPDATE campaigns SET owner_user_id = %s WHERE id = %s",
                    (ALICE.id + 1, self.campaign.id),
                )

    async def test_the_api_answers_with_plain_codes(self) -> None:
        discord = FakeDiscord()
        discord.user_info = BOB
        app = create_app(
            settings(),
            self.web,
            discord,
            payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: self.now,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        ) as client:
            start = await client.get("/auth/discord/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            await client.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
            headers = {"X-DMbot-Request": "1"}
            before = (await client.get("/me")).json()["campaigns"]
            self.assertEqual(before, [])  # not a DM of it yet
            accepted = await client.post(f"/offers/{self.ref}/accept", headers=headers)
            self.assertEqual(accepted.status_code, 204)
            after = (await client.get("/me")).json()["campaigns"]
            self.assertEqual(
                [(c["name"], c["role"]) for c in after], [(self.campaign.name, "owner")]
            )
            again = await client.post(f"/offers/{self.ref}/accept", headers=headers)
            self.assertEqual((again.status_code, again.json()), (409, {"error": "offer_gone"}))
            refused = await client.post(f"/offers/{self.ref}/withdraw")  # no site header
            self.assertEqual(refused.status_code, 403)
        self.assertEqual((await self.owner_and_dms())[0], BOB.id)
