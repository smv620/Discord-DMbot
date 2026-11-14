"""The DM's check of the names DMbot joined itself (#3602, slice 4b): the words, the count line,
the home button, and the presses (DM only, one answer each). Invented names; no Discord, no
database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory._changes import ALREADY_UNDONE, NOT_FOUND
from dmbot.memory.models import MemoryRuleError, WaitingJoin
from dmbot.ui import joined_review as ui
from dmbot.ui.names import NamesHome, Reviews, review_line

JOIN = WaitingJoin(41, "k" * 32, "g" * 32, "The Ferry Lady", "Old Tam", 1)
TWO = WaitingJoin(42, "k" * 32, "h" * 32, "The Net Mender", "Old Tam", 2)


def labels(view: Any) -> list[str]:
    return [getattr(c, "label", "") for c in view.children]


class Words(unittest.TestCase):
    def test_the_question_names_both_and_says_players_wait(self) -> None:
        text = ui.question_text(JOIN, 1)
        self.assertIn("**The Ferry Lady**", text)
        self.assertIn("**Old Tam**", text)
        self.assertIn("DMbot guessed that **The Ferry Lady** is **Old Tam**", text)
        self.assertIn("Players and recaps still treat them as two names", text)
        self.assertNotIn("more after", text)
        self.assertIn("1 more after this", ui.question_text(JOIN, 2))

    def test_the_count_line_says_how_many_and_nothing_else(self) -> None:
        self.assertEqual(ui.summary_line(1), "🤖 DMbot matched 1 name on its own. Please check it.")
        self.assertEqual(
            ui.summary_line(4), "🤖 DMbot matched 4 names on its own. Please check them."
        )

    def test_names_with_markdown_are_escaped(self) -> None:
        odd = WaitingJoin(1, "k", "g", "**Bold**", "_It_", 1)
        text = ui.question_text(odd, 1)
        self.assertNotIn("**Bold**", text.replace("\\*\\*Bold\\*\\*", ""))


class OnTheHome(unittest.TestCase):
    def test_the_home_line_and_button_show_only_while_something_waits(self) -> None:
        self.assertIsNone(review_line(Reviews()))
        self.assertIn("2 names DMbot matched on its own", review_line(Reviews(joined=2)) or "")
        none = NamesHome("c1", 0, None, Reviews())
        self.assertFalse([x for x in labels(none) if "guesses" in x])
        some = NamesHome("c1", 0, None, Reviews(joined=3))
        self.assertIn("🤖 Check guesses (3)", labels(some))
        self.assertLessEqual(len(some.children), 25)


class Pressing(unittest.IsolatedAsyncioTestCase):
    def setup(self, *, dm: bool = True, joins: list[WaitingJoin] | None = None) -> Any:
        self.memory = SimpleNamespace(
            waiting_joins=AsyncMock(return_value=list(joins if joins is not None else [JOIN])),
            confirm_join=AsyncMock(),
            undo=AsyncMock(),
        )
        self.told: list[str] = []
        self.replaced: list[str] = []
        self.changed = 0

        async def tell(_i: Any, text: str, **_k: Any) -> None:
            self.told.append(text)

        async def replace(_i: Any, text: str, _view: Any) -> None:
            self.replaced.append(text)

        def changed(*_a: Any) -> None:
            self.changed += 1

        patches: list[Any] = [
            patch.object(ui, "_memory", lambda _i: self.memory),
            patch.object(
                ui, "_campaign_for", AsyncMock(return_value=SimpleNamespace(id="c1", guild_id=5))
            ),
            patch.object(ui, "sees_secrets", lambda _c, _u: dm),
            patch.object(ui, "changed", changed),
            patch.object(ui, "_tell", tell),
            patch.object(ui, "_replace", replace),
            patch.object(ui, "_answer_first", AsyncMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # (no guild on the interaction: a DM in a private chat works from the campaign's own)
        interaction = SimpleNamespace(guild=None, user=SimpleNamespace(id=9))
        view = ui.JoinedReview("c1")
        return interaction, view

    async def test_yes_confirms_that_batch_in_the_campaigns_own_server(self) -> None:
        interaction, view = self.setup()
        await view.current_text(interaction, 5)
        await view._yes(interaction)
        self.memory.confirm_join.assert_awaited_once_with(5, "c1", 41)
        self.assertIn("Players can see it", self.replaced[-1])
        self.assertEqual(self.changed, 1)

    async def test_undo_takes_back_that_batch(self) -> None:
        interaction, view = self.setup()
        await view.current_text(interaction, 5)
        await view._undo(interaction)
        self.memory.undo.assert_awaited_once_with(5, "c1", 41)
        self.assertIn("goes back to your list of names to check", self.replaced[-1])

    async def test_a_refused_undo_says_so_and_changes_nothing_else(self) -> None:
        interaction, view = self.setup()
        self.memory.undo.side_effect = MemoryRuleError("x")
        await view.current_text(interaction, 5)
        await view._undo(interaction)
        self.assertEqual(self.told, [ui.CHANGED])
        self.assertEqual(self.replaced, [])

    async def test_a_guess_already_answered_elsewhere_moves_on_with_the_right_words(self) -> None:
        for press, error in (("_yes", NOT_FOUND), ("_undo", ALREADY_UNDONE), ("_undo", NOT_FOUND)):
            interaction, view = self.setup()
            self.memory.confirm_join.side_effect = MemoryRuleError(error)
            self.memory.undo.side_effect = MemoryRuleError(error)
            await view.current_text(interaction, 5)
            self.memory.waiting_joins.return_value = []  # (it is gone now)
            await getattr(view, press)(interaction)
            self.assertIn(ui.GONE, self.replaced[-1], (press, error))
            self.assertEqual(self.told, [])

    async def test_a_player_can_do_nothing(self) -> None:
        interaction, view = self.setup(dm=False)
        await view.current_text(interaction, 5)
        for press in (view._yes, view._undo, view._all, view._later):
            await press(interaction)
        self.assertEqual(self.told, [ui.ONLY_DMS] * 4)
        self.assertFalse(self.memory.confirm_join.await_count + self.memory.undo.await_count)
        self.assertEqual(self.replaced, [])

    async def test_all_are_right_confirms_each_but_not_one_left_for_later(self) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        await view.current_text(interaction, 5)
        view.later.add(42)
        await view._all(interaction)
        self.memory.confirm_join.assert_awaited_once_with(5, "c1", 41)
        self.assertIn("1 name is now matched", self.replaced[-1])

    async def test_all_are_right_skips_one_answered_meanwhile_and_says_when_none_were_left(
        self,
    ) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        self.memory.confirm_join.side_effect = [None, MemoryRuleError("gone")]
        await view.current_text(interaction, 5)
        await view._all(interaction)
        self.assertEqual(self.memory.confirm_join.await_count, 2)
        self.assertIn("1 name is now matched", self.replaced[-1])
        self.memory.confirm_join.side_effect = MemoryRuleError("gone")
        await view._all(interaction)
        self.assertIn(ui.NOTHING_LEFT, self.replaced[-1])

    async def test_the_cache_is_refreshed_even_if_all_are_right_breaks_part_way(self) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        self.memory.confirm_join.side_effect = [None, RuntimeError("boom")]
        await view.current_text(interaction, 5)
        with self.assertRaises(RuntimeError):
            await view._all(interaction)
        self.assertEqual(self.changed, 1)

    async def test_later_leaves_it_waiting_and_all_are_right_needs_more_than_one(self) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        await view.current_text(interaction, 5)
        self.assertIn("✅ All are right", labels(view))
        await view._later(interaction)
        self.assertEqual(view.later, {41})
        _i, single = self.setup()
        await single.current_text(interaction, 5)
        self.assertNotIn("✅ All are right", labels(single))

    async def test_a_failed_first_read_says_so_instead_of_nothing_waiting(self) -> None:
        interaction, _view = self.setup()
        self.memory.waiting_joins.side_effect = RuntimeError("down")
        with patch.object(ui, "_send", AsyncMock()) as send:
            await ui.start_joined_review(interaction, "c1")
        self.assertEqual(self.told, [ui.COULDNT_LOAD])
        self.assertFalse(send.await_count)
