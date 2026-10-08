"""🤝 Hand over, the Discord side (#437 part 1b): the owner offers from ⚙️ Settings, the
person offered answers in a private message, and an offer DMbot can't deliver is taken
back. The rules are CampaignStore's (tested with a database in test_campaign_handover);
here, what each button says and calls, with fakes. Each callback answers Discord before
the database (Discord waits 3 seconds), and a failure after saving never says it failed."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.models import HandoverOffer
from dmbot.campaigns.store import NO_OWNER_YET, NOT_THE_OWNER, OFFER_WAITING
from dmbot.dm_screen import handover
from dmbot.dm_screen.handover import (
    AcceptOfferButton,
    DeclineOfferButton,
    HandoverButton,
    NotNowButton,
    PickNewOwner,
    TakeOnButton,
    WithdrawOfferButton,
)

GUILD, OTHER_GUILD, OWNER, BUYER, OTHER = 111, 222, 7, 9, 8
CAMPAIGN_ID = "c" * 32
OFFER_ID = 42


def campaign(owner: int | None = OWNER) -> Campaign:
    return Campaign(
        CAMPAIGN_ID, GUILD, "Frost*maiden", 0, None, "2024", "2014", True,
        frozenset({OWNER, OTHER}), None, None, "peek", owner_user_id=owner,
    )  # fmt: skip


def offer(status: str = "open", created_at: int | None = None) -> HandoverOffer:
    return HandoverOffer(
        OFFER_ID, GUILD, CAMPAIGN_ID, OWNER, BUYER, "Oskar", "Mirelle",
        handover._now() if created_at is None else created_at, status, None,  # type: ignore[arg-type]
    )  # fmt: skip


def interaction(user: int, *, guild: bool = True) -> Any:
    """A press, recording the order of answering Discord and reading the database."""
    order = MagicMock()
    store = MagicMock(spec=CampaignStore)
    store.get = AsyncMock(return_value=campaign())
    store.get_offer = AsyncMock(return_value=offer())
    for name in (
        "offer_handover", "accept_handover", "decline_handover", "withdraw_handover",
        "take_ownership", "open_offer",
    ):  # fmt: skip
        setattr(store, name, AsyncMock())
    order.attach_mock(store.get, "get")
    order.attach_mock(store.get_offer, "get_offer")
    it = MagicMock()
    it.user = MagicMock(spec=discord.Member, id=user, display_name=f"Person {user}")
    the_guild = MagicMock(spec=discord.Guild, id=GUILD)
    the_guild.name = "The Table"
    member = MagicMock()
    member.send = AsyncMock()
    the_guild.get_member = MagicMock(return_value=member)
    the_guild.fetch_member = AsyncMock(return_value=member)
    it.guild = the_guild if guild else None
    it.client = MagicMock(campaigns=store)
    it.client.get_guild = MagicMock(return_value=the_guild)
    it.client.fetch_guild = AsyncMock(return_value=the_guild)
    done = {"yes": False}

    async def answered(*_: Any, **__: Any) -> None:
        done["yes"] = True

    it.response.send_message = AsyncMock(side_effect=answered)
    it.response.edit_message = AsyncMock(side_effect=answered)
    it.response.defer = AsyncMock(side_effect=answered)
    it.response.is_done = MagicMock(side_effect=lambda: done["yes"])
    order.attach_mock(it.response.defer, "defer")
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    it.member = member  # the person DMbot messages, for checks
    it.order = order
    return it


def said(it: Any) -> str:
    """The last private reply, before or after answering Discord."""
    call = it.followup.send.await_args or it.response.send_message.await_args
    return str(call.args[0])


def shown(it: Any) -> str:
    return str(it.edit_original_response.await_args.kwargs["content"])


def answered_first(test: unittest.TestCase, it: Any) -> None:
    names = [c[0] for c in it.order.mock_calls]
    test.assertEqual(names[0], "defer", names)


class Words(unittest.TestCase):
    def test_the_card_says_whose_plan_it_uses(self) -> None:
        line = handover.owner_line(campaign(), None)
        self.assertIn(f"<@{OWNER}>", line)
        self.assertIn("uses their plan's hours", line)
        self.assertIn("none yet", handover.owner_line(campaign(owner=None), None))
        self.assertIn("Offered to **Mirelle**; waiting", handover.owner_line(campaign(), offer()))

    def test_one_button_hand_over_or_take_back(self) -> None:
        (hand,) = handover.owner_buttons(campaign(), None)
        self.assertIsInstance(hand, HandoverButton)
        (back,) = handover.owner_buttons(campaign(), offer())
        self.assertIsInstance(back, WithdrawOfferButton)

    def test_every_button_works_after_a_restart_and_fits_a_phone(self) -> None:
        items: list[Any] = [
            HandoverButton(CAMPAIGN_ID),
            WithdrawOfferButton(GUILD, OFFER_ID),
            AcceptOfferButton(GUILD, OFFER_ID),
            DeclineOfferButton(GUILD, OFFER_ID),
            TakeOnButton(CAMPAIGN_ID),
            NotNowButton(CAMPAIGN_ID),
        ]
        for item in items:
            template = type(item).__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)), item)
            self.assertLessEqual(len(item.item.label), 25)
            self.assertLessEqual(len(str(item.item.custom_id)), 100)
        self.assertIsNone(  # "Take it on" and "Not now" never answer for each other
            TakeOnButton.__discord_ui_compiled_template__.fullmatch(
                str(NotNowButton(CAMPAIGN_ID).item.custom_id)
            )
        )


class Offering(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_owner_gets_the_picker(self) -> None:
        it = interaction(OTHER)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        answered_first(self, it)
        self.assertEqual(said(it), NOT_THE_OWNER)
        it = interaction(OWNER)
        it.client.campaigns.get.return_value = campaign(owner=None)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(said(it), NO_OWNER_YET)
        it = interaction(OWNER)
        it.client.campaigns.get.return_value = None
        await HandoverButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(said(it), handover.ENDED_CAMPAIGN)
        it = interaction(OWNER)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        sent = it.followup.send.await_args
        self.assertIsInstance(sent.kwargs["view"], PickNewOwner)
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertIn("Nothing changes until they accept", sent.args[0])

    async def test_a_break_before_answering_still_says_so(self) -> None:
        it = interaction(OWNER)
        it.response.defer.side_effect = RuntimeError("discord")
        with self.assertLogs("dmbot.dm_screen.handover", "ERROR"):
            await HandoverButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], handover.FAILED)

    def picker(self, picked: Any) -> PickNewOwner:
        view = PickNewOwner(campaign())
        view.pick._values = [picked]  # what Discord fills in
        return view

    def person(self, user_id: int, *, bot: bool = False) -> Any:
        return MagicMock(id=user_id, bot=bot, display_name="Mirelle")

    async def test_picking_someone_sends_them_the_offer(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.return_value = offer()
        await self.picker(self.person(BUYER))._picked(it)
        call = it.client.campaigns.offer_handover.await_args
        self.assertEqual(call.args[:4], (GUILD, CAMPAIGN_ID, OWNER, BUYER))
        self.assertEqual(call.kwargs, {"from_name": f"Person {OWNER}", "to_name": "Mirelle"})
        sent = it.member.send.await_args
        self.assertIn(
            "**Oskar** is asking you to pay for the campaign **Frost\\*maiden**", sent.args[0]
        )
        self.assertIn("Nothing changes unless you tap **Accept**", sent.args[0])
        self.assertIn("see behind the DM screen (spoilers", sent.args[0])
        self.assertIn("uses your plan's hours", sent.args[0])
        self.assertEqual(
            [type(i) for i in sent.kwargs["view"].children], [AcceptOfferButton, DeclineOfferButton]
        )
        self.assertEqual(sent.kwargs["allowed_mentions"], handover.NO_PINGS)
        done = it.edit_original_response.await_args.kwargs  # the picker itself becomes this
        self.assertTrue(done["content"].startswith("Offer sent to **Mirelle**."))
        self.assertIsNone(done["view"])

    async def test_an_offer_they_cant_get_is_taken_back(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.return_value = offer()
        it.client.campaigns.withdraw_handover.return_value = "withdrawn"
        it.member.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
        with self.assertLogs("dmbot.dm_screen.handover", "INFO"):
            await self.picker(self.person(BUYER))._picked(it)
        self.assertEqual(
            it.client.campaigns.withdraw_handover.await_args.args[:3], (GUILD, OFFER_ID, OWNER)
        )
        self.assertIn("so the offer was taken back", shown(it))

    async def test_an_offer_that_cant_be_taken_back_says_how(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.return_value = offer()
        it.client.campaigns.withdraw_handover.side_effect = RuntimeError("db")
        guild = it.guild
        guild.get_member.return_value = None
        guild.fetch_member.side_effect = discord.NotFound(MagicMock(status=404), "gone")
        with self.assertLogs("dmbot.dm_screen.handover", "INFO"):
            await self.picker(self.person(BUYER))._picked(it)
        self.assertIn("Take it back yourself: ⚙️ Settings", shown(it))

    async def test_a_bot_or_a_refusal_says_why(self) -> None:
        it = interaction(OWNER)
        await self.picker(self.person(99, bot=True))._picked(it)
        self.assertEqual(said(it), handover.NOT_A_PERSON)
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.side_effect = CampaignError(OFFER_WAITING)
        await self.picker(self.person(BUYER))._picked(it)
        self.assertEqual(said(it), OFFER_WAITING)
        it.member.send.assert_not_awaited()

    def test_a_waiting_offer_names_the_button_that_takes_it_back(self) -> None:
        for text in (OFFER_WAITING, handover.OFFER_SENT, handover.UNREACHABLE_STUCK):
            self.assertIn(f"**{handover.WITHDRAW_LABEL}**", text)
        self.assertIn(handover.HANDOVER_LABEL, handover.UNREACHABLE)  # try again with it

    async def test_an_offer_answered_meanwhile_is_not_called_taken_back(self) -> None:
        # Answered on the website in the gap (#690): "gone", so not "taken back".
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.return_value = offer()
        it.client.campaigns.withdraw_handover.return_value = "gone"
        it.member.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
        with self.assertLogs("dmbot.dm_screen.handover", "INFO"):
            await self.picker(self.person(BUYER))._picked(it)
        self.assertEqual(shown(it), handover.UNREACHABLE_ANSWERED.format(name="Mirelle"))

    async def test_a_message_that_cant_be_edited_is_said_anew(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover.return_value = offer()
        it.edit_original_response.side_effect = discord.HTTPException(MagicMock(status=500), "x")
        await self.picker(self.person(BUYER))._picked(it)
        self.assertTrue(said(it).startswith("Offer sent to **Mirelle**."))


class TakingBack(unittest.IsolatedAsyncioTestCase):
    async def test_the_owner_takes_it_back_and_the_card_is_redrawn(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.withdraw_handover.return_value = "withdrawn"
        await WithdrawOfferButton(GUILD, OFFER_ID).callback(it)
        answered_first(self, it)
        self.assertEqual(
            it.client.campaigns.withdraw_handover.await_args.args[:3], (GUILD, OFFER_ID, OWNER)
        )
        self.assertIn("**Owner:**", shown(it))  # the card, as it is now
        self.assertIn("Offer taken back", said(it))
        self.assertIn("**Oskar** took back the offer", it.member.send.await_args.args[0])

    async def test_a_co_dm_is_told_who_can(self) -> None:
        it = interaction(OTHER)
        await WithdrawOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), "Only **Oskar**, who made this offer, can take it back.")
        it.client.campaigns.withdraw_handover.assert_not_awaited()

    async def test_only_in_its_own_server(self) -> None:
        it = interaction(OWNER)
        await WithdrawOfferButton(OTHER_GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.NOT_HERE)
        it.client.campaigns.get_offer.assert_not_awaited()

    async def test_an_ended_offer_says_so(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.get_offer.return_value = offer(status="declined")
        await WithdrawOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("This offer has ended", said(it))
        it.client.campaigns.withdraw_handover.assert_not_awaited()


class Answering(unittest.IsolatedAsyncioTestCase):
    async def test_accepting_takes_it_and_tells_the_owner(self) -> None:
        it = interaction(BUYER, guild=False)  # in a private message
        it.client.campaigns.accept_handover.return_value = "accepted"
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        answered_first(self, it)
        self.assertEqual(
            it.client.campaigns.accept_handover.await_args.args[:3], (GUILD, OFFER_ID, BUYER)
        )
        done = it.edit_original_response.await_args.kwargs
        self.assertIn("Done: **Frost\\*maiden** uses your plan now", done["content"])
        self.assertIn("**Oskar** is still one of its DMs", done["content"])
        self.assertIsNone(done["view"])  # the buttons go
        self.assertIn("**Mirelle** accepted", it.member.send.await_args.args[0])

    async def test_pressed_again_says_it_again(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.get_offer.return_value = offer(status="accepted")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("uses your plan now", shown(it))
        it.client.campaigns.accept_handover.assert_not_awaited()

    async def test_no_room_keeps_the_offer_and_says_what_to_do(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover.return_value = "no_free_slot"
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("no room for another campaign", said(it))
        it.edit_original_response.assert_not_awaited()  # Accept stays

    async def test_an_ended_or_someone_elses_offer_says_so(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover.return_value = "gone"
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("This offer has ended", shown(it))
        it = interaction(OTHER, guild=False)  # not the person offered
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("This offer has ended", shown(it))
        it.client.campaigns.accept_handover.assert_not_awaited()

    async def test_someone_who_left_the_server_cant_accept(self) -> None:
        it = interaction(BUYER, guild=False)
        guild = it.client.get_guild.return_value
        guild.fetch_member.side_effect = discord.NotFound(MagicMock(status=404), "gone")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.NOT_A_MEMBER)
        it.client.campaigns.accept_handover.assert_not_awaited()

    async def test_a_server_another_process_serves_is_looked_up(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.get_guild.return_value = None  # sharding: not cached here
        it.client.campaigns.accept_handover.return_value = "accepted"
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        it.client.fetch_guild.assert_awaited_once_with(GUILD)
        self.assertIn("uses your plan now", shown(it))
        it = interaction(BUYER, guild=False)
        it.client.get_guild.return_value = None
        it.client.fetch_guild.side_effect = discord.NotFound(MagicMock(status=404), "left")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.SERVER_GONE)

    async def test_accept_never_goes_ahead_unchecked(self) -> None:
        # Discord couldn't say whether they're still in the server: ask again later.
        it = interaction(BUYER, guild=False)
        guild = it.client.get_guild.return_value
        guild.fetch_member.side_effect = discord.HTTPException(MagicMock(status=503), "busy")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.NOT_HERE)
        it.client.campaigns.accept_handover.assert_not_awaited()

    async def test_a_server_that_removed_dmbot_is_gone(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.get_guild.return_value = None
        it.client.fetch_guild.side_effect = discord.Forbidden(MagicMock(status=403), "no access")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.SERVER_GONE)
        it.client.campaigns.accept_handover.assert_not_awaited()
        it = interaction(BUYER, guild=False)  # No thanks the same
        it.client.get_guild.return_value = None
        it.client.fetch_guild.side_effect = discord.Forbidden(MagicMock(status=403), "no access")
        await DeclineOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.SERVER_GONE)
        it.client.campaigns.decline_handover.assert_not_awaited()

    async def test_an_accept_that_cant_edit_its_message_still_says_done(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover.return_value = "accepted"
        it.edit_original_response.side_effect = discord.HTTPException(MagicMock(status=500), "x")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("uses your plan now", said(it))

    async def test_a_break_says_so_privately(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.get_offer.side_effect = RuntimeError("db")
        with self.assertLogs("dmbot.dm_screen.handover", "ERROR"):
            await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(said(it), handover.FAILED)
        it.client.campaigns.accept_handover.assert_not_awaited()  # nothing saved

    async def test_no_thanks_tells_the_owner(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.decline_handover.return_value = "declined"
        await DeclineOfferButton(GUILD, OFFER_ID).callback(it)
        answered_first(self, it)
        self.assertIn("You said no thanks. Nothing changed.", shown(it))
        self.assertIn("**Mirelle** said no thanks", it.member.send.await_args.args[0])
        it = interaction(BUYER, guild=False)
        it.client.campaigns.get_offer.return_value = offer(status="declined")
        await DeclineOfferButton(GUILD, OFFER_ID).callback(it)  # pressed again
        self.assertIn("You said no thanks", shown(it))
        it.client.campaigns.decline_handover.assert_not_awaited()


class TakingItOn(unittest.IsolatedAsyncioTestCase):
    async def test_start_asks_only_a_dm_of_a_campaign_with_no_owner(self) -> None:
        it = interaction(OWNER)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        it.followup.send.assert_not_awaited()  # it has an owner
        it = interaction(BUYER)  # not one of its DMs
        it.client.campaigns.get.return_value = campaign(owner=None)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        it.followup.send.assert_not_awaited()
        it = interaction(OTHER)
        it.client.campaigns.get.return_value = campaign(owner=None)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        sent = it.followup.send.await_args
        self.assertIn("needs an owner", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertEqual(
            [type(i) for i in sent.kwargs["view"].children], [TakeOnButton, NotNowButton]
        )

    async def test_a_failed_check_asks_nothing(self) -> None:
        it = interaction(OTHER)
        it.client.campaigns.get.side_effect = RuntimeError("db")
        with self.assertLogs("dmbot.dm_screen.handover", "ERROR"):
            await handover.ask_to_take_on(it, CAMPAIGN_ID)
        it.followup.send.assert_not_awaited()

    async def test_taking_it_on(self) -> None:
        for result, words in [("taken", "You own **Frost\\*maiden** now"), ("gone", "already")]:
            it = interaction(OTHER)
            it.client.campaigns.take_ownership.return_value = result
            await TakeOnButton(CAMPAIGN_ID).callback(it)
            answered_first(self, it)
            self.assertIn(words, shown(it))
        it = interaction(OTHER)
        it.client.campaigns.take_ownership.return_value = "no_free_slot"
        await TakeOnButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(said(it), handover.TAKE_NO_ROOM)

    async def test_not_now_changes_nothing(self) -> None:
        it = interaction(OTHER)
        await NotNowButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], handover.NOT_NOW)
        it.client.campaigns.take_ownership.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
