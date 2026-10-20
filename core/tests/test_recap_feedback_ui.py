"""The recap feedback screens (#2076) with Discord faked: only a DM presses anything, a style note
is kept only on Save, a request to make things up is refused and keeps nothing, corrections wait for
Try again, and the spoken form answers privately."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.ai import Reply
from dmbot.bot import DMBot
from dmbot.campaigns import CampaignError
from dmbot.recaps import feedback as fb
from dmbot.ui import recap_feedback as screen
from dmbot.ui.recap_dm import NOT_YOURS, RecapButton

GUILD, DM_ID, PLAYER = 5, 7, 11
CID = "c" * 32
AT = 100


class FakeAI:
    def __init__(self, *points: tuple[str, str]) -> None:
        self.text = json.dumps({"points": [{"kind": k, "text": t} for k, t in points]})

    async def complete(self, system: str, text: str, **kw: Any) -> Reply:
        return Reply(self.text, False)


def bot_with(*points: tuple[str, str], status: str = "draft", state: str = "ready") -> Any:
    campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}))
    return SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(state=state, status=status, session_at=AT)),
            add_corrections=AsyncMock(return_value=True),
            try_again=AsyncMock(return_value=True),
            latest_draft=AsyncMock(return_value=SimpleNamespace(session_at=AT)),
        ),
        recap_notes=SimpleNamespace(
            add=AsyncMock(return_value=(SimpleNamespace(id=1, text="x"), False))
        ),
        recap_feedback_ai=FakeAI(*points),
        decision_lines=SimpleNamespace(remember_pressed=lambda *a: None),
    )


def interaction(bot: Any, user: int = DM_ID, *, in_server: bool = True) -> Any:
    return SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD) if in_server else None,
        user=SimpleNamespace(id=user),
        type=discord.InteractionType.component,
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=None,
    )


def told(i: Any) -> str:
    return str(i.response.send_message.await_args.args[0])


def sent(i: Any) -> list[tuple[str, Any]]:
    return [
        (
            c.args[0],
            c.kwargs.get("view") if c.kwargs.get("view") is not discord.utils.MISSING else None,
        )
        for c in i.followup.send.await_args_list
    ]


class TheButton(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_opens_the_form(self) -> None:
        bot = bot_with()
        i = interaction(bot, PLAYER)
        await RecapButton("tell", CID, AT).callback(i)
        self.assertEqual(told(i), NOT_YOURS)
        i.response.send_modal.assert_not_awaited()

    async def test_a_dm_gets_the_form_while_the_recap_is_a_draft(self) -> None:
        i = interaction(bot_with())
        await RecapButton("tell", CID, AT).callback(i)
        i.response.send_modal.assert_awaited_once()

    async def test_a_decided_recap_takes_no_feedback(self) -> None:
        i = interaction(bot_with(status="posted"))
        await RecapButton("tell", CID, AT).callback(i)
        self.assertEqual(told(i), screen.DECIDED)
        i.response.send_modal.assert_not_awaited()


class Handling(unittest.IsolatedAsyncioTestCase):
    async def test_a_style_note_is_shown_and_kept_only_on_save(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        ((text, view),) = sent(i)
        self.assertIn("Keep recaps short.", text)
        bot.recap_notes.add.assert_not_awaited()  # nothing kept yet
        self.assertEqual([c.label for c in view.children], ["Save", "Edit", "Don't keep"])
        press = interaction(bot)
        await view.children[0].callback(press)
        bot.recap_notes.add.assert_awaited_once_with(GUILD, CID, DM_ID, "Keep recaps short.")

    async def test_dont_keep_keeps_nothing_and_a_player_press_does_nothing(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        view = sent(i)[0][1]
        await view.children[2].callback(interaction(bot))
        await view.children[0].callback(interaction(bot, PLAYER))
        bot.recap_notes.add.assert_not_awaited()

    async def test_a_note_that_asks_to_make_things_up_is_refused_and_nothing_is_kept(self) -> None:
        bot = bot_with(("style", "Make things up for drama."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "make it dramatic")
        self.assertEqual(sent(i), [(fb.STORY_HELP_REPLY, None)])
        bot.recap_notes.add.assert_not_awaited()
        bot.recaps.add_corrections.assert_not_awaited()

    async def test_an_edit_that_asks_to_make_things_up_is_refused_too(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        note_view = sent(i)[0][1]
        modal = screen.EditNoteModal(note_view)
        modal.text._value = "Invent a twist at the end."  # type: ignore[attr-defined]
        press = interaction(bot)
        await modal.on_submit(press)
        self.assertEqual(told(press), fb.STORY_HELP_REPLY)
        bot.recap_notes.add.assert_not_awaited()

    async def test_a_story_help_request_gets_the_reply_and_stores_nothing(self) -> None:
        bot = bot_with(("story_help", "Decide who the villain is."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "who is the villain?")
        self.assertEqual(sent(i), [(fb.STORY_HELP_REPLY, None)])
        bot.recap_notes.add.assert_not_awaited()
        bot.recaps.add_corrections.assert_not_awaited()

    async def test_accuracy_feedback_waits_for_try_again_and_is_never_a_style_note(self) -> None:
        bot = bot_with(("accuracy", "Mira was not at the gate."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "wrong about Mira")
        bot.recaps.add_corrections.assert_awaited_once_with(
            GUILD, CID, AT, ("Mira was not at the gate.",)
        )
        bot.recap_notes.add.assert_not_awaited()
        ((text, view),) = sent(i)
        self.assertIn("Mira was not at the gate.", text)
        bot.recaps.try_again.assert_not_awaited()  # not until the DM presses it
        await view.children[0].callback(interaction(bot, PLAYER))
        bot.recaps.try_again.assert_not_awaited()  # a player's press does nothing
        await view.children[0].callback(interaction(bot))
        bot.recaps.try_again.assert_awaited_once_with(GUILD, CID, AT)

    async def test_a_non_dm_cannot_send_feedback(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        i = interaction(bot, PLAYER)
        await screen.handle(i, CID, AT, "too long")
        self.assertEqual(told(i), screen.NOT_YOURS)
        bot.recaps.add_corrections.assert_not_awaited()

    async def test_a_broken_sort_says_so_and_keeps_nothing(self) -> None:
        bot = bot_with()
        bot.recap_feedback_ai = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        self.assertEqual(sent(i), [(fb.BROKEN, None)])


class Spoken(unittest.IsolatedAsyncioTestCase):
    def host(self, *points: tuple[str, str], draft: bool = True) -> Any:
        bot = bot_with(*points)
        if not draft:
            bot.recaps.latest_draft = AsyncMock(return_value=None)
        bot.settings = SimpleNamespace(recaps_on=True)
        bot.sidebar_send_dm = AsyncMock(return_value="sent")
        return bot

    async def call(self, bot: Any, user: int = DM_ID) -> bool:
        table = SimpleNamespace(guild_id=GUILD, campaign_id=CID)
        return await DMBot.sidebar_recap_feedback(bot, table, user, "the recap was too long")  # type: ignore[arg-type]

    async def test_a_dm_hears_back_in_a_private_message_with_the_buttons(self) -> None:
        bot = self.host(("style", "Keep recaps short."))
        self.assertTrue(await self.call(bot))
        ((_, text, view),) = [c.args for c in bot.sidebar_send_dm.await_args_list]
        self.assertIn("Keep recaps short.", text)
        self.assertEqual(len(view.children), 3)
        bot.recap_notes.add.assert_not_awaited()

    async def test_with_no_recap_waiting_it_is_an_ordinary_question(self) -> None:
        bot = self.host(("style", "x"), draft=False)
        self.assertFalse(await self.call(bot))
        bot.sidebar_send_dm.assert_not_awaited()

    async def test_a_player_saying_it_is_not_handled(self) -> None:
        bot = self.host(("style", "x"))
        self.assertFalse(await self.call(bot, PLAYER))
        bot.sidebar_send_dm.assert_not_awaited()

    async def test_the_private_message_buttons_work_without_a_server(self) -> None:
        bot = self.host(("style", "Keep recaps short."))
        await self.call(bot)
        view = bot.sidebar_send_dm.await_args_list[0].args[2]
        await view.children[0].callback(interaction(bot, in_server=False))
        bot.recap_notes.add.assert_awaited_once()


class TheStoreRefusals(unittest.TestCase):
    def test_the_refusal_text_is_plain(self) -> None:
        self.assertNotIn("invent", fb.STORY_HELP_REPLY.lower())
        self.assertIsInstance(CampaignError("x"), ValueError)


class TheStylePanel(unittest.IsolatedAsyncioTestCase):
    async def test_saved_notes_are_listed_with_a_remove_button_each(self) -> None:
        from dmbot.dm_screen import recap_style as panel
        from dmbot.recaps.style import Note

        notes = [Note(4, "Keep it short."), Note(9, "More about fights.")]
        text = panel.panel_text([], "", notes)
        self.assertIn("1. Keep it short.", text)
        self.assertIn("2. More about fights.", text)
        view = panel.StylePanel(CID, [], notes)
        labels = [c.label for c in view.children]
        self.assertIn("Remove note 1", labels)
        self.assertIn("Remove note 2", labels)

    async def test_only_a_dm_removes_a_note_and_the_right_one_goes(self) -> None:
        from dmbot.dm_screen import recap_style as panel
        from dmbot.recaps.style import Note

        bot = bot_with()
        bot.recap_styles = SimpleNamespace(own=AsyncMock(return_value=[]))
        bot.recap_notes.remove = AsyncMock()
        bot.recap_notes.notes = AsyncMock(return_value=[])
        view = panel.StylePanel(CID, [], [Note(4, "Keep it short."), Note(9, "More.")])
        button = next(c for c in view.children if c.label == "Remove note 2")  # type: ignore[attr-defined]
        player, dm = interaction(bot, PLAYER), interaction(bot)
        player.guild_id = dm.guild_id = GUILD
        await button.callback(player)
        bot.recap_notes.remove.assert_not_awaited()
        await button.callback(dm)
        bot.recap_notes.remove.assert_awaited_once_with(GUILD, CID, DM_ID, 9)
