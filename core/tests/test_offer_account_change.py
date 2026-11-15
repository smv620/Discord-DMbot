"""A hand-over offer closes, with a notice, when one of its people's accounts changes (#2533, slice
3; migration 0503): the person offered to joins another account, the owner's login is taken off,
or both end up on one account. Needs Postgres; invented numbers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import handover, site_offers
from dmbot.mentions import NO_PINGS
from tests.pg import DatabaseTest
from tests.test_account_discord_ids import add_user, link, unlink

GUILD = 1
OWNER, SECOND, PERSON, OTHER = 8001, 8002, 8003, 8004
NOW = 1_700_000_000


class Closing(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        await add_user(self.db, OWNER)
        self.assertEqual(await link(self.db, OWNER, SECOND), "ok")  # a second login of the owner
        self.campaign = await self.store.create(GUILD, "Frostmaiden", OWNER)

    async def offer(self, by: int = OWNER, to: int = PERSON) -> int:
        made = await self.store.offer_handover(
            GUILD, self.campaign.id, by, to, NOW, from_name="Owner", to_name="Person"
        )
        return made.id

    async def ended(self) -> list[Any]:
        return await self.store.offers_to_end(GUILD, NOW + 60)

    async def test_an_offer_whose_people_did_not_change_stays_open(self) -> None:
        await self.offer()
        self.assertEqual(await self.ended(), [])
        self.assertEqual(await self.store.close_changed_offers(GUILD, NOW + 60), 0)

    async def test_the_person_offered_to_joins_another_account_and_the_offer_closes(self) -> None:
        offer_id = await self.offer()
        await add_user(self.db, OTHER)
        self.assertEqual(await link(self.db, OTHER, PERSON), "ok")
        (closed,) = await self.ended()
        self.assertEqual(
            (closed.id, closed.status, closed.closed_side), (offer_id, "closed", "receiver")
        )
        self.assertIsNone(
            await self.store.open_offer(GUILD, self.campaign.id, NOW + 60)
        )  # nothing is open

    async def test_the_owners_added_login_is_taken_off_and_the_offer_it_made_closes(self) -> None:
        await self.offer(by=SECOND)
        self.assertEqual(await unlink(self.db, OWNER, SECOND), "ok")
        (closed,) = await self.ended()
        self.assertEqual((closed.status, closed.closed_side), ("closed", "offerer"))

    async def test_both_ending_up_on_one_account_closes_it_as_the_same(self) -> None:
        await self.offer()
        self.assertEqual(await link(self.db, OWNER, PERSON), "ok")  # the person joins the owner
        (closed,) = await self.ended()
        self.assertEqual(closed.closed_side, "same")

    async def test_the_new_account_cannot_accept_before_the_sweep_closes_it(self) -> None:
        offer_id = await self.offer()
        await add_user(self.db, OTHER)
        self.assertEqual(await link(self.db, OTHER, PERSON), "ok")  # the person joins another
        self.assertEqual(
            await self.store.accept_handover(GUILD, offer_id, PERSON, NOW + 30), "gone"
        )
        self.assertEqual(await self.store.accept_handover(GUILD, offer_id, OTHER, NOW + 30), "gone")
        campaign = await self.store.get(GUILD, self.campaign.id)
        assert campaign is not None
        self.assertEqual(campaign.owner_user_id, OWNER)  # (nothing moved)

    async def test_a_person_whose_first_login_is_taken_off_closes_it_for_the_receiver(self) -> None:
        await add_user(self.db, PERSON)
        self.assertEqual(await link(self.db, PERSON, OTHER), "ok")  # so a login may be removed
        offer_id = await self.offer()
        self.assertEqual(await unlink(self.db, PERSON, PERSON), "ok")
        (closed,) = await self.ended()
        self.assertEqual((closed.id, closed.closed_side), (offer_id, "receiver"))

    async def test_an_owner_whose_first_login_is_taken_off_closes_it_for_the_offerer(self) -> None:
        await self.offer()  # (made from OWNER, the account's first login; SECOND is its other)
        self.assertEqual(await unlink(self.db, OWNER, OWNER), "ok")
        (closed,) = await self.ended()
        self.assertEqual(closed.closed_side, "offerer")

    async def test_an_offer_is_told_about_once_and_not_closed_again(self) -> None:
        await self.offer()
        await add_user(self.db, OTHER)
        await link(self.db, OTHER, PERSON)
        (closed,) = await self.ended()
        self.assertTrue(await self.store.claim_end_notice(GUILD, closed.id, NOW + 61))
        self.assertEqual(await self.ended(), [])
        self.assertEqual(await self.store.close_changed_offers(GUILD, NOW + 62), 0)


class Telling(DatabaseTest):
    """Which pair of words each side gets, by whose account changed."""

    def guild_with(self) -> tuple[Any, dict[int, AsyncMock], Any]:
        sent: dict[int, AsyncMock] = {}

        def member(user_id: int) -> Any:
            sent[user_id] = AsyncMock()
            return SimpleNamespace(
                send=sent[user_id], dm_channel=None, create_dm=AsyncMock(side_effect=OSError)
            )

        async def get(_guild: Any, user_id: int) -> Any:
            return member(user_id)

        return SimpleNamespace(), sent, get

    async def told(self, side: str) -> dict[int, str]:
        guild, sent, get = self.guild_with()
        offer = SimpleNamespace(
            from_user_id=OWNER,
            to_user_id=PERSON,
            closed_side=side,
            message_id=None,
            via_email=False,
            delivered_at=1,
        )
        campaign = SimpleNamespace(name="Frostmaiden")
        with patch.object(site_offers, "_member", get):
            await site_offers._tell_closed(guild, offer, campaign)  # type: ignore[arg-type]
        return {uid: m.await_args.args[0] for uid, m in sent.items() if m.await_args}

    async def test_the_owners_account_changed(self) -> None:
        got = await self.told("offerer")
        self.assertEqual(got[OWNER], handover.CLOSED_DM_TO_DM.format(campaign="Frostmaiden"))
        self.assertEqual(got[PERSON], handover.CLOSED_DM_TO_RECEIVER.format(campaign="Frostmaiden"))

    async def test_the_receivers_account_changed(self) -> None:
        got = await self.told("receiver")
        self.assertEqual(got[OWNER], handover.CLOSED_RECEIVER_TO_DM.format(campaign="Frostmaiden"))
        self.assertEqual(
            got[PERSON], handover.CLOSED_RECEIVER_TO_RECEIVER.format(campaign="Frostmaiden")
        )

    async def test_an_offer_never_sent_tells_only_the_owner(self) -> None:
        guild, sent, get = self.guild_with()
        offer = SimpleNamespace(
            from_user_id=OWNER,
            to_user_id=PERSON,
            closed_side="receiver",
            message_id=None,
            via_email=False,
            delivered_at=None,
        )
        with patch.object(site_offers, "_member", get):
            await site_offers._tell_closed(guild, offer, SimpleNamespace(name="Frostmaiden"))  # type: ignore[arg-type]
        self.assertEqual(set(sent), {OWNER})

    async def test_one_account_is_told_once_with_the_first_words_only(self) -> None:
        got = await self.told("same")
        self.assertEqual(set(got), {OWNER})
        self.assertEqual(got[OWNER], handover.CLOSED_DM_TO_DM.format(campaign="Frostmaiden"))

    async def test_the_persons_offer_message_loses_its_buttons_and_says_it_was_closed(
        self,
    ) -> None:
        # With the offer's own message on record, the person's message is edited in place.
        partial = SimpleNamespace(edit=AsyncMock())
        asked: list[int] = []

        def record(mid: int) -> Any:
            asked.append(mid)
            return partial

        people: dict[int, Any] = {}
        guild: Any = SimpleNamespace()
        campaign: Any = SimpleNamespace(name="Frostmaiden")

        async def get(_guild: Any, user_id: int) -> Any:
            people[user_id] = SimpleNamespace(
                send=AsyncMock(),
                dm_channel=SimpleNamespace(get_partial_message=lambda mid: record(mid))
                if user_id == PERSON
                else None,
                create_dm=AsyncMock(side_effect=OSError),
            )
            return people[user_id]

        offer = SimpleNamespace(
            from_user_id=OWNER,
            to_user_id=PERSON,
            closed_side="receiver",
            message_id=555,
            via_email=False,
            delivered_at=1,
        )
        with patch.object(site_offers, "_member", get):
            await site_offers._tell_closed(guild, offer, campaign)  # type: ignore[arg-type]
        self.assertEqual(asked, [555])  # the offer's own message, not another one
        partial.edit.assert_awaited_once()
        kwargs = partial.edit.await_args.kwargs
        self.assertIs(kwargs["allowed_mentions"], NO_PINGS)
        self.assertEqual(
            kwargs["content"], handover.CLOSED_RECEIVER_TO_RECEIVER.format(campaign="Frostmaiden")
        )
        self.assertIsNone(kwargs["view"])  # the buttons are gone
        people[PERSON].send.assert_not_awaited()  # no new message for the person
        people[OWNER].send.assert_awaited_once()
        self.assertEqual(
            cast(Any, people[OWNER].send.await_args).args[0],
            handover.CLOSED_RECEIVER_TO_DM.format(campaign="Frostmaiden"),
        )

    async def test_without_an_offer_message_the_person_gets_a_new_private_message(self) -> None:
        guild, sent, get = self.guild_with()
        campaign: Any = SimpleNamespace(name="Frostmaiden")
        offer: Any = SimpleNamespace(
            from_user_id=OWNER,
            to_user_id=PERSON,
            closed_side="receiver",
            message_id=None,
            via_email=False,
            delivered_at=1,
        )
        with patch.object(site_offers, "_member", get):
            await site_offers._tell_closed(guild, offer, campaign)
        sent[PERSON].assert_awaited_once()
        self.assertEqual(
            cast(Any, sent[PERSON].await_args).args[0],
            handover.CLOSED_RECEIVER_TO_RECEIVER.format(campaign="Frostmaiden"),
        )
