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
                plot_gate=False,
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


def three(act: str, user: int = DM_ID, *, why: str = "", problems: list[str] | None = None,
          found: Any = None, approved: bool = True) -> Any:  # fmt: skip
    """The same, for the three ways to approve (#2420): `why` is why audio can't be made."""
    button, bot, interaction = armed(
        act, user, problems=problems, found=found or with_text(), approved=approved
    )
    bot.recap_audio_why = AsyncMock(return_value=why)
    return button, bot, interaction


class TheThreeWays(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_may_press_any_of_them(self) -> None:
        for act in ("ap", "aa", "ak"):
            button, bot, interaction = three(act, PLAYER)
            await button.callback(interaction)
            self.assertEqual(told(interaction), NOT_YOURS)
            bot.recaps.approve.assert_not_awaited()

    async def test_approve_and_keep_approves_text_only_and_posts_nothing(self) -> None:
        button, bot, interaction = three("ak")
        await button.callback(interaction)
        bot.recaps.approve.assert_awaited_once_with(
            5, "c" * 32, AT, DM_ID, "text", expected=TEXT_HASH, post=False, plot_gate=False
        )
        self.assertEqual(told(interaction), screen.KEPT)
        bot.post_recaps.assert_not_awaited()

    async def test_approve_and_post_approves_text_only_and_hands_it_to_the_bot(self) -> None:
        button, bot, interaction = three("ap")
        bot.recaps.get = AsyncMock(side_effect=[with_text(), with_text(status="posted")])
        await button.callback(interaction)
        bot.recaps.approve.assert_awaited_once_with(
            5, "c" * 32, AT, DM_ID, "text", expected=TEXT_HASH, post=True, plot_gate=False
        )
        bot.post_recaps.assert_awaited_once_with(5)
        self.assertEqual(interaction.followup.send.await_args.args[0], screen.POSTED)

    async def test_a_post_that_the_bots_checks_stop_says_so_and_nothing_is_lost(self) -> None:
        button, bot, interaction = three("ap")
        bot.recaps.get = AsyncMock(
            side_effect=[with_text(), with_text(last_error="A name is secret.")]
        )
        await button.callback(interaction)
        said = interaction.followup.send.await_args.args[0]
        self.assertIn("Not posted: A name is secret.", said)

    async def test_audio_asks_which_when_it_can_be_made_and_approves_nothing_yet(self) -> None:
        button, bot, interaction = three("aa")
        await button.callback(interaction)
        self.assertEqual(told(interaction), screen.ASK_WHICH_AUDIO)
        self.assertEqual(
            labels_of(view_of(interaction)), ["🎙️ Voice-over", "🎵 Background music", "Both"]
        )  # (text only is the other two buttons)
        bot.recaps.approve.assert_not_awaited()

    async def test_audio_that_cannot_be_made_is_refused_with_the_reason_for_each(self) -> None:
        for why in ("not_set_up", "unavailable"):
            button, bot, interaction = three("aa", why=why)
            await button.callback(interaction)
            self.assertEqual(told(interaction), screen.AUDIO_WHY[why])
            bot.recaps.approve.assert_not_awaited()

    async def test_cue_problems_stop_all_three_and_nothing_is_approved(self) -> None:
        for act in ("ap", "aa", "ak"):
            button, bot, interaction = three(act, problems=["Paragraph 1: bad mood."])
            await button.callback(interaction)
            self.assertIn(CUES_FIRST, told(interaction))
            bot.recaps.approve.assert_not_awaited()

    async def test_an_approved_text_and_a_decided_recap_change_nothing(self) -> None:
        for act in ("ap", "aa", "ak"):
            button, bot, interaction = three(act, found=job(approved=True, audio_choice="both"))
            await button.callback(interaction)
            self.assertEqual(told(interaction), screen.APPROVED_ALREADY)
            button, bot, interaction = three(act, found=with_text(status="posted"))
            await button.callback(interaction)
            self.assertEqual(told(interaction), DECIDED)
            bot.recaps.approve.assert_not_awaited()

    async def test_post_finishes_a_text_approval_kept_or_stopped_without_approving_again(
        self,
    ) -> None:
        # (#2420) approved as text only, then kept, or stopped by the bot's checks and put back to
        # a draft: Approve and post text only posts it, only for the text as approved
        button, bot, interaction = three("ap", found=job(approved=True, audio_choice="text"))
        bot.recaps.decide = AsyncMock(return_value=True)
        bot.recaps.get = AsyncMock(
            side_effect=[job(approved=True, audio_choice="text"), job(status="posted")]
        )
        await button.callback(interaction)
        bot.recaps.approve.assert_not_awaited()
        bot.recaps.decide.assert_awaited_once_with(5, "c" * 32, AT, "post", require_approved=True)
        bot.post_recaps.assert_awaited_once_with(5)
        # an approval with audio chosen is not finished this way, and a text that changed since
        button, bot, interaction = three("ap", found=job(approved=True, audio_choice="both"))
        await button.callback(interaction)
        self.assertEqual(told(interaction), screen.APPROVED_ALREADY)
        button, bot, interaction = three("ap", found=job(approved=True, audio_choice="text"))
        bot.recaps.decide = AsyncMock(return_value=False)
        await button.callback(interaction)
        self.assertEqual(told(interaction), TOO_LATE)
        bot.post_recaps.assert_not_awaited()

    async def test_a_store_that_refuses_says_so(self) -> None:
        for act in ("ap", "ak"):
            button, _bot, interaction = three(act, approved=False)
            await button.callback(interaction)
            self.assertEqual(told(interaction), TOO_LATE)

    def test_the_line_has_the_three_in_the_pages_words_the_review_link_and_no_dont_post(
        self,
    ) -> None:
        view = screen.ready_view(
            CID, AT, "https://site.example.com", "https://site.example.com/r/1"
        )
        labels = labels_of(view)
        self.assertEqual(
            labels[:3],
            ["✅ Approve and post text only", "🎙️ Approve and make audio to review",
             "📋 Approve but don't post"],
        )  # fmt: skip
        self.assertIn("Review the recap", labels)
        self.assertFalse(any("Don't post" in x or "Not this one" in x for x in labels))
        link = next(c for c in view.children if getattr(c, "url", None))
        self.assertEqual(link.url, "https://site.example.com/r/1")  # type: ignore[attr-defined]
        rows: dict[int, int] = {}
        for c in view.children:
            r = getattr(getattr(c, "item", c), "row", 0) or 0
            rows[r] = rows.get(r, 0) + 1
        self.assertLessEqual(max(rows.values()), 5)
        self.assertLessEqual(max(rows), 4)

    def test_the_audio_button_is_greyed_only_when_audio_is_off(self) -> None:
        on = screen.ready_view(CID, AT, "https://site.example.com").children
        off = screen.ready_view(CID, AT, "https://site.example.com", audio_off=True).children

        def disabled(children: list[Any]) -> list[bool]:
            return [bool(getattr(getattr(c, "item", c), "disabled", False)) for c in children[:3]]

        self.assertEqual(disabled(on), [False, False, False])
        self.assertEqual(disabled(off), [False, True, False])

    def test_without_a_website_read_it_stands_in_for_the_review_link(self) -> None:
        labels = labels_of(screen.ready_view(CID, AT))
        self.assertEqual(labels[3], "Read it")
        self.assertNotIn("Review the recap", labels)

    def test_the_ids_fit_discord_and_survive_a_restart(self) -> None:
        for act in ("ap", "aa", "ak"):
            custom = RecapButton(act, CID, AT).item.custom_id
            assert custom is not None
            self.assertLessEqual(len(custom), 100)
            self.assertRegex(custom, r"^dmbot:recap:(ap|aa|ak):[0-9a-f]{32}:[0-9]{1,12}$")


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
