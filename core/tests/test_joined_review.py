"""The DM's check of the names DMbot joined itself (#3602, slice 4b): the words, the count line,
the home button, and the presses (DM only, one answer each). Invented names; no Discord, no
database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

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
        self.assertIn("won't see it until you say it's right", text)
        self.assertNotIn("more after", text)
        self.assertIn("1 more after this", ui.question_text(JOIN, 2))

    def test_the_count_line_says_how_many_and_nothing_else(self) -> None:
        self.assertEqual(ui.summary_line(1), "🤖 DMbot answered 1 name itself: check it.")
        self.assertEqual(ui.summary_line(4), "🤖 DMbot answered 4 names itself: check them.")

    def test_names_with_markdown_are_escaped(self) -> None:
        odd = WaitingJoin(1, "k", "g", "**Bold**", "_It_", 1)
        text = ui.question_text(odd, 1)
        self.assertNotIn("**Bold**", text.replace("\\*\\*Bold\\*\\*", ""))


class OnTheHome(unittest.TestCase):
    def test_the_home_line_and_button_show_only_while_something_waits(self) -> None:
        self.assertIsNone(review_line(Reviews()))
        self.assertIn("2 names DMbot joined itself", review_line(Reviews(joined=2)) or "")
        none = NamesHome("c1", 0, None, Reviews())
        self.assertFalse([x for x in labels(none) if "joins" in x])
        some = NamesHome("c1", 0, None, Reviews(joined=3))
        self.assertIn("🤖 Check DMbot's joins (3)", labels(some))
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

        async def tell(_i: Any, text: str, **_k: Any) -> None:
            self.told.append(text)

        async def replace(_i: Any, text: str, _view: Any) -> None:
            self.replaced.append(text)

        patches: list[Any] = [
            patch.object(ui, "_memory", lambda _i: self.memory),
            patch.object(ui, "_campaign_for", AsyncMock(return_value=SimpleNamespace(id="c1"))),
            patch.object(ui, "sees_secrets", lambda _c, _u: dm),
            patch.object(ui, "changed", lambda *_a: None),
            patch.object(ui, "_tell", tell),
            patch.object(ui, "_replace", replace),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=9))
        view = ui.JoinedReview("c1")
        return interaction, view

    async def test_yes_confirms_that_batch_and_moves_on(self) -> None:
        interaction, view = self.setup()
        await view.current_text(interaction)
        await view._yes(interaction)
        self.memory.confirm_join.assert_awaited_once_with(1, "c1", 41)
        self.assertIn("is now another name of", self.replaced[-1])

    async def test_undo_takes_back_that_batch(self) -> None:
        interaction, view = self.setup()
        await view.current_text(interaction)
        await view._undo(interaction)
        self.memory.undo.assert_awaited_once_with(1, "c1", 41)
        self.assertIn("Undone", self.replaced[-1])

    async def test_a_refused_undo_says_so_and_changes_nothing_else(self) -> None:
        interaction, view = self.setup()
        self.memory.undo.side_effect = MemoryRuleError("x")
        await view.current_text(interaction)
        await view._undo(interaction)
        self.assertEqual(self.told, [ui.CHANGED])
        self.assertEqual(self.replaced, [])

    async def test_a_join_answered_elsewhere_says_it_was_answered(self) -> None:
        interaction, view = self.setup()
        self.memory.confirm_join.side_effect = MemoryRuleError(
            "DMbot doesn't remember that any more."
        )
        await view.current_text(interaction)
        await view._yes(interaction)
        self.assertEqual(self.told, [ui.GONE])

    async def test_a_player_can_do_nothing(self) -> None:
        interaction, view = self.setup(dm=False)
        await view.current_text(interaction)
        for press in (view._yes, view._undo, view._all, view._later):
            await press(interaction)
        self.assertEqual(self.told, [ui.ONLY_DMS] * 4)
        self.assertFalse(self.memory.confirm_join.await_count + self.memory.undo.await_count)

    async def test_all_fine_confirms_each_and_skips_one_answered_meanwhile(self) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        self.memory.confirm_join.side_effect = [None, MemoryRuleError("gone")]
        await view.current_text(interaction)
        await view._all(interaction)
        self.assertEqual(self.memory.confirm_join.await_count, 2)
        self.assertIn("1 confirmed", self.replaced[-1])

    async def test_later_leaves_it_waiting_and_all_fine_needs_more_than_one(self) -> None:
        interaction, view = self.setup(joins=[JOIN, TWO])
        await view.current_text(interaction)
        self.assertIn("✅ All fine", labels(view))
        await view._later(interaction)
        self.assertEqual(view.later, {41})
        _i, single = self.setup()
        await single.current_text(interaction)
        self.assertNotIn("✅ All fine", labels(single))
