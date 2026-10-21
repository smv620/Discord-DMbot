"""✅ Approve the text on the DM screen (#2083): only a DM, the cues and secrets checked on every
press with nothing approved when there is a problem, only the audio the plan includes offered,
each choice approving through the store, and the buttons that survive a restart."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.recaps import approval
from dmbot.ui import recap_dm as screen
from dmbot.ui.recap_dm import (
    APPROVED,
    CUES_FIRST,
    DECIDED,
    NOT_READY,
    NOT_YOURS,
    TOO_LATE,
    RecapButton,
    choice_view,
)
from tests.test_recap_dm import AT, CID, DM_ID, PLAYER, job, press, told

TEXT_HASH = approval.text_hash("The Silver Lock", "《Tense》 Mira drew her blade.")


def armed(act: str, user: int = DM_ID, *, problems: list[str] | None = None, audio: bool = True,
          found: Any = "default", approved: bool = True) -> Any:  # fmt: skip
    button, bot, interaction = press(act, user, found=found)
    bot.recap_approval_problems = AsyncMock(return_value=problems or [])
    bot.recap_audio_wanted = AsyncMock(return_value=audio)
    bot.recaps.approve = AsyncMock(return_value=approved)
    return button, bot, interaction


def labels_of(view: Any) -> list[str]:
    return [str(getattr(getattr(c, "item", c), "label", "")) for c in view.children]


def view_of(interaction: Any) -> Any:
    return interaction.response.send_message.await_args.kwargs["view"]


def with_text(**kw: Any) -> Any:
    return job(approved=False, audio_choice="", **kw)


class TheApproveButton(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_may_press_approve_or_a_choice(self) -> None:
        for act in ("approve", "cv", "cm", "cb", "ct"):
            button, bot, interaction = armed(act, PLAYER, found=with_text())
            await button.callback(interaction)
            self.assertEqual(told(interaction), NOT_YOURS)
            bot.recaps.approve.assert_not_awaited()

    async def test_a_recap_still_being_written_or_already_decided_is_not_approved(self) -> None:
        button, bot, interaction = armed("approve", found=with_text(state="beats"))
        await button.callback(interaction)
        self.assertEqual(told(interaction), NOT_READY)
        button, bot, interaction = armed("cb", found=with_text(status="posted"))
        await button.callback(interaction)
        self.assertEqual(told(interaction), DECIDED)
        bot.recaps.approve.assert_not_awaited()

    async def test_cue_problems_are_listed_with_the_allowed_words_and_nothing_is_approved(
        self,
    ) -> None:
        problems = ["Paragraph 1: 《Tens》 is not a word DMbot knows. Did you mean tense?"]
        for act in ("approve", "cb"):
            button, bot, interaction = armed(act, problems=problems, found=with_text())
            await button.callback(interaction)
            text = told(interaction)
            self.assertIn(CUES_FIRST, text)
            self.assertIn("Did you mean tense?", text)
            self.assertIn("tense", text)  # the allowed moods
            self.assertIn("long pause", text)  # and the pacing words
            bot.recaps.approve.assert_not_awaited()

    async def test_a_long_list_of_problems_is_cut_and_says_how_many_more(self) -> None:
        button, _bot, interaction = armed(
            "approve", problems=[f"Problem {n}." for n in range(12)], found=with_text()
        )
        await button.callback(interaction)
        text = told(interaction)
        self.assertIn("Problem 7.", text)
        self.assertNotIn("Problem 8.", text)
        self.assertIn("4 more", text)

    async def test_a_clean_text_offers_the_four_choices(self) -> None:
        button, bot, interaction = armed("approve", found=with_text())
        await button.callback(interaction)
        self.assertEqual(labels_of(view_of(interaction)), list(approval.LABELS.values()))
        bot.recaps.approve.assert_not_awaited()  # approving is the choice, not the first press

    async def test_a_plan_without_audio_offers_text_only(self) -> None:
        button, _bot, interaction = armed("approve", audio=False, found=with_text())
        await button.callback(interaction)
        self.assertEqual(labels_of(view_of(interaction)), ["Text only"])

    async def test_each_choice_approves_with_it(self) -> None:
        for act, choice in (("cv", "voice"), ("cm", "music"), ("cb", "both"), ("ct", "text")):
            button, bot, interaction = armed(act, found=with_text())
            await button.callback(interaction)
            bot.recaps.approve.assert_awaited_once_with(
                5,
                "c" * 32,
                AT,
                DM_ID,
                choice,
                expected=approval.text_hash("The Silver Lock", "《Tense》 Mira drew her blade."),
            )
            self.assertEqual(told(interaction), APPROVED[choice])

    async def test_a_voice_or_music_choice_without_audio_in_the_plan_is_refused(self) -> None:
        for act in ("cv", "cm", "cb"):
            button, bot, interaction = armed(act, audio=False, found=with_text())
            await button.callback(interaction)
            self.assertEqual(told(interaction), screen.NO_AUDIO)
            bot.recaps.approve.assert_not_awaited()
        button, bot, interaction = armed("ct", audio=False, found=with_text())
        await button.callback(interaction)
        bot.recaps.approve.assert_awaited_once()

    async def test_a_store_that_refuses_says_so(self) -> None:
        button, _bot, interaction = armed("cb", approved=False, found=with_text())
        await button.callback(interaction)
        self.assertEqual(told(interaction), TOO_LATE)

    async def test_an_approved_text_says_so_instead_of_asking_again(self) -> None:
        button, _bot, interaction = armed("approve", found=job(approved=True, audio_choice="both"))
        await button.callback(interaction)
        self.assertEqual(told(interaction), screen.APPROVED_ALREADY)


class TheButtonsThemselves(unittest.TestCase):
    def test_the_approve_button_has_an_id_that_survives_a_restart(self) -> None:
        self.assertEqual(
            RecapButton("approve", CID, AT).item.custom_id, f"dmbot:recap:approve:{CID}:{AT}"
        )
        for act in ("approve", "cv", "cm", "cb", "ct"):
            longest = f"dmbot:recap:{act}:{CID}:{10**11}"
            self.assertLessEqual(len(longest), 100)
            self.assertIsNotNone(screen._TEMPLATE and RecapButton(act, CID, AT))

    def test_the_choice_view_shows_only_what_is_offered(self) -> None:
        labels = labels_of(choice_view(CID, AT, ("voice", "text")))
        self.assertEqual(labels, ["🎙️ Voice-over", "Text only"])
