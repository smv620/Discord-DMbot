"""Answering a campaign hand-over on the website (#614), as the website's own database
role: the same store rules as the bot's buttons, held to the signed-in person's own
offers and servers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
from psycopg import errors

from dmbot import campaign_cap
from dmbot.campaigns.models import HandoverOffer
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.web import entitlements_writer, offers, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild, DiscordUser
from dmbot.web.me import build_me
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
        offer = await self.offer()
        self.ref = offers.offer_ref(THURSDAY.id, offer.id)

    async def asyncTearDown(self) -> None:
        await self.web.close()
        await super().asyncTearDown()

    async def sign_in(self, user: DiscordUser, guilds: list[DiscordGuild]) -> sessions.Session:
        token = await sessions.sign_in(self.web, user, guilds, now=self.now, days=30)
        found = await sessions.find(self.web, token, now=self.now)
        assert found is not None
        return found

    async def offer(self) -> HandoverOffer:
        return await self.store.offer_handover(
            THURSDAY.id,
            self.campaign.id,
            ALICE.id,
            BOB.id,
            self.now,
            from_name=ALICE.name,
            to_name=BOB.name,
        )

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

    async def test_the_account_page_lists_open_offers_both_ways(self) -> None:
        expected = {
            "id": self.ref,
            "campaignId": self.campaign.id,
            "campaignName": "The Brynwater Crossing",
            "serverName": THURSDAY.name,
            "expiresAt": "2027-01-22T08:00:00Z",
        }
        bob = (await build_me(self.web, self.bob, now=self.now))["offers"]
        self.assertEqual(
            bob, {"incoming": [{**expected, "personName": ALICE.name}], "outgoing": []}
        )
        alice = (await build_me(self.web, self.alice, now=self.now))["offers"]
        self.assertEqual(
            alice, {"incoming": [], "outgoing": [{**expected, "personName": BOB.name}]}
        )
        # Gone once it's past 7 days, or for someone signed in without that server.
        later = await build_me(self.web, self.bob, now=self.now + 7 * DAY)
        self.assertEqual(later["offers"], {"incoming": [], "outgoing": []})
        elsewhere = await self.sign_in(BOB, [GORRAK])
        outside = await build_me(self.web, elsewhere, now=self.now)
        self.assertEqual(outside["offers"], {"incoming": [], "outgoing": []})

    async def test_with_plans_enforced_an_owner_at_the_cap_cannot_accept(self) -> None:
        # Try It covers one campaign and Bob already owns one: accepting a second on the site
        # must be refused just as it is in Discord (#437 part 2c; Supervisor, #927).
        await self.store.create(GORRAK.id, "Bob's own", BOB.id)
        outcome = await offers.answer(
            self.web, self.bob, self.ref, "accept", now=self.now, enforce_plans=True
        )
        self.assertEqual(outcome, "no_free_slot")
        owner, _ = await self.owner_and_dms()
        self.assertEqual(owner, ALICE.id)  # nothing changed

    async def test_with_plans_enforced_an_owner_with_room_may_accept_and_the_count_follows(
        self,
    ) -> None:
        outcome = await offers.answer(
            self.web, self.bob, self.ref, "accept", now=self.now, enforce_plans=True
        )
        self.assertEqual(outcome, "accepted")
        async with self.db.guild(THURSDAY.id) as conn:  # the bot's side counts the same
            self.assertEqual(await campaign_cap.owned_count(conn, BOB.id), 1)
            self.assertEqual(await campaign_cap.owned_count(conn, ALICE.id), 0)

    async def test_not_enforced_an_owner_at_the_cap_may_accept(self) -> None:
        await self.store.create(GORRAK.id, "Bob's own", BOB.id)
        outcome = await offers.answer(self.web, self.bob, self.ref, "accept", now=self.now)
        self.assertEqual(outcome, "accepted")

    async def test_the_site_can_count_only_its_own_signed_in_person(self) -> None:
        # The site's role has no grant on the table, only on the one function, which takes
        # no argument and answers for the person set in the transaction.
        await self.store.create(GORRAK.id, "Bob's own", BOB.id)
        async with self.web.as_person(BOB.id, self.bob.id_hash).guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT dmbot_owned_campaigns() AS n")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT count(*) FROM owner_campaigns")

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
        again = await self.offer()
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
        await self.offer()
        with self.assertRaises(errors.InsufficientPrivilege):
            async with scoped.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "UPDATE campaigns SET owner_user_id = %s WHERE id = %s",
                    (ALICE.id + 1, self.campaign.id),
                )

    async def test_the_role_has_no_other_rights_on_offers_or_campaigns(self) -> None:
        # The grants (schema.WEB_ROLE_GRANTS) allow only an offer's status and a campaign's
        # owner to change; everything else is refused before any policy runs.
        scoped = self.web.as_person(BOB.id, self.bob.id_hash)
        refused = (
            (
                "INSERT INTO campaign_handover_offers (guild_id, campaign_id, from_user_id,"
                " to_user_id, created_at, from_name, to_name)"
                " VALUES (%s, %s, %s, %s, %s, 'a', 'b')",
                (THURSDAY.id, self.campaign.id, ALICE.id, BOB.id, self.now),
            ),
            ("UPDATE campaign_handover_offers SET to_user_id = %s", (BOB.id,)),
            ("UPDATE campaign_handover_offers SET from_user_id = %s", (BOB.id,)),
            ("UPDATE campaign_handover_offers SET created_at = %s", (self.now,)),
            ("UPDATE campaigns SET name = %s", ("Mine now",)),
        )
        for sql, params in refused:
            with self.subTest(sql=sql), self.assertRaises(errors.InsufficientPrivilege):
                async with scoped.guild(THURSDAY.id) as conn:
                    await conn.execute(sql, params)

    async def test_the_accept_policies_only_narrow(self) -> None:
        # RESTRICTIVE: ANDed with each table's own policy, never widening what it allows.
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT tablename, permissive FROM pg_policies"
                " WHERE schemaname = current_schema() AND policyname = 'web_accept_handover'"
                " ORDER BY tablename"
            )
            rows = [(r["tablename"], r["permissive"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [("campaign_dms", "RESTRICTIVE"), ("campaigns", "RESTRICTIVE")])

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


class SiteAnswersAnnounce(unittest.IsolatedAsyncioTestCase):
    """Every answer on the site tells the bot, so the other person hears (#737)."""

    async def test_each_answer_is_made_with_announce(self) -> None:
        session = MagicMock(user_id=7, id_hash="h", guilds=[MagicMock(id=111)])
        for what, method in (
            ("accept", "accept_handover"),
            ("decline", "decline_handover"),
            ("withdraw", "withdraw_handover"),
        ):
            with patch.object(CampaignStore, method, AsyncMock(return_value="gone")) as call:
                await offers.answer(MagicMock(), session, "111-5", what, now=1)  # type: ignore[arg-type]
            assert call.await_args is not None
            self.assertEqual(call.await_args.kwargs, {"announce": True}, what)
