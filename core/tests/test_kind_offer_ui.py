"""The Discord question "make these words a kind of connection?" (#3033): offered once after a
session, answered by a DM only, nothing said at the table in it. Stand-ins; invented words."""

from __future__ import annotations

import re
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_questions import KINDS, ORDER
from dmbot.memory import kind_offers
from dmbot.memory.link_words import back_words
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.ui import kind_offer as ko

BOT: Any = DMBot
DM, PLAYER, GUILD, CAMPAIGN = 7, 8, 5, "c" * 32
OFFER = kind_offers.Offer(
    "sworn on the ashes of", "sworn on the ashes of", 3, ("r1", "r2", "r3"),
    ("character",), ("faction",),
)  # fmt: skip


def bot(*, plan: bool = True, offer: Any = OFFER) -> Any:
    memory = SimpleNamespace(
        kind_offer=AsyncMock(return_value=offer),
        kind_offer_list=AsyncMock(return_value=[] if offer is None else [offer]),
        make_kind_of_words=AsyncMock(return_value=SimpleNamespace(batch=42)),
        answer_kind_offer=AsyncMock(),
        undo=AsyncMock(),
    )
    me = SimpleNamespace(
        memory=memory,
        campaigns=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    dm_user_ids=frozenset({DM}), dm_accounts=frozenset({DM})
                )
            )
        ),
        plan_allows=AsyncMock(return_value=plan),
        ask_dm=AsyncMock(),
    )
    me.offer_kind = partial(BOT.offer_kind, me)
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
    )


class TheQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_asked_once_with_the_words_and_the_count(self) -> None:
        me = bot()
        await me.offer_kind(table())
        me.ask_dm.assert_awaited_once()
        ask = me.ask_dm.await_args.args[1]
        self.assertEqual(ask.kind, KINDS)
        self.assertIn(KINDS, ORDER)
        self.assertIn("“sworn on the ashes of” (3 times)", ask.alone)
        self.assertIn("Make it a kind of connection of its own?", ask.alone)
        self.assertIsInstance(ask.button(), ko.OpenButton)

    async def test_nothing_to_offer_asks_nothing(self) -> None:
        me = bot(offer=None)
        await me.offer_kind(table())
        me.ask_dm.assert_not_awaited()

    async def test_a_plan_without_story_memory_is_not_asked(self) -> None:
        me = bot(plan=False)
        await me.offer_kind(table())
        me.ask_dm.assert_not_awaited()
        me.memory.kind_offer.assert_not_awaited()

    def test_button_ids_fit_and_match_their_templates(self) -> None:
        words = ko.key_of(OFFER.words_key)
        for item, template in (
            (ko.OpenButton(GUILD, CAMPAIGN), ko.OpenButton),
            (ko.AnswerButton(GUILD, CAMPAIGN, "yes", words), ko.AnswerButton),
            (ko.AnswerButton(GUILD, CAMPAIGN, "never", words), ko.AnswerButton),
            (ko.UndoButton(GUILD, CAMPAIGN, 123456789), ko.UndoButton),
        ):
            custom_id = str(item.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(re.fullmatch(template.__discord_ui_compiled_template__, custom_id))
            self.assertNotIn("ashes", custom_id)  # (the words are never in an id)


class TheAnswers(unittest.IsolatedAsyncioTestCase):
    def words(self) -> str:
        return ko.key_of(OFFER.words_key)

    async def test_yes_makes_the_kind_as_the_dm_and_offers_an_undo(self) -> None:
        me = bot()
        it = pressed(me, DM)
        await ko.answer(it, GUILD, CAMPAIGN, "yes", self.words())
        me.memory.make_kind_of_words.assert_awaited_once_with(
            GUILD, CAMPAIGN, OFFER.words_key, source=DM_SOURCE
        )
        sent = it.followup.send.await_args
        self.assertIn("3 connections now read", sent.args[0])
        self.assertEqual(len(sent.kwargs["view"].children), 1)  # (the Undo)

    async def test_not_now_and_never_are_remembered_with_the_count(self) -> None:
        for which, state in (("later", kind_offers.LATER), ("never", kind_offers.NEVER)):
            with self.subTest(which):
                me = bot()
                await ko.answer(pressed(me, DM), GUILD, CAMPAIGN, which, self.words())
                me.memory.answer_kind_offer.assert_awaited_once_with(
                    GUILD, CAMPAIGN, OFFER.words_key, state, seen=3
                )
                me.memory.make_kind_of_words.assert_not_awaited()

    async def test_a_player_cannot_answer(self) -> None:
        me = bot()
        it = pressed(me, PLAYER)
        await ko.answer(it, GUILD, CAMPAIGN, "yes", self.words())
        self.assertIn(ko.ONLY_DMS, it.followup.send.await_args.args[0])
        me.memory.make_kind_of_words.assert_not_awaited()
        me.memory.answer_kind_offer.assert_not_awaited()

    async def test_words_that_changed_since_the_question_answer_nothing(self) -> None:
        me = bot()
        it = pressed(me, DM)
        await ko.answer(it, GUILD, CAMPAIGN, "yes", "f" * 12)
        self.assertIn("out of date", it.followup.send.await_args.args[0])
        me.memory.make_kind_of_words.assert_not_awaited()

    async def test_undo_is_the_dms_and_takes_the_change_back(self) -> None:
        me = bot()
        await ko.undo(pressed(me, DM), GUILD, CAMPAIGN, 42)
        me.memory.undo.assert_awaited_once_with(GUILD, CAMPAIGN, 42, source=DM_SOURCE)
        me = bot()
        await ko.undo(pressed(me, PLAYER), GUILD, CAMPAIGN, 42)
        me.memory.undo.assert_not_awaited()


class TheBackWords(unittest.TestCase):
    def test_a_campaigns_own_kind_reads_back_as_linked_to_with_its_words(self) -> None:
        self.assertEqual(
            back_words("sworn_on_the_ashes_of", "", True),
            "is linked (“sworn on the ashes of”) to",
        )

    def test_a_core_kind_keeps_its_own_back_words_or_none(self) -> None:
        self.assertEqual(back_words("owns", ""), "is owned by")
        self.assertIsNone(back_words("appears_in", ""))
