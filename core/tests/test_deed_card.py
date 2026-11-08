"""The DM's card for deeds the session reader noticed (#3175 slice 4, PR 3): offered once after a
session, answered by a DM only, nothing recorded until Record. Stand-ins; invented names."""

from __future__ import annotations

import re
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.dm_questions import DEEDS, ORDER
from dmbot.memory.deed_proposals import GONE, Waiting
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.ui import deed_card as dc

BOT: Any = DMBot
DM, PLAYER, GUILD, CAMPAIGN = 7, 8, 5, "c" * 32
PID = "d" * 32
DEED = Waiting(PID, "the party", "Saved the mill", ("Brindle", "Oldmill"), "Helped them", 500)


def bot(*, on: bool = True, plan: bool = True, count: int = 2) -> Any:
    me = SimpleNamespace(
        settings=SimpleNamespace(story_deeds_on=on),
        memory=object(),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={DM, 9})),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_user_ids=frozenset({DM})))
        ),
        plan_allows=AsyncMock(return_value=plan),
        ask_dm=AsyncMock(),
    )
    me.offer_deeds = partial(BOT.offer_deeds, me)
    me.count = count
    return me


def table() -> Any:
    return SimpleNamespace(guild_id=GUILD, campaign_id=CAMPAIGN)


def pressed(me: Any, user: int) -> Any:
    done = {"yes": False}

    async def defer(**_: Any) -> None:
        done["yes"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        client=me,
        response=SimpleNamespace(
            is_done=lambda: done["yes"],
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=defer),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def deeds_double(**kw: Any) -> Any:
    """A DeedProposals stand-in (patched into the module that builds it)."""
    double = SimpleNamespace(
        count_waiting=AsyncMock(return_value=kw.get("count", 2)),
        waiting=AsyncMock(return_value=kw.get("waiting", [DEED])),
        record=AsyncMock(return_value=kw.get("batch", 42)),
        dismiss=AsyncMock(),
    )
    return double


class TheQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_asked_once_with_the_count(self) -> None:
        me = bot()
        double = deeds_double()
        with patch("dmbot.bot.DeedProposals", return_value=double):
            await me.offer_deeds(table())
        me.ask_dm.assert_awaited_once()
        ask = me.ask_dm.await_args.args[1]
        self.assertEqual(ask.kind, DEEDS)
        self.assertIn(DEEDS, ORDER)
        self.assertIn("noticed 2 deeds the party did", ask.alone)
        self.assertIn("Nothing is saved until you do.", ask.alone)
        self.assertIsInstance(ask.button(), dc.OpenButton)

    async def test_nothing_waiting_asks_nothing(self) -> None:
        me = bot()
        with patch("dmbot.bot.DeedProposals", return_value=deeds_double(count=0)):
            await me.offer_deeds(table())
        me.ask_dm.assert_not_awaited()

    async def test_the_switch_and_the_plan_gate_it(self) -> None:
        for me in (bot(on=False), bot(plan=False)):
            with patch("dmbot.bot.DeedProposals", return_value=deeds_double()) as made:
                await me.offer_deeds(table())
            me.ask_dm.assert_not_awaited()
            made.assert_not_called()

    def test_button_ids_fit_and_match_their_templates(self) -> None:
        for item, template in (
            (dc.OpenButton(GUILD, CAMPAIGN), dc.OpenButton),
            (dc.RecordButton(GUILD, CAMPAIGN, PID), dc.RecordButton),
            (dc.NotButton(GUILD, CAMPAIGN, PID), dc.NotButton),
            (dc.UndoButton(GUILD, CAMPAIGN, 123456789), dc.UndoButton),
        ):
            custom_id = str(item.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(re.fullmatch(template.__discord_ui_compiled_template__, custom_id))

    def test_the_card_says_who_what_whom_and_how_it_landed(self) -> None:
        text = dc.card(DEED, 3)
        self.assertIn("**the party**: “Saved the mill”", text)
        self.assertIn("**Brindle**, **Oldmill** (it helped them)", text)
        self.assertIn("2 more after this one.", text)
        self.assertIn("Nothing is saved until you press Record.", text)

    def test_the_card_says_what_kind_of_deed_record_will_keep(self) -> None:
        rescue = Waiting(
            PID, "the party", "Saved Brindle", ("Brindle",), "Helped them", 1, "rescue"
        )
        self.assertIn("“Saved Brindle” (a rescue)", dc.card(rescue, 1))
        self.assertNotIn("(a ", dc.card(DEED, 1))  # (no kind: nothing added)

    def test_the_tellings_markdown_cannot_format_or_ping(self) -> None:
        text = dc.card(Waiting(PID, "*Cerric*", "@everyone **loud**", ("<@1>",), "Hurt them", 1), 1)
        self.assertIn("\\*\\*loud\\*\\*", text)  # (escaped, so it does not format)
        self.assertIn("\\*Cerric\\*", text)


class TheAnswers(unittest.IsolatedAsyncioTestCase):
    async def answer(self, me: Any, user: int, double: Any, *, record: bool) -> Any:
        it = pressed(me, user)
        with patch("dmbot.ui.deed_card.DeedProposals", return_value=double):
            await dc.answer(it, GUILD, CAMPAIGN, PID, record=record)
        return it

    async def test_record_is_the_dms_and_offers_an_undo_and_the_next_card(self) -> None:
        me, double = bot(), deeds_double(waiting=[DEED])
        it = await self.answer(me, DM, double, record=True)
        double.record.assert_awaited_once_with(
            GUILD, CAMPAIGN, PID, opted_in={DM, 9}, source=DM_SOURCE
        )
        shown = it.edit_original_response.await_args.kwargs
        self.assertIn("✅ Recorded.", shown["content"])
        self.assertIn("Saved the mill", shown["content"])  # (the next one in its place)
        undo = it.followup.send.await_args
        self.assertEqual(len(undo.kwargs["view"].children), 1)

    async def test_a_record_with_no_undo_batch_sends_no_undo(self) -> None:
        me, double = bot(), deeds_double(waiting=[], batch=None)
        it = await self.answer(me, DM, double, record=True)
        it.followup.send.assert_not_awaited()

    async def test_the_undo_is_sent_even_if_the_next_card_cannot_be_loaded(self) -> None:
        me, double = bot(), deeds_double()
        double.waiting = AsyncMock(side_effect=RuntimeError("down"))
        it = await self.answer(me, DM, double, record=True)
        it.followup.send.assert_awaited_once()  # (the recorded deed can still be taken back)
        self.assertIn(dc.FAILED, it.edit_original_response.await_args.kwargs["content"])

    async def test_an_already_answered_card_moves_on_to_the_next_one(self) -> None:
        me, double = bot(), deeds_double()
        double.record = AsyncMock(side_effect=MemoryRuleError(GONE))
        it = await self.answer(me, DM, double, record=True)
        shown = it.edit_original_response.await_args.kwargs["content"]
        self.assertIn("already answered", shown)
        self.assertIn("Saved the mill", shown)  # (the next waiting deed, with buttons)

    async def test_the_consenting_set_is_what_the_count_is_made_with(self) -> None:
        me, double = bot(), deeds_double()
        with patch("dmbot.bot.DeedProposals", return_value=double):
            await me.offer_deeds(table())
        self.assertEqual(double.count_waiting.await_args.kwargs["opted_in"], {DM, 9})

    async def test_when_none_are_left_it_says_so(self) -> None:
        me, double = bot(), deeds_double(waiting=[])
        it = await self.answer(me, DM, double, record=True)
        self.assertIn(dc.NONE_LEFT, it.edit_original_response.await_args.kwargs["content"])

    async def test_not_this_turns_it_down_and_records_nothing(self) -> None:
        me, double = bot(), deeds_double(waiting=[])
        await self.answer(me, DM, double, record=False)
        double.dismiss.assert_awaited_once_with(GUILD, CAMPAIGN, PID, source=DM_SOURCE)
        double.record.assert_not_awaited()

    async def test_a_player_cannot_record_or_dismiss(self) -> None:
        for record in (True, False):
            me, double = bot(), deeds_double()
            it = await self.answer(me, PLAYER, double, record=record)
            self.assertIn(dc.ONLY_DMS, it.followup.send.await_args.args[0])
            double.record.assert_not_awaited()
            double.dismiss.assert_not_awaited()

    async def test_a_refusal_says_why_and_a_failure_never_hangs(self) -> None:
        me = bot()
        refused = deeds_double()
        refused.record = AsyncMock(
            side_effect=MemoryRuleError("DMbot doesn't know who “X” is yet.")
        )
        it = await self.answer(me, DM, refused, record=True)
        self.assertIn("doesn't know who", it.followup.send.await_args.args[0])
        broken = deeds_double()
        broken.record = AsyncMock(side_effect=RuntimeError("down"))
        it = await self.answer(me, DM, broken, record=True)
        self.assertEqual(it.followup.send.await_args.args[0], dc.FAILED)

    async def test_open_shows_the_first_deed_to_a_dm_only(self) -> None:
        me = bot()
        with patch("dmbot.ui.deed_card.DeedProposals", return_value=deeds_double()):
            it = pressed(me, DM)
            await dc.open_first(it, GUILD, CAMPAIGN)
            self.assertIn("Saved the mill", it.followup.send.await_args.args[0])
            other = pressed(me, PLAYER)
            await dc.open_first(other, GUILD, CAMPAIGN)
            self.assertIn(dc.ONLY_DMS, other.followup.send.await_args.args[0])

    async def test_undo_is_the_dms_and_takes_the_change_back(self) -> None:
        me = bot()
        me.memory = SimpleNamespace(undo=AsyncMock())
        await dc.undo(pressed(me, DM), GUILD, CAMPAIGN, 42)
        me.memory.undo.assert_awaited_once_with(GUILD, CAMPAIGN, 42, source=DM_SOURCE)
        me.memory.undo.reset_mock()
        await dc.undo(pressed(me, PLAYER), GUILD, CAMPAIGN, 42)
        me.memory.undo.assert_not_awaited()
