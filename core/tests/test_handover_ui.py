"""🤝 Hand over, the Discord side (#437 part 1b): the owner offers from ⚙️ Settings, the
person offered answers in a private message, and an offer DMbot can't deliver is taken
back. The rules are CampaignStore's (tested with a database in test_campaign_handover);
here, what each button says and calls, with fakes."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.campaigns.models import HandoverOffer
from dmbot.campaigns.store import NO_OWNER_YET, NOT_THE_OWNER
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

GUILD, OWNER, BUYER, OTHER = 111, 7, 9, 8
CAMPAIGN_ID = "c" * 32
OFFER_ID = 42


def campaign(owner: int | None = OWNER) -> Campaign:
    return Campaign(
        CAMPAIGN_ID, GUILD, "Frost*maiden", 0, None, "2024", "2014", True,
        frozenset({OWNER, OTHER}), None, None, "peek", owner_user_id=owner,
    )  # fmt: skip


def offer(status: str = "open") -> HandoverOffer:
    return HandoverOffer(
        OFFER_ID, GUILD, CAMPAIGN_ID, OWNER, BUYER, "Oskar", "Mirelle", 1000,
        status, None,  # type: ignore[arg-type]
    )  # fmt: skip


def interaction(user: int, *, guild: bool = True) -> Any:
    store = MagicMock()
    store.get = AsyncMock(return_value=campaign())
    store.get_offer = AsyncMock(return_value=offer())
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
    it.response.send_message = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.is_done = MagicMock(return_value=True)
    it.followup.send = AsyncMock()
    it.followup.edit_message = AsyncMock()
    it.edit_original_response = AsyncMock()
    it.message = None
    it.member = member  # the person DMbot messages, for checks
    return it


class Words(unittest.TestCase):
    def test_the_card_says_whose_plan_it_uses(self) -> None:
        line = handover.owner_line(campaign(), None)
        self.assertIn(f"<@{OWNER}>", line)
        self.assertIn("uses their plan", line)
        self.assertIn("none yet", handover.owner_line(campaign(owner=None), None))
        waiting = handover.owner_line(campaign(), offer())
        self.assertIn("Offered to **Mirelle** until <t:", waiting)

    def test_one_button_hand_over_or_withdraw(self) -> None:
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
        # "Take it on" and "Not now" never answer for each other.
        self.assertIsNone(
            TakeOnButton.__discord_ui_compiled_template__.fullmatch(
                str(NotNowButton(CAMPAIGN_ID).item.custom_id)
            )
        )


class Offering(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_owner_sees_the_picker(self) -> None:
        it = interaction(OTHER)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], NOT_THE_OWNER)
        it = interaction(OWNER)
        it.client.campaigns.get.return_value = campaign(owner=None)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], NO_OWNER_YET)
        it = interaction(OWNER)
        await HandoverButton(CAMPAIGN_ID).callback(it)
        sent = it.response.send_message.await_args
        self.assertIsInstance(sent.kwargs["view"], PickNewOwner)
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertIn("Frost\\*maiden", sent.args[0])

    def picker(self, picked: Any) -> PickNewOwner:
        view = PickNewOwner(campaign())
        view.pick._values = [picked]  # what Discord fills in
        return view

    def person(self, user_id: int, *, bot: bool = False) -> Any:
        return MagicMock(id=user_id, bot=bot, display_name="Mirelle")

    async def test_picking_someone_sends_them_the_offer(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover = AsyncMock(return_value=offer())
        await self.picker(self.person(BUYER))._picked(it)
        call = it.client.campaigns.offer_handover.await_args
        self.assertEqual(call.args[:4], (GUILD, CAMPAIGN_ID, OWNER, BUYER))
        self.assertEqual(call.kwargs, {"from_name": f"Person {OWNER}", "to_name": "Mirelle"})
        sent = it.member.send.await_args
        self.assertIn("**Oskar** wants to hand you the campaign **Frost\\*maiden**", sent.args[0])
        self.assertIn("**The Table**", sent.args[0])
        buttons = [type(i) for i in sent.kwargs["view"].children]
        self.assertEqual(buttons, [AcceptOfferButton, DeclineOfferButton])
        told = it.edit_original_response.await_args.kwargs["content"]
        self.assertTrue(told.startswith("Offer sent. **Mirelle** has 7 days"))

    async def test_an_offer_they_cant_get_is_taken_back(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.offer_handover = AsyncMock(return_value=offer())
        it.client.campaigns.withdraw_handover = AsyncMock(return_value="withdrawn")
        it.member.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
        with self.assertLogs("dmbot.dm_screen.handover", "INFO"):
            await self.picker(self.person(BUYER))._picked(it)
        it.client.campaigns.withdraw_handover.assert_awaited_once()
        self.assertEqual(
            it.client.campaigns.withdraw_handover.await_args.args[:3], (GUILD, OFFER_ID, OWNER)
        )
        told = it.edit_original_response.await_args.kwargs["content"]
        self.assertIn("couldn't message **Mirelle**", told)
        self.assertIn("taken back", told)

    async def test_a_bot_or_a_refusal_says_why(self) -> None:
        it = interaction(OWNER)
        await self.picker(self.person(99, bot=True))._picked(it)
        self.assertEqual(it.response.send_message.await_args.args[0], handover.NOT_A_PERSON)
        it = interaction(OWNER)
        it.client.campaigns.offer_handover = AsyncMock(side_effect=CampaignError("No plan."))
        await self.picker(self.person(BUYER))._picked(it)
        self.assertEqual(it.followup.send.await_args.args[0], "No plan.")
        it.member.send.assert_not_awaited()

    async def test_the_owner_withdraws_from_settings(self) -> None:
        it = interaction(OWNER)
        it.client.campaigns.withdraw_handover = AsyncMock(return_value="withdrawn")
        await WithdrawOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("Offer taken back", it.followup.send.await_args.args[0])
        it.client.campaigns.withdraw_handover = AsyncMock(return_value="gone")
        await WithdrawOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn("This offer has ended", it.followup.send.await_args.args[0])


class Answering(unittest.IsolatedAsyncioTestCase):
    async def test_accepting_takes_it_and_tells_the_owner(self) -> None:
        it = interaction(BUYER, guild=False)  # in a private message
        it.client.campaigns.accept_handover = AsyncMock(return_value="accepted")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        it.client.campaigns.accept_handover.assert_awaited_once()
        self.assertEqual(
            it.client.campaigns.accept_handover.await_args.args[:3], (GUILD, OFFER_ID, BUYER)
        )
        done = it.edit_original_response.await_args.kwargs
        self.assertIn("is yours now", done["content"])
        self.assertIsNone(done["view"])  # the buttons go
        self.assertIn("**Mirelle** accepted", it.member.send.await_args.args[0])

    async def test_no_room_keeps_the_offer_and_says_what_to_do(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover = AsyncMock(return_value="no_free_slot")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], handover.NO_FREE_SLOT)
        it.edit_original_response.assert_not_awaited()  # Accept stays

    async def test_an_ended_offer_says_so(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover = AsyncMock(return_value="gone")
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn(
            "This offer has ended", it.edit_original_response.await_args.kwargs["content"]
        )

    async def test_someone_who_left_the_server_cant_accept(self) -> None:
        it = interaction(BUYER, guild=False)
        guild = it.client.get_guild.return_value
        guild.fetch_member.side_effect = discord.NotFound(MagicMock(status=404), "gone")
        it.client.campaigns.accept_handover = AsyncMock()
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], handover.NOT_A_MEMBER)
        it.client.campaigns.accept_handover.assert_not_awaited()

    async def test_a_server_this_process_doesnt_serve(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.get_guild.return_value = None
        await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], handover.NOT_HERE)

    async def test_no_thanks_tells_the_owner(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.decline_handover = AsyncMock(return_value="declined")
        await DeclineOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertIn(
            "DMbot will tell **Oskar**", it.edit_original_response.await_args.kwargs["content"]
        )
        self.assertIn("**Mirelle** said no thanks", it.member.send.await_args.args[0])

    async def test_a_break_says_so_privately(self) -> None:
        it = interaction(BUYER, guild=False)
        it.client.campaigns.accept_handover = AsyncMock(side_effect=RuntimeError("db"))
        with self.assertLogs("dmbot.dm_screen.handover", "ERROR"):
            await AcceptOfferButton(GUILD, OFFER_ID).callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], handover.FAILED)


class TakingItOn(unittest.IsolatedAsyncioTestCase):
    async def test_start_asks_only_a_dm_of_a_campaign_with_no_owner(self) -> None:
        it = interaction(OWNER)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        it.followup.send.assert_not_awaited()  # it has an owner
        it.client.campaigns.get.return_value = campaign(owner=None)
        it = interaction(BUYER)  # not one of its DMs
        it.client.campaigns.get.return_value = campaign(owner=None)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        it.followup.send.assert_not_awaited()
        it = interaction(OTHER)
        it.client.campaigns.get.return_value = campaign(owner=None)
        await handover.ask_to_take_on(it, CAMPAIGN_ID)
        sent = it.followup.send.await_args
        self.assertIn("has no owner yet", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertEqual(
            [type(i) for i in sent.kwargs["view"].children], [TakeOnButton, NotNowButton]
        )

    async def test_taking_it_on(self) -> None:
        for result, words in [
            ("taken", "uses your plan now"),
            ("gone", "Someone already took"),
        ]:
            it = interaction(OTHER)
            it.client.campaigns.take_ownership = AsyncMock(return_value=result)
            await TakeOnButton(CAMPAIGN_ID).callback(it)
            self.assertIn(words, it.edit_original_response.await_args.kwargs["content"])
        it = interaction(OTHER)
        it.client.campaigns.take_ownership = AsyncMock(return_value="no_free_slot")
        await TakeOnButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], handover.TAKE_NO_ROOM)

    async def test_not_now_changes_nothing(self) -> None:
        it = interaction(OTHER)
        await NotNowButton(CAMPAIGN_ID).callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], handover.NOT_NOW)


if __name__ == "__main__":
    unittest.main()
