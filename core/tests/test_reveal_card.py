"""The DM's card for who learned what (#2996, part 2 slice 1, PR 4): offered once after a
session, answered by a DM only, nothing recorded until Keep, with what a Keep means for the table
(a player sees it; a guess becomes sure). Stand-ins; invented names."""

from __future__ import annotations

import re
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.dm_questions import ORDER, REVEALS
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.reveal_proposals import GONE, STATEMENT_GONE, Shown, Waiting
from dmbot.ui import reveal_card as dc

BOT: Any = DMBot
DM, PLAYER, GUILD, CAMPAIGN = 7, 8, 5, "c" * 32
PID = "d" * 32
DEED = Waiting(PID, "t" * 32, "connection", "r" * 32, "check", "Insight", None)
SHOWN = Shown("Tran", "Bruno knows Shela", "a successful Insight check", False, False)


def bot(*, on: bool = True, plan: bool = True, count: int = 2) -> Any:
    me = SimpleNamespace(
        settings=SimpleNamespace(story_reveals_on=on),
        memory=object(),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={DM, 9})),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_user_ids=frozenset({DM})))
        ),
        plan_allows=AsyncMock(return_value=plan),
        ask_dm=AsyncMock(),
    )
    me.offer_reveals = partial(BOT.offer_reveals, me)
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


def reveals_double(**kw: Any) -> Any:
    """A RevealProposals stand-in (patched into the module that builds it)."""
    double = SimpleNamespace(
        count_waiting=AsyncMock(return_value=kw.get("count", 2)),
        waiting=AsyncMock(return_value=kw.get("waiting", [DEED])),
        describe=AsyncMock(return_value=kw.get("shown", SHOWN)),
        record=AsyncMock(return_value=kw.get("batch", 42)),
        dismiss=AsyncMock(),
    )
    double.keep = double.record  # (the card calls it keep)
    return double


class TheQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_asked_once_with_the_count(self) -> None:
        me = bot()
        double = reveals_double()
        with patch("dmbot.bot.RevealProposals", return_value=double):
            await me.offer_reveals(table())
        me.ask_dm.assert_awaited_once()
        ask = me.ask_dm.await_args.args[1]
        self.assertEqual(ask.kind, REVEALS)
        self.assertIn(REVEALS, ORDER)
        self.assertIn("noticed 2 things the characters learned", ask.alone)
        self.assertIn("Nothing is saved until you do.", ask.alone)
        self.assertIsInstance(ask.button(), dc.OpenButton)

    async def test_nothing_waiting_asks_nothing(self) -> None:
        me = bot()
        with patch("dmbot.bot.RevealProposals", return_value=reveals_double(count=0)):
            await me.offer_reveals(table())
        me.ask_dm.assert_not_awaited()

    async def test_the_switch_and_the_plan_gate_it(self) -> None:
        for me in (bot(on=False), bot(plan=False)):
            with patch("dmbot.bot.RevealProposals", return_value=reveals_double()) as made:
                await me.offer_reveals(table())
            me.ask_dm.assert_not_awaited()
            made.assert_not_called()

    def test_button_ids_fit_and_match_their_templates(self) -> None:
        for item, template in (
            (dc.OpenButton(GUILD, CAMPAIGN), dc.OpenButton),
            (dc.KeepButton(GUILD, CAMPAIGN, PID), dc.KeepButton),
            (dc.NotButton(GUILD, CAMPAIGN, PID), dc.NotButton),
            (dc.UndoButton(GUILD, CAMPAIGN, 123456789), dc.UndoButton),
        ):
            custom_id = str(item.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(re.fullmatch(template.__discord_ui_compiled_template__, custom_id))

    def test_the_card_says_who_learned_what_and_how(self) -> None:
        text = dc.card(SHOWN, 3)
        self.assertIn("**Tran** learned: “Bruno knows Shela” (a successful Insight check)", text)
        self.assertIn("2 more after this one.", text)
        self.assertIn("Nothing is saved until you press Keep.", text)
        self.assertNotIn("player see it", text)  # (an NPC: no player)
        self.assertNotIn("guessing", text)

    def test_the_card_says_what_a_keep_means_for_the_table(self) -> None:
        pc = Shown("Tran", "Bruno knows Shela", "Clyde told them", True, True)
        text = dc.card(pc, 1)
        self.assertIn("Keeping this lets Tran's player see it.", text)
        self.assertIn("Tran was only guessing; keeping this makes it sure.", text)

    def test_the_words_cannot_format_or_ping(self) -> None:
        text = dc.card(Shown("*Tran*", "@everyone **loud**", "<@1> told them", False, False), 1)
        self.assertIn("\\*\\*loud\\*\\*", text)  # (escaped, so it does not format)
        self.assertIn("\\*Tran\\*", text)


class TheAnswers(unittest.IsolatedAsyncioTestCase):
    async def answer(self, me: Any, user: int, double: Any, *, keep: bool) -> Any:
        it = pressed(me, user)
        with patch("dmbot.ui.reveal_card.RevealProposals", return_value=double):
            await dc.answer(it, GUILD, CAMPAIGN, PID, keep=keep)
        return it

    async def test_keep_is_the_dms_and_offers_an_undo_and_the_next_card(self) -> None:
        me, double = bot(), reveals_double(waiting=[DEED])
        it = await self.answer(me, DM, double, keep=True)
        double.record.assert_awaited_once_with(
            GUILD, CAMPAIGN, PID, opted_in={DM, 9}, source=DM_SOURCE
        )
        shown = it.edit_original_response.await_args.kwargs
        self.assertIn("✅ Kept.", shown["content"])
        self.assertIn("Bruno knows Shela", shown["content"])  # (the next one in its place)
        undo = it.followup.send.await_args
        self.assertEqual(len(undo.kwargs["view"].children), 1)

    async def test_a_keep_that_changed_nothing_sends_no_undo_and_says_so(self) -> None:
        me, double = bot(), reveals_double(waiting=[], batch=None)
        it = await self.answer(me, DM, double, keep=True)
        it.followup.send.assert_not_awaited()
        self.assertIn(
            "They already knew it.", it.edit_original_response.await_args.kwargs["content"]
        )

    async def test_a_statement_gone_since_says_so_in_plain_words(self) -> None:
        me, double = bot(), reveals_double()
        double.keep = AsyncMock(side_effect=MemoryRuleError(STATEMENT_GONE))
        it = await self.answer(me, DM, double, keep=True)
        self.assertIn("nothing to keep", it.followup.send.await_args.args[0])

    async def test_one_that_can_no_longer_be_shown_is_turned_down_and_skipped(self) -> None:
        later = Waiting("e" * 32, "t" * 32, "entry", "s" * 32, "tell", "", "c" * 32)
        me, double = bot(), reveals_double(waiting=[DEED, later])
        double.describe = AsyncMock(side_effect=[None, SHOWN])
        it = pressed(me, DM)
        with patch("dmbot.ui.reveal_card.RevealProposals", return_value=double):
            await dc.open_first(it, GUILD, CAMPAIGN)
        double.dismiss.assert_awaited_once_with(GUILD, CAMPAIGN, PID, source=DM_SOURCE)
        self.assertIn("Bruno knows Shela", it.followup.send.await_args.args[0])

    async def test_the_undo_is_sent_even_if_the_next_card_cannot_be_loaded(self) -> None:
        me, double = bot(), reveals_double()
        double.waiting = AsyncMock(side_effect=RuntimeError("down"))
        it = await self.answer(me, DM, double, keep=True)
        it.followup.send.assert_awaited_once()  # (the kept one can still be taken back)
        self.assertIn(dc.FAILED, it.edit_original_response.await_args.kwargs["content"])

    async def test_an_already_answered_card_moves_on_to_the_next_one(self) -> None:
        me, double = bot(), reveals_double()
        double.record = AsyncMock(side_effect=MemoryRuleError(GONE))
        double.keep = double.record
        it = await self.answer(me, DM, double, keep=True)
        shown = it.edit_original_response.await_args.kwargs["content"]
        self.assertIn("already answered", shown)
        self.assertIn("Bruno knows Shela", shown)  # (the next waiting one, with buttons)

    async def test_the_consenting_set_is_what_the_count_is_made_with(self) -> None:
        me, double = bot(), reveals_double()
        with patch("dmbot.bot.RevealProposals", return_value=double):
            await me.offer_reveals(table())
        self.assertEqual(double.count_waiting.await_args.kwargs["opted_in"], {DM, 9})

    async def test_when_none_are_left_it_says_so(self) -> None:
        me, double = bot(), reveals_double(waiting=[])
        it = await self.answer(me, DM, double, keep=True)
        self.assertIn(dc.NONE_LEFT, it.edit_original_response.await_args.kwargs["content"])

    async def test_not_this_turns_it_down_and_keeps_nothing(self) -> None:
        me, double = bot(), reveals_double(waiting=[])
        await self.answer(me, DM, double, keep=False)
        double.dismiss.assert_awaited_once_with(GUILD, CAMPAIGN, PID, source=DM_SOURCE)
        double.record.assert_not_awaited()

    async def test_a_player_cannot_keep_or_dismiss(self) -> None:
        for record in (True, False):
            me, double = bot(), reveals_double()
            it = await self.answer(me, PLAYER, double, keep=record)
            self.assertIn(dc.ONLY_DMS, it.followup.send.await_args.args[0])
            double.record.assert_not_awaited()
            double.dismiss.assert_not_awaited()

    async def test_a_refusal_says_why_and_a_failure_never_hangs(self) -> None:
        me = bot()
        refused = reveals_double()
        refused.record = AsyncMock(
            side_effect=MemoryRuleError("Only a character can learn something this way.")
        )
        refused.keep = refused.record
        it = await self.answer(me, DM, refused, keep=True)
        self.assertIn("Only a character", it.followup.send.await_args.args[0])
        broken = reveals_double()
        broken.record = AsyncMock(side_effect=RuntimeError("down"))
        broken.keep = broken.record
        it = await self.answer(me, DM, broken, keep=True)
        self.assertEqual(it.followup.send.await_args.args[0], dc.FAILED)

    async def test_open_shows_the_first_one_to_a_dm_only(self) -> None:
        me = bot()
        with patch("dmbot.ui.reveal_card.RevealProposals", return_value=reveals_double()):
            it = pressed(me, DM)
            await dc.open_first(it, GUILD, CAMPAIGN)
            self.assertIn("Bruno knows Shela", it.followup.send.await_args.args[0])
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
