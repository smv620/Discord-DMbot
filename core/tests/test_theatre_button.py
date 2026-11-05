"""The DM's "Make a script" question and button (#3244, 5c): offered only when the switch and the
plan allow it, usable by a DM only. The question itself says what it costs, so one press makes the
script (no second "are you sure?" step), and the finished card links to the session's page.
Invented ids."""

from __future__ import annotations

import re
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_questions import SCRIPT
from dmbot.transcript import theatre_job as job
from dmbot.ui import theatre_button as tb

BOT: Any = DMBot  # (its methods are called on a stand-in)
DM, PLAYER, GUILD, SESSION = 7, 8, 5, "a" * 32


def bot(*, on: bool = True, plan: bool = True, lines: int = 300) -> Any:
    me = SimpleNamespace(
        settings=SimpleNamespace(theatre_on=on, rel_read_on=False, site_url="https://x.test"),
        theatre_ai=object() if on else None,
        memory=object(),
        transcripts=SimpleNamespace(
            session=AsyncMock(
                return_value=SimpleNamespace(lines=lines, campaign_id="c" * 32, number=2)
            )
        ),
        campaigns=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(dm_user_ids=frozenset({DM}), dm_screen_channel_id=9)
            )
        ),
        plan_allows=AsyncMock(return_value=plan),
        ask_dm=AsyncMock(),
    )
    me.offer_script = partial(BOT.offer_script, me)
    return me


def table() -> Any:
    return SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, transcript_session_id=SESSION)


def pressed(me: Any, user: int) -> Any:
    answered = {"yes": False}

    async def defer(**_: Any) -> None:
        answered["yes"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        client=me,
        response=SimpleNamespace(
            is_done=lambda: answered["yes"],
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=defer),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TheOffer(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_offered_with_the_switch_and_the_plan(self) -> None:
        me = bot()
        await me.offer_script(table())
        me.ask_dm.assert_awaited_once()
        ask = me.ask_dm.await_args.args[1]
        self.assertEqual(ask.kind, SCRIPT)
        self.assertIsInstance(ask.button(), tb.ScriptButton)
        # the cost and who can read it are in the question, so the button is the one "yes"
        self.assertIn(job.estimate_line(300), ask.alone)
        self.assertIn(job.PRIVATE_NOTE, ask.alone)
        self.assertIn("Nothing is used until you press the button.", ask.alone)

    async def test_it_is_hidden_when_the_switch_is_off(self) -> None:
        me = bot(on=False)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()

    async def test_it_is_hidden_when_the_plan_does_not_allow_it_or_cannot_be_checked(self) -> None:
        me = bot(plan=False)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()
        self.assertEqual(me.plan_allows.await_args.kwargs, {"fail_open": False})

    async def test_a_session_with_no_lines_gets_no_offer(self) -> None:
        me = bot(lines=0)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()

    def test_the_button_knows_its_server_and_session_after_a_restart(self) -> None:
        button = tb.ScriptButton(GUILD, SESSION)
        custom_id = button.item.custom_id
        assert custom_id is not None
        match = re.fullmatch(r"dmbot:script2:([0-9]{1,20}):([0-9a-f]{32})", custom_id)
        assert match is not None
        self.assertEqual((int(match[1]), match[2]), (GUILD, SESSION))


class ThePress(unittest.IsolatedAsyncioTestCase):
    def said(self, interaction: Any) -> str:
        return str(interaction.followup.send.await_args.args[0])

    def bot(self, **kw: Any) -> Any:
        me = bot(**kw)
        me.campaigns.db = object()
        return me

    async def test_one_press_by_a_dm_answers_first_then_starts_the_script(self) -> None:
        from unittest.mock import patch

        me = self.bot(lines=300)
        interaction = pressed(me, DM)
        order: list[str] = []
        interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))
        interaction.edit_original_response = AsyncMock(side_effect=lambda **_: order.append("card"))

        async def run(*_: Any) -> None:
            order.append("run")

        with (
            patch.object(tb, "TheatreRuns") as runs,
            patch.object(tb, "run", AsyncMock(side_effect=run)) as made,
        ):
            runs.return_value.state = AsyncMock(return_value=None)
            await tb.press(interaction, GUILD, SESSION)
        self.assertEqual(order, ["defer", "card", "run"])
        made.assert_awaited_once_with(interaction, GUILD, SESSION)
        self.assertFalse(hasattr(tb, "Confirm"))  # no second "are you sure?" step

    async def test_a_press_after_the_script_is_made_spends_nothing_and_says_so(self) -> None:
        from unittest.mock import patch

        me = self.bot()
        interaction = pressed(me, DM)
        with (
            patch.object(tb, "TheatreRuns") as runs,
            patch.object(tb, "run", AsyncMock()) as made,
        ):
            runs.return_value.state = AsyncMock(return_value="done")
            await tb.press(interaction, GUILD, SESSION)
        made.assert_not_awaited()
        self.assertIn(tb.ALREADY_MADE, self.said(interaction))
        self.assertIn(f"/account/transcripts/{GUILD}.{'c' * 32}/2", self.said(interaction))

    async def test_a_failed_earlier_run_may_be_tried_again(self) -> None:
        from unittest.mock import patch

        interaction = pressed(self.bot(), DM)
        with (
            patch.object(tb, "TheatreRuns") as runs,
            patch.object(tb, "run", AsyncMock()) as made,
        ):
            runs.return_value.state = AsyncMock(return_value="failed")
            await tb.press(interaction, GUILD, SESSION)
        made.assert_awaited_once()

    async def test_a_player_is_refused_even_when_the_screen_shows_the_button(self) -> None:
        from unittest.mock import patch

        me = self.bot()
        interaction = pressed(me, PLAYER)
        with patch.object(tb, "run", AsyncMock()) as made:
            await tb.press(interaction, GUILD, SESSION)
        made.assert_not_awaited()
        self.assertEqual(self.said(interaction), tb.ONLY_DMS)

    async def test_a_stale_card_with_the_switch_off_or_no_plan_says_why(self) -> None:
        for me, words in ((self.bot(on=False), tb.OFF), (self.bot(plan=False), tb.NO_PLAN)):
            interaction = pressed(me, DM)
            await tb.press(interaction, GUILD, SESSION)
            self.assertEqual(self.said(interaction), words)

    async def test_a_session_that_is_gone_says_so(self) -> None:
        me = self.bot()
        me.transcripts.session = AsyncMock(return_value=None)
        interaction = pressed(me, DM)
        await tb.press(interaction, GUILD, SESSION)
        self.assertEqual(self.said(interaction), tb.GONE)

    async def test_a_question_from_before_this_change_starts_nothing_and_offers_the_new_one(
        self,
    ) -> None:
        from unittest.mock import patch

        interaction = pressed(self.bot(lines=300), DM)
        old = tb.OldScriptButton(GUILD, SESSION)
        with (
            patch.object(tb, "TheatreRuns") as runs,
            patch.object(tb, "run", AsyncMock()) as made,
        ):
            runs.return_value.state = AsyncMock(return_value=None)
            await old.callback(interaction)
        made.assert_not_awaited()  # nothing started, nothing spent
        sent = interaction.followup.send.await_args
        self.assertIn(tb.OLD_QUESTION, sent.args[0])
        self.assertIn(job.estimate_line(300), sent.args[0])  # the new question, with its cost
        (button,) = sent.kwargs["view"].children
        self.assertEqual(button.item.custom_id, f"dmbot:script2:{GUILD}:{SESSION}")
        self.assertEqual(old.item.custom_id, f"dmbot:script:{GUILD}:{SESSION}")

    async def test_the_old_button_makes_the_same_checks_and_never_re_offers_a_made_script(
        self,
    ) -> None:
        from unittest.mock import patch

        for me, user, words in (
            (self.bot(), PLAYER, tb.ONLY_DMS),
            (self.bot(on=False), DM, tb.OFF),
            (self.bot(plan=False), DM, tb.NO_PLAN),
        ):
            interaction = pressed(me, user)
            await tb.OldScriptButton(GUILD, SESSION).callback(interaction)
            self.assertEqual(self.said(interaction), words)
        interaction = pressed(self.bot(), DM)
        with patch.object(tb, "TheatreRuns") as runs:
            runs.return_value.state = AsyncMock(return_value="done")
            await tb.OldScriptButton(GUILD, SESSION).callback(interaction)
        self.assertIn(tb.ALREADY_MADE, self.said(interaction))


class TheRun(unittest.IsolatedAsyncioTestCase):
    def bot(self, **kw: Any) -> Any:
        me = bot(**kw)
        me.transcripts.lines = AsyncMock(
            return_value=[SimpleNamespace(user_id=PLAYER), SimpleNamespace(user_id=DM)]
        )
        me.consent = SimpleNamespace(consenting=AsyncMock(return_value={DM}))  # (PLAYER stopped)
        me.memory = SimpleNamespace(table_names=AsyncMock(return_value=["Kell"]))
        me.name_of = lambda gid, uid: {DM: "Dana", PLAYER: "Pat"}.get(uid, f"<@{uid}>")
        me.theatre_ai = SimpleNamespace(complete=AsyncMock())
        me.campaigns.db = object()
        me.sidebar_send_dm = AsyncMock(return_value="sent")
        return me

    def card(self, me: Any, user: int = DM, *, expired: bool = False) -> Any:
        import discord

        exc: Any = discord.HTTPException  # (built from a stand-in response)
        gone = exc(SimpleNamespace(status=404, reason="x"), "gone")
        c = pressed(me, user)
        c.edit_original_response = AsyncMock(side_effect=gone if expired else None)
        return c

    async def go(self, c: Any, outcome: job.Outcome) -> tuple[job.Outcome, Any]:
        from unittest.mock import patch

        async def fake(*a: Any, **kw: Any) -> job.Outcome:
            if outcome.text and outcome.started:
                await kw["edit"](outcome.text)  # (the job ends the card with the outcome)
            return outcome

        with patch.object(job, "make_script", AsyncMock(side_effect=fake)) as made:
            got = await tb.run(c, GUILD, SESSION)
        return got, made

    async def test_the_ai_is_given_only_agreed_names_and_the_tables_list(self) -> None:
        me = self.bot()
        _, made = await self.go(self.card(me), job.Outcome("Script ready", started=True, ok=True))
        kw = made.await_args.kwargs
        self.assertEqual(kw["names"], {DM: "Dana"})  # (Pat stopped: no name goes)
        self.assertEqual(kw["known"], ["Kell"])
        self.assertEqual(kw["agreed"], {DM})
        self.assertEqual(kw["link"], f"https://x.test/account/transcripts/{GUILD}.{'c' * 32}/2")

    async def test_the_switch_and_the_plan_are_checked_again_when_it_is_confirmed(self) -> None:
        for me, words in ((self.bot(on=False), tb.OFF), (self.bot(plan=False), tb.NO_PLAN)):
            c = self.card(me)
            got, made = await self.go(c, job.Outcome("x", started=True))
            made.assert_not_awaited()
            self.assertEqual(got.text, words)
            self.assertEqual(c.edit_original_response.await_args.kwargs["content"], words)

    async def test_a_non_dm_cannot_make_one_from_a_confirm_step(self) -> None:
        me = self.bot()
        got, made = await self.go(self.card(me, PLAYER), job.Outcome("x", started=True))
        made.assert_not_awaited()
        self.assertEqual(got.text, tb.ONLY_DMS)

    async def test_a_second_card_for_a_run_already_going_is_told_so_and_not_left_hanging(
        self,
    ) -> None:
        me = self.bot()
        c = self.card(me)
        await self.go(c, job.Outcome(job.ALREADY, started=False))
        self.assertEqual(c.edit_original_response.await_args.kwargs["content"], job.ALREADY)

    async def test_when_the_card_has_expired_the_last_word_reaches_the_dm_privately(self) -> None:
        me = self.bot()
        text = "No lines were kept. Nothing was changed."
        await self.go(self.card(me, expired=True), job.Outcome(text, started=True))
        me.sidebar_send_dm.assert_awaited_once_with(DM, text)

    async def test_a_working_card_sends_no_extra_message_and_a_taken_over_run_says_nothing(
        self,
    ) -> None:
        me = self.bot()
        await self.go(self.card(me), job.Outcome("Script ready", started=True, ok=True))
        me.sidebar_send_dm.assert_not_awaited()
        await self.go(self.card(me, expired=True), job.Outcome("", started=True, taken_over=True))
        me.sidebar_send_dm.assert_not_awaited()


class TheLink(unittest.TestCase):
    def test_the_sessions_page_on_the_website(self) -> None:
        self.assertEqual(
            tb.script_link("https://getdmbot.com/", GUILD, "c" * 32, 3),
            f"https://getdmbot.com/account/transcripts/{GUILD}.{'c' * 32}/3",
        )
        self.assertEqual(tb.script_link("", GUILD, "c" * 32, 3), "")  # no site address
        self.assertEqual(tb.script_link("https://x.test", GUILD, "c" * 32, 0), "")  # no number

    def test_a_finished_script_card_links_to_it_and_a_failed_one_does_not(self) -> None:
        from dmbot.transcript import theatre_run as tr

        report = tr.Report(parts=2, lines=10, kept=9, saved=True)
        done = job.done_text(report, "https://x.test/p")
        self.assertIn("Script ready: 9 lines from 2 scenes. Only the DMs can read it.", done)
        self.assertIn("https://x.test/p", done)
        self.assertIn("It is saved with the session.", job.done_text(report))  # no site address
        self.assertNotIn("http", job.stopped_text(1, 2))
