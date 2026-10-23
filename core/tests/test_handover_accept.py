"""Opening, accepting and declining a hand-over link on the website (#2532 slice B): the link says
nothing about why it ended, a look changes nothing, accepting moves ownership to the signed-in
account (the old owner stays a co-DM), the plan checks hold, and a link works for one person only.
As the website's own database role, through the routes the page uses."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.campaigns import CampaignStore
from dmbot.web import grants, sessions
from dmbot.web.discord import DiscordUser
from tests.test_handover_email import OfferBase
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW
from tests.test_web_api import ALICE, GORRAK, QUILLON, THURSDAY
from tests.test_web_email_signin import BOB

CAROL = DiscordUser(id=3003, name="Carol", email="carol@example.com")
WORKS = {"period_start": 0, "period_end": 4_000_000_000}


class LinkBase(OfferBase):
    async def give_plan(self, user_id: int, cap: int = 3) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(
                INSERT_PLAN, {**PLAN_ROW, **WORKS, "campaign_cap": cap, "user_id": user_id}
            )

    async def link(self) -> str:
        """Alice offers the campaign by email; the token the page would send."""
        self.assertEqual((await self.offer()).status_code, 200)
        return self.token_of(self.transport.sent[-1][1])

    async def post(self, who: httpx.AsyncClient, tail: str, **body: Any) -> httpx.Response:
        return await who.post(f"/me/handover/{tail}", json=body)

    async def bob(self) -> httpx.AsyncClient:
        client = await self.person(BOB)
        self.addAsyncCleanup(client.aclose)
        return client

    async def row(self) -> dict[str, Any]:
        [row] = await self.offers()
        return row


class Opening(LinkBase):
    async def test_a_look_shows_the_offer_and_changes_nothing(self) -> None:
        token = await self.link()
        got = (await self.post(await self.bob(), "look", token=token)).json()
        self.assertEqual(got["status"], "open")
        self.assertEqual(got["campaign"], {"name": "Frostmaiden"})
        self.assertEqual(got["from"], {"name": ALICE.name})
        self.assertEqual(got["server"]["id"], str(THURSDAY.id))
        self.assertEqual(
            got["servers"],
            [{"id": str(THURSDAY.id), "name": THURSDAY.name, "current": True, "botIn": True}],
        )
        row = await self.row()
        self.assertEqual((row["status"], row["to_user_id"]), ("open", 0))  # not claimed by a look

    async def test_every_link_that_opens_nothing_reads_the_same(self) -> None:
        token = await self.link()
        server, offer, secret = token.split("-", 2)
        bob = await self.bob()
        wrong = f"{server}-{offer}-{'x' * len(secret)}"
        elsewhere = f"{int(server) + 1}-{offer}-{secret}"
        for bad in (wrong, elsewhere, "nonsense", "", None, 5):
            got = await self.post(bob, "look", token=bad)
            self.assertEqual((got.status_code, got.json()), (200, {"status": "ended"}), bad)

    async def test_a_link_taken_back_or_late_has_ended(self) -> None:
        token = await self.link()
        bob = await self.bob()
        taken = await self.alice.delete(f"/me/campaigns/{self.ref}/handover")
        self.assertEqual((taken.status_code, taken.json()), (200, {"status": "withdrawn"}))
        self.assertEqual((await self.post(bob, "look", token=token)).json(), {"status": "ended"})
        accepted = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((accepted.status_code, accepted.json()["error"]), (409, "ended"))

    async def test_only_the_owner_can_take_an_offer_back(self) -> None:
        await self.link()
        bob = await self.bob()
        refused = await bob.delete(f"/me/campaigns/{self.ref}/handover")
        self.assertEqual(refused.status_code, 404)
        self.assertEqual((await self.row())["status"], "open")
        nothing = await self.alice.delete(f"/me/campaigns/{THURSDAY.id}.{'0' * 32}/handover")
        self.assertEqual(nothing.status_code, 404)

    async def test_an_expired_link_has_ended(self) -> None:
        token = await self.link()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET created_at = created_at - 8 * 86400"
            )
        got = await self.post(await self.bob(), "look", token=token)
        self.assertEqual(got.json(), {"status": "ended"})

    async def test_accepting_moves_ownership_to_the_account_and_keeps_the_old_owner_a_dm(
        self,
    ) -> None:
        await self.give_plan(BOB.id)
        token = await self.link()
        got = await self.post(await self.bob(), "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json(), {"status": "accepted", "campaignRef": self.ref})
        campaign = await CampaignStore(self.db).get(THURSDAY.id, self.campaign.id)
        assert campaign is not None
        self.assertEqual(campaign.owner_user_id, BOB.id)
        self.assertEqual(set(campaign.dm_user_ids), {ALICE.id, BOB.id})
        row = await self.row()
        self.assertEqual(
            (row["status"], row["to_user_id"], row["to_name"]), ("accepted", BOB.id, BOB.name)
        )

    async def test_the_link_works_once_and_for_one_person(self) -> None:
        await self.give_plan(BOB.id)
        token = await self.link()
        bob = await self.bob()
        await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        again = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((again.status_code, again.json()["error"]), (409, "ended"))
        carol = await self.person(CAROL)
        self.addAsyncCleanup(carol.aclose)
        self.assertEqual((await self.post(carol, "look", token=token)).json(), {"status": "ended"})

    async def test_a_look_does_not_claim_the_link(self) -> None:
        token = await self.link()
        carol = await self.person(CAROL)
        self.addAsyncCleanup(carol.aclose)
        await self.post(carol, "look", token=token)
        self.assertEqual((await self.row())["to_user_id"], 0)

    async def test_the_owner_cannot_accept_their_own_offer(self) -> None:
        token = await self.link()
        got = await self.post(self.alice, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((got.status_code, got.json()["error"]), (403, "owner"))
        look = (await self.post(self.alice, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "owner"))

    async def test_only_the_server_it_runs_on_now_for_now(self) -> None:
        await self.give_plan(BOB.id)
        token = await self.link()
        got = await self.post(await self.bob(), "accept", token=token, serverId="42")
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_server"))
        self.assertEqual((await self.row())["status"], "open")

    async def test_no_thanks_ends_the_offer_and_the_campaign_stays_put(self) -> None:
        token = await self.link()
        got = await self.post(await self.bob(), "decline", token=token)
        self.assertEqual((got.status_code, got.json()), (200, {"status": "declined"}))
        self.assertEqual((await self.row())["status"], "declined")
        campaign = await CampaignStore(self.db).get(THURSDAY.id, self.campaign.id)
        assert campaign is not None
        self.assertEqual(campaign.owner_user_id, ALICE.id)
        again = await self.post(await self.bob(), "decline", token=token)
        self.assertEqual((again.status_code, again.json()["error"]), (409, "ended"))

    async def test_a_bad_body_is_refused_in_plain_codes(self) -> None:
        bob = await self.bob()
        got = await self.post(bob, "accept")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "ended"))
        got = await self.post(bob, "decline")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "ended"))

    async def test_a_person_outside_the_server_can_say_no_thanks_and_it_is_recorded(self) -> None:
        # The common case: the person emailed is not in the campaign's server.
        from dmbot.web import sessions

        token = await self.link()
        outsider = await sessions.sign_in(self.web, BOB, [QUILLON], now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://api.dmbot.example",
            headers=self.alice.headers,
        )  # fmt: skip
        client.cookies.set("__Host-dmbot_session", outsider, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        got = await self.post(client, "decline", token=token)
        self.assertEqual((got.status_code, got.json()), (200, {"status": "declined"}))
        row = await self.row()
        self.assertEqual(
            (row["status"], row["to_user_id"], row["to_name"]), ("declined", BOB.id, BOB.name)
        )
        self.assertEqual((await self.post(client, "look", token=token)).json(), {"status": "ended"})
        again = await self.post(client, "decline", token=token)
        self.assertEqual((again.status_code, again.json()["error"]), (409, "ended"))

    async def test_the_owner_cannot_decline_their_own_offer(self) -> None:
        token = await self.link()
        got = await self.post(self.alice, "decline", token=token)
        self.assertEqual(got.status_code, 409)
        self.assertEqual((await self.row())["status"], "open")


class Plans(LinkBase):
    """Only a paid plan with room can receive a campaign handed over by email (PLAN), whether or
    not the campaign cap is enforced."""

    async def test_no_plan_cannot_accept_and_the_page_says_why(self) -> None:
        token = await self.link()
        bob = await self.bob()
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "no_plan"))
        got = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((got.status_code, got.json()["error"]), (402, "not_on_a_paid_plan"))
        self.assertEqual((await self.row())["to_user_id"], 0)  # nothing was claimed

    async def test_a_full_plan_cannot_accept(self) -> None:
        await self.give_plan(BOB.id, cap=1)
        await CampaignStore(self.db).create(THURSDAY.id, "Bob's own", BOB.id)
        token = await self.link()
        bob = await self.bob()
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "no_slot"))
        got = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((got.status_code, got.json()["error"]), (402, "no_free_slot"))

    async def test_a_paid_plan_with_room_can(self) -> None:
        await self.give_plan(BOB.id, cap=2)
        token = await self.link()
        look = (await self.post(await self.bob(), "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (True, None))

    async def test_an_accept_that_fails_gives_the_link_back_to_the_real_recipient(self) -> None:
        from unittest.mock import patch

        carol = await self.person(CAROL)  # (signing in makes the account)
        self.addAsyncCleanup(carol.aclose)
        await self.give_plan(BOB.id)
        await self.give_plan(CAROL.id)
        token = await self.link()
        # Carol holds the link and passes every check, but the accept itself does not happen
        # (say her room went between the check and the write): the link must not stay hers.
        with patch.object(CampaignStore, "accept_handover", return_value="no_free_slot"):
            got = await self.post(carol, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((got.status_code, got.json()["error"]), (402, "no_free_slot"))
        self.assertEqual((await self.row())["to_user_id"], 0)
        bob = await self.bob()
        done = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual(done.status_code, 200, done.text)

    async def test_two_people_accepting_at_once_make_one_owner(self) -> None:
        import asyncio

        bob, carol = await self.bob(), await self.person(CAROL)
        self.addAsyncCleanup(carol.aclose)
        await self.give_plan(BOB.id)
        await self.give_plan(CAROL.id)
        token = await self.link()
        got = await asyncio.gather(
            *[
                self.post(who, "accept", token=token, serverId=str(THURSDAY.id))
                for who in (bob, carol)
            ]
        )
        self.assertEqual(sorted(g.status_code for g in got), [200, 409])
        campaign = await CampaignStore(self.db).get(THURSDAY.id, self.campaign.id)
        assert campaign is not None
        self.assertIn(campaign.owner_user_id, (BOB.id, CAROL.id))

    async def test_whoever_holds_the_link_can_say_no_thanks_and_it_ends_for_everyone(self) -> None:
        # The emailed link is the recipient (that is what a one-use link is): a stranger who got
        # it can end the offer, and the owner offers again.
        token = await self.link()
        carol = await self.person(CAROL)
        self.addAsyncCleanup(carol.aclose)
        self.assertEqual((await self.post(carol, "decline", token=token)).status_code, 200)
        self.assertEqual(
            (await self.post(await self.bob(), "look", token=token)).json(), {"status": "ended"}
        )

    async def test_try_it_and_its_trial_extension_cannot_receive_a_campaign(self) -> None:
        token = await self.link()
        bob = await self.bob()
        for period_end in (
            4_000_000_000,
            4_100_000_000,
        ):  # Try It, then lengthened by the extension
            async with self.db.plan_writer(BOB.id) as conn:
                await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (BOB.id,))
                await conn.execute(
                    INSERT_PLAN,
                    {**PLAN_ROW, **WORKS, "plan": "try-it", "campaign_cap": 1,
                     "period_end": period_end, "user_id": BOB.id},
                )  # fmt: skip
            look = (await self.post(bob, "look", token=token)).json()
            self.assertEqual((look["canAccept"], look["why"]), (False, "try_it"), period_end)
            got = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
            self.assertEqual((got.status_code, got.json()["error"]), (402, "not_on_a_paid_plan"))
            self.assertEqual((await self.row())["to_user_id"], 0)  # nothing was claimed

    async def test_a_grant_alone_cannot_but_a_priced_plan_with_a_grant_can(self) -> None:
        token = await self.link()
        bob = await self.bob()
        await grants.give(
            self.db, "admin@example.invalid", BOB.id, "guild", ends_at=None, note="", now=self.now
        )
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "no_plan"))  # a grant alone
        await self.give_plan(BOB.id, cap=1)
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (True, None))

    async def test_a_free_listed_account_with_a_priced_plan_can_and_without_one_cannot(
        self,
    ) -> None:
        from dmbot import entitlements

        entitlements.configure_free_users([BOB.id])
        self.addCleanup(entitlements.configure_free_users, ())
        token = await self.link()
        bob = await self.bob()
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "no_plan"))  # free list alone
        await self.give_plan(BOB.id, cap=2)
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (True, None))
        got = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual(got.status_code, 200, got.text)

    async def test_the_owner_hears_of_a_decline_on_the_website(self) -> None:
        # The door announces the answer, as every answer made on the website does (#737).
        import psycopg

        from tests.pg import TEST_URL

        token = await self.link()
        bob = await self.bob()
        async with await psycopg.AsyncConnection.connect(TEST_URL, autocommit=True) as listener:
            await listener.execute("LISTEN dmbot_handover_decided")
            got = await self.post(bob, "decline", token=token)
            self.assertEqual(got.status_code, 200, got.text)
            seen = []
            async for note in listener.notifies(timeout=5, stop_after=1):
                seen.append(note.payload)
        [row] = await self.offers()
        self.assertEqual(seen, [f"{THURSDAY.id}:{row['id']}"])

    async def test_a_free_listed_account_has_its_plans_own_room_not_unlimited(self) -> None:
        from dmbot import entitlements

        entitlements.configure_free_users([BOB.id])
        self.addCleanup(entitlements.configure_free_users, ())
        await self.give_plan(BOB.id, cap=1)
        await CampaignStore(self.db).create(THURSDAY.id, "Bob's own", BOB.id)  # one owned, cap 1
        token = await self.link()
        bob = await self.bob()
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (False, "no_slot"))
        got = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((got.status_code, got.json()["error"]), (402, "no_free_slot"))
        await grants.give(  # a grant may raise it
            self.db, "admin@example.invalid", BOB.id, "guild", ends_at=None, note="", now=self.now
        )
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual((look["canAccept"], look["why"]), (True, None))

    async def test_a_refused_second_decline_sends_nothing(self) -> None:
        import psycopg

        from tests.pg import TEST_URL

        token = await self.link()
        bob = await self.bob()
        async with await psycopg.AsyncConnection.connect(TEST_URL, autocommit=True) as listener:
            await listener.execute("LISTEN dmbot_handover_decided")
            first = await self.post(bob, "decline", token=token)
            second = await self.post(bob, "decline", token=token)
            seen = [n.payload async for n in listener.notifies(timeout=2)]
        self.assertEqual((first.status_code, second.status_code), (200, 409))
        [row] = await self.offers()
        self.assertEqual(seen, [f"{THURSDAY.id}:{row['id']}"])  # one notice, not two


class Moving(LinkBase):
    """Slice C1 (#2532): accepting for another server records the wish and claims the link in one
    step. The bot's move (C2) is not built yet, so it is held behind `MOVES_ARE_RUN`."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from unittest.mock import patch

        patcher = patch("dmbot.web.handover_accept.MOVES_ARE_RUN", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.give_plan(BOB.id)
        async with self.db.guild(QUILLON.id) as conn:  # DMbot is installed in Quillon's Corner
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_at, via) VALUES (%s, %s, 'link')",
                (QUILLON.id, self.now),
            )

    async def bob_in(self, *guilds: Any) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, BOB, list(guilds), now=self.now, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://api.dmbot.example",
            headers=self.alice.headers,
        )  # fmt: skip
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_the_page_lists_their_servers_and_whether_dmbot_is_in_each(self) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON, GORRAK)
        look = (await self.post(bob, "look", token=token)).json()
        self.assertEqual(
            look["servers"],
            [
                {"id": "111", "name": "Thursday Table", "current": True, "botIn": True},
                {"id": "222", "name": "Quillon's Corner", "current": False, "botIn": True},
            ],  # (Gorrak's Hall: not one they manage)
        )

    async def test_asking_for_another_server_claims_the_link_and_records_the_wish(self) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        got = await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual((got.status_code, got.json()), (202, {"status": "moving"}))
        row = await self.row()
        self.assertEqual(
            (row["status"], row["to_user_id"], row["move_to_guild_id"]),
            ("open", BOB.id, QUILLON.id),
        )
        campaign = await CampaignStore(self.db).get(THURSDAY.id, self.campaign.id)
        assert campaign is not None
        self.assertEqual(campaign.owner_user_id, ALICE.id)  # nothing has moved yet

    async def test_a_server_without_dmbot_or_not_theirs_is_refused_and_nothing_is_claimed(
        self,
    ) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, GORRAK)
        for server in (str(GORRAK.id), "999", "nonsense"):  # no DMbot there / not theirs / junk
            got = await self.post(bob, "accept", token=token, serverId=server)
            self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_server"), server)
        self.assertEqual((await self.row())["to_user_id"], 0)

    async def test_no_plan_cannot_ask_for_a_move(self) -> None:
        token = await self.link()
        async with self.db.plan_writer(BOB.id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (BOB.id,))
        bob = await self.bob_in(THURSDAY, QUILLON)
        got = await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual((got.status_code, got.json()["error"]), (402, "not_on_a_paid_plan"))
        self.assertIsNone((await self.row())["move_to_guild_id"])

    async def test_while_a_move_is_asked_for_it_cannot_be_taken_back_replaced_or_run_out(
        self,
    ) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual(
            (await self.alice.delete(f"/me/campaigns/{self.ref}/handover")).status_code, 404
        )
        again = await self.offer("someone.else@example.org")
        self.assertEqual((again.status_code, again.json()["error"]), (409, "not_allowed"))
        async with self.db.guild(THURSDAY.id) as conn:  # eight days on
            await conn.execute(
                "UPDATE campaign_handover_offers SET created_at = created_at - 8 * 86400"
            )
        store = CampaignStore(self.db, clock=lambda: self.now)
        self.assertEqual(await store.offers_to_end(THURSDAY.id, self.now), [])
        [row] = await self.offers()
        self.assertEqual((row["status"], row["move_to_guild_id"]), ("open", QUILLON.id))
        shown = await store.open_offer(THURSDAY.id, self.campaign.id, self.now)
        self.assertIsNotNone(shown)  # the card still shows it waiting

    async def test_a_second_person_cannot_use_the_link_after_a_move_is_asked_for(self) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        carol = await self.person(CAROL)
        self.addAsyncCleanup(carol.aclose)
        self.assertEqual((await self.post(carol, "look", token=token)).json(), {"status": "ended"})

    async def test_without_the_bots_move_another_server_is_refused(self) -> None:
        # The default until C2: an accepted move that never runs must not be possible.
        from unittest.mock import patch

        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        with patch("dmbot.web.handover_accept.MOVES_ARE_RUN", False):
            look = (await self.post(bob, "look", token=token)).json()
            self.assertEqual([s["id"] for s in look["servers"]], ["111"])
            got = await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_server"))
        self.assertEqual((await self.row())["to_user_id"], 0)

    async def test_a_server_they_are_in_but_do_not_manage_is_refused_and_nothing_is_claimed(
        self,
    ) -> None:
        async with self.db.guild(GORRAK.id) as conn:  # DMbot is installed there
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_at, via) VALUES (%s, %s, 'link')",
                (GORRAK.id, self.now),
            )
        token = await self.link()
        bob = await self.bob_in(THURSDAY, GORRAK)
        got = await self.post(bob, "accept", token=token, serverId=str(GORRAK.id))
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_server"))
        self.assertEqual((await self.row())["to_user_id"], 0)

    async def test_a_pending_move_cannot_be_looked_at_answered_or_declined_underneath(self) -> None:
        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual((await self.post(bob, "look", token=token)).json(), {"status": "ended"})
        same = await self.post(bob, "accept", token=token, serverId=str(THURSDAY.id))
        self.assertEqual((same.status_code, same.json()["error"]), (409, "ended"))
        declined = await self.post(bob, "decline", token=token)
        self.assertEqual((declined.status_code, declined.json()["error"]), (409, "ended"))
        row = await self.row()  # the claim and the wish are untouched
        self.assertEqual(
            (row["status"], row["to_user_id"], row["move_to_guild_id"]),
            ("open", BOB.id, QUILLON.id),
        )

    async def test_end_to_end_the_wish_becomes_a_campaign_in_the_other_server(self) -> None:
        """The page asks (202 moving), the bot's mover moves it, and the new owner's account page
        shows the campaign in the other server while the old server has nothing left."""
        from dmbot.campaigns.moves import HandoverMover

        token = await self.link()
        bob = await self.bob_in(THURSDAY, QUILLON)
        got = await self.post(bob, "accept", token=token, serverId=str(QUILLON.id))
        self.assertEqual(got.status_code, 202)
        store = CampaignStore(self.db, clock=lambda: self.now)
        mover = HandoverMover(store, clock=lambda: self.now)
        [offer_id] = await mover.waiting(THURSDAY.id)
        self.assertEqual(await mover.move(THURSDAY.id, offer_id), "moved")
        me = (await bob.get("/me")).json()
        mine = [c for c in me["campaigns"] if c["role"] == "owner"]
        self.assertEqual(
            [(c["name"], c["serverName"]) for c in mine], [("Frostmaiden", "Quillon's Corner")]
        )
        self.assertEqual(await store.get(THURSDAY.id, self.campaign.id), None)
