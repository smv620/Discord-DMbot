"""Opening, accepting and declining a hand-over link on the website (#2532 slice B): the link says
nothing about why it ended, a look changes nothing, accepting moves ownership to the signed-in
account (the old owner stays a co-DM), the plan checks hold, and a link works for one person only.
As the website's own database role, through the routes the page uses."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.campaigns import CampaignStore
from dmbot.web.sessions import DiscordUser
from tests.test_handover_email import OfferBase
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW
from tests.test_web_api import ALICE, THURSDAY
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


class Enforced(LinkBase):
    """With plans enforced (the go-live switch): a paid plan with room, nothing else."""

    enforce_plans = True

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
