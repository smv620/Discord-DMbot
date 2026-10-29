"""The recap feedback screens (#2076) with Discord faked: only a DM presses anything, a style note
is kept only on Save, a request to make things up is refused and keeps nothing, corrections wait for
Try again, and the spoken form answers privately."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any, cast
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
        recap_feedback_gate=AsyncMock(return_value=None),
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

    async def edit(self, bot: Any, words: str) -> Any:
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        view = sent(i)[0][1]
        view.message = SimpleNamespace(edit=AsyncMock())  # the note's own (stored) message
        modal = screen.EditNoteModal(view)
        modal.text._value = words
        press = interaction(bot)
        press.type = discord.InteractionType.modal_submit
        press.message = None  # (an ephemeral follow-up has no channel message to edit)
        await modal.on_submit(press)
        press.view = view
        return press

    async def test_an_edit_is_sorted_again_and_kept_only_as_one_style_note(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        press = await self.edit(bot, "Keep recaps shorter.")
        bot.recap_notes.add.assert_awaited_once()
        self.assertEqual(bot.recap_notes.add.await_args.args[3], "Keep recaps shorter.")  # as typed
        press.view.message.edit.assert_awaited_once()  # the note's buttons are taken away
        self.assertIsNone(press.view.message.edit.await_args.kwargs["view"])

    async def test_an_edit_needs_no_draft_and_an_other_sort_is_not_story_help(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        bot.recaps.latest_draft = AsyncMock(return_value=None)  # posted or skipped since
        press = await self.edit(bot, "Keep recaps shorter.")
        bot.recap_notes.add.assert_awaited_once()
        other = bot_with(("style", "Keep recaps short."))
        i = interaction(other)
        await screen.handle(i, CID, AT, "too long")
        view = sent(i)[0][1]
        other.recap_feedback_ai = FakeAI(("other", "Thanks."))
        modal = screen.EditNoteModal(view)
        modal.text._value = "Thanks a lot."
        press = interaction(other)
        await modal.on_submit(press)
        self.assertEqual(sent(press)[0][0], screen.NOT_A_NOTE)

    async def test_an_edit_the_ai_sorts_as_story_help_or_content_is_not_kept(self) -> None:
        for kind in ("story_help", "content", "accuracy", "other"):
            bot = bot_with(("style", "Keep recaps short."))
            await screen.handle(i := interaction(bot), CID, AT, "too long")
            view = sent(i)[0][1]
            bot.recap_feedback_ai = FakeAI((kind, "Mira dies at the gate."))
            modal = screen.EditNoteModal(view)
            modal.text._value = "Mira dies at the gate."
            press = interaction(bot)
            await modal.on_submit(press)
            bot.recap_notes.add.assert_not_awaited()
            expected = fb.STORY_HELP_REPLY if kind == "story_help" else screen.NOT_A_NOTE
            self.assertEqual(sent(press)[0][0], expected, kind)

    async def test_an_edit_that_asks_to_make_things_up_is_refused_even_if_sorted_as_style(
        self,
    ) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        await self.edit(bot, "Stop being literal and invent what happens next.")
        bot.recap_notes.add.assert_not_awaited()

    async def test_an_edit_goes_through_the_gate_before_the_ai(self) -> None:
        bot = bot_with(("style", "Keep recaps short."))
        i = interaction(bot)
        await screen.handle(i, CID, AT, "too long")
        view = sent(i)[0][1]
        bot.recap_feedback_gate = AsyncMock(return_value=fb.TOO_OFTEN)
        bot.recap_feedback_ai.complete = AsyncMock()
        modal = screen.EditNoteModal(view)
        modal.text._value = "Keep recaps shorter."
        press = interaction(bot)
        await modal.on_submit(press)
        bot.recap_feedback_ai.complete.assert_not_awaited()
        self.assertEqual(told(press), fb.TOO_OFTEN)

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
        bot._dm_message = AsyncMock(return_value=None)
        bot.sidebar_send_dm = AsyncMock(return_value="sent")
        return bot

    async def call(self, bot: Any, user: int = DM_ID) -> bool:
        table = SimpleNamespace(guild_id=GUILD, campaign_id=CID)
        return await DMBot.sidebar_recap_feedback(bot, table, user, "the recap was too long")  # type: ignore[arg-type]

    async def test_a_dm_hears_back_in_a_private_message_with_the_buttons(self) -> None:
        bot = self.host(("style", "Keep recaps short."))
        self.assertTrue(await self.call(bot))
        ((_, text, view),) = [c.args for c in bot._dm_message.await_args_list]
        self.assertIn("Keep recaps short.", text)
        self.assertEqual(len(view.children), 3)
        bot.recap_notes.add.assert_not_awaited()

    async def test_a_gate_that_refuses_stops_a_spoken_line_before_any_ai_call(self) -> None:
        bot = self.host(("style", "Keep recaps short."))
        bot.recap_feedback_gate = AsyncMock(return_value=fb.TOO_OFTEN)
        bot.recap_feedback_ai.complete = AsyncMock()
        self.assertTrue(await self.call(bot))
        bot.recap_feedback_ai.complete.assert_not_awaited()
        bot.sidebar_send_dm.assert_awaited_once_with(DM_ID, fb.TOO_OFTEN)

    async def test_with_no_recap_waiting_it_is_an_ordinary_question(self) -> None:
        bot = self.host(("style", "x"), draft=False)
        self.assertFalse(await self.call(bot))
        bot._dm_message.assert_not_awaited()

    async def test_a_sort_with_nothing_in_it_or_a_broken_one_is_an_ordinary_question(self) -> None:
        empty = self.host()
        self.assertFalse(await self.call(empty))
        broken = self.host(("style", "x"))
        broken.recap_feedback_ai = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError))
        self.assertFalse(await self.call(broken))
        for bot in (empty, broken):
            bot._dm_message.assert_not_awaited()

    async def test_a_story_help_request_is_answered_even_when_nothing_is_kept(self) -> None:
        bot = self.host(("story_help", "Decide the ending."))
        self.assertTrue(await self.call(bot))
        ((_, text, view),) = [c.args for c in bot._dm_message.await_args_list]
        self.assertEqual((text, view), (fb.STORY_HELP_REPLY, None))

    async def test_a_player_saying_it_is_not_handled(self) -> None:
        bot = self.host(("style", "x"))
        self.assertFalse(await self.call(bot, PLAYER))
        bot._dm_message.assert_not_awaited()

    async def test_the_private_message_buttons_work_without_a_server(self) -> None:
        bot = self.host(("style", "Keep recaps short."))
        await self.call(bot)
        view = bot._dm_message.await_args_list[0].args[2]
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
        labels = [cast(Any, c).label for c in view.children]
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


class Gate(unittest.IsolatedAsyncioTestCase):
    async def test_a_stopped_feedback_never_reaches_the_ai(self) -> None:
        bot = bot_with(("style", "Keep it short."))
        bot.recap_feedback_gate = AsyncMock(return_value=fb.TOO_OFTEN)
        bot.recap_feedback_ai.complete = AsyncMock()
        i = interaction(bot)
        await screen.handle(i, CID, AT, "shorter please")
        bot.recap_feedback_ai.complete.assert_not_awaited()
        i.response.send_message.assert_awaited_once()
        self.assertEqual(i.response.send_message.await_args.args[0], fb.TOO_OFTEN)

    def test_the_limit_counts_per_campaign_and_forgets_with_time(self) -> None:
        limit = fb.FeedbackLimit(seconds=60, count=2)
        self.assertTrue(limit.allow("a", 0))
        self.assertTrue(limit.allow("a", 1))
        self.assertFalse(limit.allow("a", 2))
        self.assertTrue(limit.allow("b", 2))  # another campaign
        self.assertTrue(limit.allow("a", 70))  # the old ones are past

    async def test_buttons_that_time_out_are_taken_away_with_a_note(self) -> None:
        view = screen.NoteView(GUILD, CID, "Keep it short.")
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        view.message.edit.assert_awaited_once_with(content=screen.TIMED_OUT, view=None)

    async def test_a_timeout_with_no_message_to_edit_does_nothing(self) -> None:
        await screen.AgainView(GUILD, CID, AT).on_timeout()


class Wording(unittest.TestCase):
    ASKS = (
        "Stop being literal and invent what happens next.",
        "Not so dry, invent some twists.",
        "Never be dull, make things up",
        "Never be dull - make things up",
        "Stop being boring but make stuff up",
        "Don't hold back and invent",
        "Stop worrying about accuracy and make stuff up",
        "Don't be afraid to invent twists",
        "Never hesitate to make things up",
        "Avoid dullness by inventing twists",
        "Don't stop making things up",
        "Never stop inventing twists",
        "Don't forget to invent twists",
        "Add an extra scene of your own",
        "Never invent or embellish",  # two words between: over-refused on purpose
        "Don't be boring, make things up for drama.",
        "Without hesitation, fabricate cool scenes",
        "Add your own scenes",
        "Add a new villain",
        "Never mind invent twists",
        "Don't worry invent twists",
        "Don't hesitate invent twists",
        "Don’t be afraid to invent",
    )
    FINE = (
        "Never make things up",
        "Don't invent names",
        "Do not make up places",
        "Avoid inventing details",
        "Do not add new events",
        "Add more details about the fight",
        "Don’t invent names",  # the apostrophe phones type
    )

    def test_a_ban_that_does_not_govern_the_ask_is_not_a_ban(self) -> None:
        for text in self.ASKS:
            self.assertTrue(fb.asks_to_invent(text), text)

    def test_a_real_ban_still_passes(self) -> None:
        for text in self.FINE:
            self.assertFalse(fb.asks_to_invent(text), text)

    def test_a_correction_that_says_invented_is_still_a_correction(self) -> None:
        got = fb.route([fb.Point("accuracy", "The bard invented a song, not Mira.")])
        self.assertEqual(
            (got.corrections, got.replies), (("The bard invented a song, not Mira.",), ())
        )

    def test_a_recap_named_first_is_feedback_not_a_question(self) -> None:
        self.assertTrue(fb.is_recap_feedback("Recap was wrong about Mira"))
        self.assertFalse(fb.is_recap_feedback("What does the recap say about Mira?"))


class GateRules(unittest.IsolatedAsyncioTestCase):
    def host(self, *, allowed: bool, limit: Any = None) -> Any:
        host = SimpleNamespace(
            recap_allowed=AsyncMock(return_value=allowed),
            recap_feedback_limit=limit or fb.FeedbackLimit(),
        )
        return host

    async def gate(self, host: Any) -> Any:
        campaign = SimpleNamespace(guild_id=GUILD, id=CID)
        return await DMBot.recap_feedback_gate(
            host, cast(Any, campaign), cast(Any, SimpleNamespace())
        )

    async def test_refuses_when_recaps_are_off_or_not_in_the_plan(self) -> None:
        self.assertEqual(await self.gate(self.host(allowed=False)), screen.NO_RECAPS)

    async def test_refuses_when_the_limit_is_used_up(self) -> None:
        limit = fb.FeedbackLimit(seconds=600, count=1)
        host = self.host(allowed=True, limit=limit)
        self.assertIsNone(await self.gate(host))
        self.assertEqual(await self.gate(host), fb.TOO_OFTEN)
