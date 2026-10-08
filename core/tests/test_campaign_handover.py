"""Handing a campaign over to another subscriber (#437 part 1b): only the owner offers,
the person offered accepts within 7 days if they still have room, and every write is
in CampaignStore so the bot and the website follow the same rules."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.models import HANDOVER_SECONDS, HandoverOffer
from dmbot.campaigns.store import (
    NO_OWNER_YET,
    NOT_A_SUBSCRIBER,
    NOT_THE_OWNER,
    OFFER_TO_SELF,
    OFFER_WAITING,
    OWNER_STAYS,
)
from dmbot.entitlements import RENEWAL_SLACK_SECONDS
from tests.pg import DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

GUILD, OTHER_GUILD = 111, 222
OWNER, CO_DM, BUYER, NO_PLAN = 7, 8, 9, 10
NOW = 1_000
WORKS = {"period_start": 0, "period_end": 4_000_000_000}  # a plan that works throughout


class HandoverTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db, clock=lambda: NOW)
        for person in (OWNER, CO_DM, BUYER):
            await self.give_plan(person)
        self.campaign = await self.store.create(GUILD, "Frostmaiden", OWNER)

    async def give_plan(self, user_id: int, **values: Any) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (user_id,),
            )
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(INSERT_PLAN, {**PLAN_ROW, **WORKS, **values, "user_id": user_id})

    async def make_offer(
        self, guild: int, campaign_id: str, by: int, to: int, now: int
    ) -> HandoverOffer:
        return await self.store.offer_handover(
            guild, campaign_id, by, to, now, from_name=f"Person {by}", to_name=f"Person {to}"
        )

    async def offer(self, to: int = BUYER, now: int = NOW) -> int:
        offer = await self.make_offer(GUILD, self.campaign.id, OWNER, to, now)
        return offer.id


class Offering(HandoverTest):
    async def test_the_owner_offers_and_the_buyer_accepts(self) -> None:
        offer = await self.make_offer(GUILD, self.campaign.id, OWNER, BUYER, NOW)
        self.assertEqual((offer.status, offer.expires_at), ("open", NOW + HANDOVER_SECONDS))
        self.assertEqual(await self.store.open_offer(GUILD, self.campaign.id, NOW), offer)
        self.assertEqual(await self.store.accept_handover(GUILD, offer.id, BUYER, NOW), "accepted")
        after = await self.store.get(GUILD, self.campaign.id)
        assert after is not None
        self.assertEqual(after.owner_user_id, BUYER)
        self.assertEqual(after.dm_user_ids, frozenset({OWNER, BUYER}))  # the old one stays
        done = await self.store.get_offer(GUILD, offer.id, NOW)
        assert done is not None
        self.assertEqual((done.status, done.decided_at), ("accepted", NOW))
        self.assertIsNone(await self.store.open_offer(GUILD, self.campaign.id, NOW))

    async def test_only_the_owner_offers_and_only_to_a_subscriber(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        cases = [
            (CO_DM, BUYER, NOT_THE_OWNER),  # a co-DM isn't the owner
            (OWNER, OWNER, OFFER_TO_SELF),
            (OWNER, NO_PLAN, NOT_A_SUBSCRIBER),
        ]
        for by, to, words in cases:
            with (
                self.subTest(by=by, to=to),
                self.assertRaisesRegex(CampaignError, re.escape(words)),
            ):
                await self.make_offer(GUILD, self.campaign.id, by, to, NOW)

    async def test_a_lapsed_plan_is_not_a_subscriber(self) -> None:
        await self.give_plan(NO_PLAN, status="lapsed", lapsed_at=500)
        with self.assertRaisesRegex(CampaignError, re.escape(NOT_A_SUBSCRIBER)):
            await self.offer(to=NO_PLAN)

    async def test_one_open_offer_at_a_time_until_it_expires(self) -> None:
        first = await self.offer()
        with self.assertRaisesRegex(CampaignError, re.escape(OFFER_WAITING)):
            await self.offer(to=CO_DM)
        later = NOW + HANDOVER_SECONDS + 5
        self.assertIsNone(await self.store.open_offer(GUILD, self.campaign.id, later))
        second = await self.offer(to=CO_DM, now=later)  # the old one no longer counts
        old = await self.store.get_offer(GUILD, first, later)
        assert old is not None
        self.assertEqual((old.status, old.decided_at), ("expired", NOW + HANDOVER_SECONDS))
        self.assertNotEqual(first, second)

    async def test_two_offers_at_once_make_one(self) -> None:
        results = await asyncio.gather(
            self.make_offer(GUILD, self.campaign.id, OWNER, BUYER, NOW),
            self.make_offer(GUILD, self.campaign.id, OWNER, CO_DM, NOW),
            return_exceptions=True,
        )
        refused = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(refused), 1)
        self.assertIsInstance(refused[0], CampaignError)
        self.assertEqual(str(refused[0]), OFFER_WAITING)

    async def test_the_offer_keeps_both_names_as_they_were(self) -> None:
        offer = await self.store.offer_handover(
            GUILD, self.campaign.id, OWNER, BUYER, NOW, from_name=" Oskar  the  Bold ",
            to_name="M" * 150,
        )  # fmt: skip
        self.assertEqual((offer.from_name, offer.to_name), ("Oskar the Bold", "M" * 100))
        again = await self.store.get_offer(GUILD, offer.id, NOW)
        assert again is not None
        self.assertEqual(again.from_name, "Oskar the Bold")
        with self.assertRaises(ValueError):  # never stored without a name
            await self.store.offer_handover(
                GUILD, self.campaign.id, OWNER, BUYER, NOW, from_name="  ", to_name="Mirelle"
            )

    async def test_a_campaign_with_no_owner_yet_cannot_be_offered(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD, self.campaign.id),
            )
        with self.assertRaisesRegex(CampaignError, re.escape(NO_OWNER_YET)):
            await self.offer()
        with self.assertRaisesRegex(CampaignError, re.escape(NO_OWNER_YET)):
            await self.offer(to=OWNER)  # not "already yours": nobody owns it

    async def test_the_old_owner_cannot_offer_again_while_an_accept_commits(self) -> None:
        """What the campaign lock guards (#652): the old offer is no longer open once
        accepted, so only the lock stops the old owner offering again meanwhile."""
        first = await self.offer()
        results = await asyncio.gather(
            self.store.accept_handover(GUILD, first, BUYER, NOW),
            self.make_offer(GUILD, self.campaign.id, OWNER, CO_DM, NOW),
            return_exceptions=True,
        )
        self.assertEqual(results[0], "accepted")
        self.assertIsInstance(results[1], CampaignError)
        self.assertIn(str(results[1]), (NOT_THE_OWNER, OFFER_WAITING))
        self.assertIsNone(await self.store.open_offer(GUILD, self.campaign.id, NOW))

    async def test_offers_stay_in_their_server(self) -> None:
        offer = await self.offer()
        self.assertIsNone(await self.store.get_offer(OTHER_GUILD, offer, NOW))
        self.assertEqual(await self.store.accept_handover(OTHER_GUILD, offer, BUYER, NOW), "gone")
        with self.assertRaises(CampaignError):  # not a campaign of that server
            await self.make_offer(OTHER_GUILD, self.campaign.id, OWNER, BUYER, NOW)

    async def test_deleting_the_campaign_deletes_its_offers(self) -> None:
        offer = await self.offer()
        await self.store.delete(GUILD, self.campaign.id)
        self.assertIsNone(await self.store.get_offer(GUILD, offer, NOW))


class SiteDelivery(HandoverTest):
    """Offers made on the website wait for the bot to send them (#690)."""

    async def site_offer(self, now: int = NOW) -> HandoverOffer:
        return await self.store.offer_handover(
            GUILD, self.campaign.id, OWNER, BUYER, now,
            from_name="Owner", to_name="Buyer", delivered=False,
        )  # fmt: skip

    async def test_a_discord_offer_is_sent_already(self) -> None:
        offer = await self.make_offer(GUILD, self.campaign.id, OWNER, BUYER, NOW)
        self.assertEqual(offer.delivered_at, NOW)
        self.assertEqual(await self.store.undelivered_offers(GUILD, NOW), [])
        self.assertIsNone(await self.store.claim_delivery(GUILD, offer.id, NOW))

    async def test_a_site_offer_is_announced_with_ids_only_and_claimed_once(self) -> None:
        listener = await self.db._pool.getconn()
        try:
            await listener.execute("LISTEN dmbot_handover_offers")
            offer = await self.site_offer()
            gen = listener.notifies(timeout=1)  # already queued at commit
            payloads = [n.payload async for n in gen]
        finally:
            await listener.execute("UNLISTEN *")
            await self.db._pool.putconn(listener)
        self.assertIn(f"{GUILD}:{offer.id}", payloads)  # no names: they skip row security
        self.assertIsNone(offer.delivered_at)
        self.assertEqual(await self.store.undelivered_offers(GUILD, NOW), [offer.id])
        claimed = await self.store.claim_delivery(GUILD, offer.id, NOW + 5)
        assert claimed is not None
        self.assertEqual(claimed.delivered_at, NOW + 5)
        self.assertIsNone(await self.store.claim_delivery(GUILD, offer.id, NOW + 6))
        self.assertEqual(await self.store.undelivered_offers(GUILD, NOW), [])

    async def test_two_claims_at_once_send_it_once(self) -> None:
        offer = await self.site_offer()
        both = await asyncio.gather(
            self.store.claim_delivery(GUILD, offer.id, NOW),
            self.store.claim_delivery(GUILD, offer.id, NOW),
        )
        self.assertEqual(sum(c is not None for c in both), 1)

    async def test_an_answered_or_expired_offer_is_never_sent(self) -> None:
        offer = await self.site_offer()
        later = NOW + HANDOVER_SECONDS
        self.assertEqual(await self.store.undelivered_offers(GUILD, later), [])
        self.assertIsNone(await self.store.claim_delivery(GUILD, offer.id, later))
        await self.store.withdraw_handover(GUILD, offer.id, OWNER, NOW)
        self.assertEqual(await self.store.undelivered_offers(GUILD, NOW), [])
        self.assertIsNone(await self.store.claim_delivery(GUILD, offer.id, NOW))

    async def test_another_server_neither_sees_nor_claims_it(self) -> None:
        offer = await self.site_offer()
        self.assertEqual(await self.store.undelivered_offers(OTHER_GUILD, NOW), [])
        self.assertIsNone(await self.store.claim_delivery(OTHER_GUILD, offer.id, NOW))
        self.assertEqual(await self.store.undelivered_offers(GUILD, NOW), [offer.id])

    async def test_a_refused_site_offer_announces_nothing_and_saves_nothing(self) -> None:
        await self.site_offer()
        with self.assertRaises(CampaignError):  # one open offer at a time
            await self.site_offer()
        self.assertEqual(len(await self.store.undelivered_offers(GUILD, NOW)), 1)


class Answering(HandoverTest):
    async def test_only_the_person_offered_accepts_while_it_is_open(self) -> None:
        offer = await self.offer()
        self.assertEqual(await self.store.accept_handover(GUILD, offer, CO_DM, NOW), "gone")
        late = NOW + HANDOVER_SECONDS
        self.assertEqual(await self.store.accept_handover(GUILD, offer, BUYER, late), "gone")
        unchanged = await self.store.get(GUILD, self.campaign.id)
        assert unchanged is not None
        self.assertEqual(unchanged.owner_user_id, OWNER)

    async def test_no_free_slot_is_checked_when_accepting(self) -> None:
        full: list[int] = []

        async def no_room(conn: Any, user_id: int, now: int) -> bool:
            full.append(user_id)
            return False

        offer = await self.offer()
        store = CampaignStore(self.db, has_free_slot=no_room)
        self.assertEqual(await store.accept_handover(GUILD, offer, BUYER, NOW), "no_free_slot")
        self.assertEqual(full, [BUYER])
        still = await store.get_offer(GUILD, offer, NOW)
        assert still is not None
        self.assertEqual(still.status, "open")  # they can free a slot and accept

    async def test_a_plan_that_stopped_since_the_offer_has_no_room(self) -> None:
        ends = NOW + 100
        await self.give_plan(NO_PLAN, period_end=ends)
        offer = await self.offer(to=NO_PLAN)  # their plan works now
        after = ends + RENEWAL_SLACK_SECONDS + 1  # stopped, but the offer is still open
        self.assertLess(after, NOW + HANDOVER_SECONDS)
        self.assertEqual(
            await self.store.accept_handover(GUILD, offer, NO_PLAN, after), "no_free_slot"
        )

    async def test_a_co_dm_can_take_it_over(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, BUYER)
        self.assertEqual(
            await self.store.accept_handover(GUILD, await self.offer(), BUYER, NOW), "accepted"
        )
        after = await self.store.get(GUILD, self.campaign.id)
        assert after is not None
        self.assertEqual(
            (after.owner_user_id, after.dm_user_ids), (BUYER, frozenset({OWNER, BUYER}))
        )

    async def test_accepting_and_declining_at_once_do_one_of_them(self) -> None:
        offer = await self.offer()
        results = await asyncio.gather(
            self.store.accept_handover(GUILD, offer, BUYER, NOW),
            self.store.decline_handover(GUILD, offer, BUYER, NOW),
        )
        self.assertIn(sorted(results), (["accepted", "gone"], ["declined", "gone"]))
        after = await self.store.get(GUILD, self.campaign.id)
        assert after is not None
        self.assertEqual(after.owner_user_id, BUYER if "accepted" in results else OWNER)

    async def test_an_offer_past_its_days_reads_as_expired_and_closes_nothing(self) -> None:
        offer = await self.offer()
        late = NOW + HANDOVER_SECONDS  # exactly 7 days on: over
        seen = await self.store.get_offer(GUILD, offer, late)
        assert seen is not None
        self.assertEqual((seen.status, seen.decided_at), ("expired", late))
        self.assertEqual(await self.store.decline_handover(GUILD, offer, BUYER, late), "gone")
        self.assertEqual(await self.store.withdraw_handover(GUILD, offer, OWNER, late), "gone")
        async with self.db.guild(GUILD) as conn:  # marked so for the website's lists
            cur = await conn.execute(
                "SELECT status FROM campaign_handover_offers WHERE id = %s", (offer,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["status"], "expired")

    async def test_declining_and_withdrawing(self) -> None:
        offer = await self.offer()
        self.assertEqual(await self.store.decline_handover(GUILD, offer, OWNER, NOW), "gone")
        self.assertEqual(await self.store.decline_handover(GUILD, offer, BUYER, NOW), "declined")
        self.assertEqual(await self.store.accept_handover(GUILD, offer, BUYER, NOW), "gone")
        offer = await self.offer()
        self.assertEqual(await self.store.withdraw_handover(GUILD, offer, BUYER, NOW), "gone")
        self.assertEqual(await self.store.withdraw_handover(GUILD, offer, OWNER, NOW), "withdrawn")
        self.assertEqual(await self.store.decline_handover(GUILD, offer, BUYER, NOW), "gone")

    async def test_an_offer_from_someone_no_longer_the_owner_is_gone(self) -> None:
        offer = await self.offer()
        async with self.db.guild(GUILD) as conn:  # the owner changed some other way since
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s AND id = %s",
                (CO_DM, GUILD, self.campaign.id),
            )
        self.assertEqual(await self.store.accept_handover(GUILD, offer, BUYER, NOW), "gone")

    async def test_accepting_twice_at_once_takes_it_once(self) -> None:
        first = await self.offer()
        # Two presses at the same moment (two devices): one wins, the other finds it gone.
        results = await asyncio.gather(
            self.store.accept_handover(GUILD, first, BUYER, NOW),
            self.store.accept_handover(GUILD, first, BUYER, NOW),
        )
        self.assertEqual(sorted(results), ["accepted", "gone"])


class Owners(HandoverTest):
    async def test_the_owner_cannot_be_removed_as_a_dm(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        with self.assertRaisesRegex(CampaignError, re.escape(OWNER_STAYS)):
            await self.store.remove_dm(GUILD, self.campaign.id, OWNER)
        await self.store.remove_dm(GUILD, self.campaign.id, CO_DM)  # anyone else can go

    async def test_a_dm_takes_on_a_campaign_with_no_owner(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        async with self.db.guild(GUILD) as conn:  # from before owners were recorded
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD, self.campaign.id),
            )
        self.assertEqual(
            await self.store.take_ownership(GUILD, self.campaign.id, BUYER, NOW), "gone"
        )  # not one of its DMs
        self.assertEqual(
            await self.store.take_ownership(GUILD, self.campaign.id, CO_DM, NOW), "taken"
        )
        self.assertEqual(
            await self.store.take_ownership(GUILD, self.campaign.id, OWNER, NOW), "gone"
        )  # it has one now
        taken = await self.store.get(GUILD, self.campaign.id)
        assert taken is not None
        self.assertEqual(taken.owner_user_id, CO_DM)

    async def test_two_dms_taking_it_on_at_once_give_it_one_owner(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD, self.campaign.id),
            )
        results = await asyncio.gather(
            self.store.take_ownership(GUILD, self.campaign.id, OWNER, NOW),
            self.store.take_ownership(GUILD, self.campaign.id, CO_DM, NOW),
        )
        self.assertEqual(sorted(results), ["gone", "taken"])

    async def test_taking_it_on_needs_room(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD, self.campaign.id),
            )

        async def no_room(conn: Any, user_id: int, now: int) -> bool:
            return False

        store = CampaignStore(self.db, has_free_slot=no_room)
        self.assertEqual(
            await store.take_ownership(GUILD, self.campaign.id, OWNER, NOW), "no_free_slot"
        )
