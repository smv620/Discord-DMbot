"""The 📜 Transcripts panel on ⚙️ Settings (#1533): DM only on every press, one warning before a
shorter limit is saved, nothing saved before its button, and a longer choice saves at once."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignError
from dmbot.dm_screen import transcript_keep as keep
from tests.answer_first import Timeline

CID = "ab" * 16


def campaign(days: int | None = None, dms: tuple[int, ...] = (7,)) -> Any:
    return SimpleNamespace(
        id=CID, guild_id=1, name="Frost", dm_user_ids=set(dms), transcript_keep_days=days
    )


def press(user: int, current: Any, saved: Any = None) -> Any:
    store = SimpleNamespace(
        get=AsyncMock(return_value=current),
        set_transcript_keep=AsyncMock(return_value=saved or current),
    )
    it = SimpleNamespace(
        client=SimpleNamespace(campaigns=store),
        guild=SimpleNamespace(id=1),
        guild_id=1,
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(
            edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()
        ),
        edit_original_response=AsyncMock(),
    )
    return it


class Panel(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_gets_nothing(self) -> None:
        it = press(99, campaign())
        await keep.TranscriptsButton(CID).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], keep.ONLY_DMS)
        it2 = press(99, campaign())
        await keep.KeepChoiceButton(CID, "30").callback(it2)
        await keep.KeepYesButton(CID, "30").callback(it2)
        await keep.KeepNoButton(CID).callback(it2)
        it2.client.campaigns.set_transcript_keep.assert_not_awaited()

    async def test_the_panel_shows_the_choices_and_says_what_stays(self) -> None:
        it = press(7, campaign(90))
        await keep.TranscriptsButton(CID).callback(it)
        text = it.response.send_message.await_args.args[0]
        self.assertIn("**90 days**", text)
        self.assertIn("Recaps and the facts you confirmed stay", text)
        view = it.response.send_message.await_args.kwargs["view"]
        self.assertEqual(
            [i.item.label for i in view.children], ["Forever", "1 year", "90 days", "30 days"]
        )

    async def test_forever_says_it_keeps_them_until_the_campaign_goes(self) -> None:
        self.assertIn("until the campaign is deleted", keep.panel_text(campaign(None)))

    async def test_a_shorter_choice_warns_and_saves_nothing_yet(self) -> None:
        it = press(7, campaign(None))
        await keep.KeepChoiceButton(CID, "90").callback(it)
        it.client.campaigns.set_transcript_keep.assert_not_awaited()
        edit = it.response.edit_message.await_args.kwargs
        self.assertEqual(
            edit["content"],
            "Sessions older than 90 days will be deleted every night, with their downloads on "
            "the website. The first clean-up is tonight. Recaps and the facts you confirmed "
            "stay. Nobody can get the deleted transcripts back.",
        )
        self.assertEqual(
            [i.item.label for i in edit["view"].children], ["Delete after 90 days", "Keep as it is"]
        )

    async def test_its_button_saves_it(self) -> None:
        it = press(7, campaign(None), saved=campaign(90))
        await keep.KeepYesButton(CID, "90").callback(it)
        it.client.campaigns.set_transcript_keep.assert_awaited_once_with(1, CID, 90)
        self.assertIn(
            "Saved. Sessions older than 90 days",
            it.edit_original_response.await_args.kwargs["content"],
        )

    async def test_saving_answers_discord_before_the_database(self) -> None:
        line = Timeline()
        it = press(7, campaign(None), saved=campaign(90))
        it.response, it.edit_original_response = line.response, line.edit_original_response
        it.client.campaigns.set_transcript_keep = line.slow("set_transcript_keep", campaign(90))
        await keep.KeepYesButton(CID, "90").callback(it)
        line.assert_answered_first()
        self.assertIn("slow:set_transcript_keep", line.events)

    async def test_cancel_changes_nothing(self) -> None:
        it = press(7, campaign(365))
        await keep.KeepNoButton(CID).callback(it)
        it.client.campaigns.set_transcript_keep.assert_not_awaited()
        self.assertIn("**1 year**", it.response.edit_message.await_args.kwargs["content"])

    async def test_a_longer_choice_and_forever_save_at_once(self) -> None:
        for current, choice, days in (
            (30, "90", 90),
            (90, "forever", None),
            (None, "forever", None),
        ):
            it = press(7, campaign(current), saved=campaign(days))
            await keep.KeepChoiceButton(CID, choice).callback(it)
            if current == days:  # already that: just the panel again
                it.client.campaigns.set_transcript_keep.assert_not_awaited()
            else:
                it.client.campaigns.set_transcript_keep.assert_awaited_once_with(1, CID, days)

    async def test_a_failure_says_nothing_changed(self) -> None:
        it = press(7, campaign(None))
        it.client.campaigns.set_transcript_keep = AsyncMock(side_effect=RuntimeError("down"))
        with self.assertLogs("dmbot.dm_screen.transcript_keep", "ERROR"):
            await keep.KeepYesButton(CID, "30").callback(it)
        self.assertEqual(it.edit_original_response.await_args.kwargs["content"], keep.COULDNT)

    async def test_a_refused_choice_is_told_in_its_words(self) -> None:
        it = press(7, campaign(None))
        it.client.campaigns.set_transcript_keep = AsyncMock(side_effect=CampaignError("Nope."))
        await keep.KeepYesButton(CID, "30").callback(it)
        self.assertEqual(it.edit_original_response.await_args.kwargs["content"], "Nope.")

    def test_shorter_than(self) -> None:
        self.assertTrue(keep._shorter(90, None))
        self.assertTrue(keep._shorter(30, 90))
        self.assertFalse(keep._shorter(365, 90))
        self.assertFalse(keep._shorter(None, 30))
        self.assertFalse(keep._shorter(90, 90))

    def test_the_buttons_fit_a_phone_and_their_ids_match_their_templates(self) -> None:
        items = [
            keep.TranscriptsButton(CID),
            *(keep.KeepChoiceButton(CID, c) for c, _ in keep.CHOICES),
            *(keep.KeepYesButton(CID, c) for c, _ in keep.CHOICES),
            keep.KeepNoButton(CID),
        ]
        for item in items:
            self.assertLessEqual(len(str(item.item.label)), 25)
            template = type(item).__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))


if __name__ == "__main__":
    unittest.main()


class NightlyRun(unittest.IsolatedAsyncioTestCase):
    """The plan job and the transcript job are independent (#1533): one failing, or the plan
    job not existing at all, never stops the other."""

    def bot(self, plan: Any, keep: Any) -> Any:
        from pathlib import Path

        from dmbot.bot import DMBot

        bot: Any = DMBot.__new__(DMBot)
        bot.retention, bot.keep_job, bot.sources = plan, keep, None
        bot.check_owners_daily = AsyncMock()
        bot.settings = SimpleNamespace(data_dir=Path("/nonexistent"))
        return bot

    async def test_a_failing_plan_job_still_lets_the_transcripts_go(self) -> None:
        from unittest.mock import patch

        keep_job = SimpleNamespace(run_once=AsyncMock())
        plan = SimpleNamespace(run_once=AsyncMock(side_effect=RuntimeError("down")))
        bot = self.bot(plan, keep_job)
        with (
            self.assertLogs("dmbot.bot", "ERROR"),
            patch("dmbot.bot.retention.write_last_run") as wrote,
        ):
            await bot._retention_once(bot.settings.data_dir)
        keep_job.run_once.assert_awaited_once()
        wrote.assert_not_called()  # the plan job didn't run, so the run isn't counted as done

    async def test_with_no_plan_job_the_transcripts_still_go(self) -> None:
        from unittest.mock import patch

        keep_job = SimpleNamespace(run_once=AsyncMock())
        bot = self.bot(None, keep_job)
        with patch("dmbot.bot.retention.write_last_run") as wrote:
            await bot._retention_once(bot.settings.data_dir)
        keep_job.run_once.assert_awaited_once()
        wrote.assert_called_once()

    async def test_a_failing_transcript_job_never_stops_the_plan_job(self) -> None:
        from unittest.mock import patch

        keep_job = SimpleNamespace(run_once=AsyncMock(side_effect=RuntimeError("down")))
        plan = SimpleNamespace(run_once=AsyncMock())
        bot = self.bot(plan, keep_job)
        with (
            self.assertLogs("dmbot.bot", "ERROR"),
            patch("dmbot.bot.retention.write_last_run") as wrote,
        ):
            await bot._retention_once(bot.settings.data_dir)
        plan.run_once.assert_awaited_once()
        wrote.assert_called_once()
