"""The 📋 screens' forms stop the menu that opened them once sent and done (#4504), so the old
menu's timeout never blanks the newer screen that took its message ten minutes later."""

from __future__ import annotations

import ast
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from dmbot.memory import thread_parts as tp
from dmbot.ui import thread_parts as ui
from tests.test_thread_parts import part


def _fake_interaction() -> SimpleNamespace:
    return SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))


def _empty_words(form: ui.WordsForm) -> None:
    form.words._value = "   "  # (as Discord fills it in; empty words go straight back to 📋)


class SendingAFormStopsItsMenu(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.step = part(tp.OBJECTIVE, words="Find the map")
        self.menu = ui.PartMenu("c1", "t", self.step, [], {})
        self.form = ui.WordsForm("c1", "t", self.step)
        self.interaction = _fake_interaction()
        await ui._open_form(self.interaction, self.menu, self.form)  # type: ignore[arg-type]
        _empty_words(self.form)

    async def test_opening_a_form_leaves_its_menu_running(self) -> None:
        self.interaction.response.send_modal.assert_awaited_once_with(self.form)
        self.assertIs(self.form.opener, self.menu)
        self.assertFalse(self.menu.is_finished())  # (open, or closed unsent: still working)

    async def test_a_sent_form_stops_its_menu_once_the_new_screen_is_up(self) -> None:
        with (
            patch.object(ui, "_answer_first", AsyncMock()),
            patch.object(ui, "open_parts", AsyncMock()) as redrawn,
        ):
            await self.form.on_submit(self.interaction)  # type: ignore[arg-type]
        redrawn.assert_awaited_once()
        self.assertTrue(self.menu.is_finished())  # (stopped: its timeout never fires)

    async def test_a_failed_form_leaves_its_menu_working_to_try_again(self) -> None:
        with (
            patch.object(ui, "_answer_first", AsyncMock()),
            patch.object(ui, "open_parts", AsyncMock(side_effect=RuntimeError("down"))),
            self.assertRaises(RuntimeError),
        ):
            await self.form.on_submit(self.interaction)  # type: ignore[arg-type]
        self.assertFalse(self.menu.is_finished())

    async def test_a_form_opened_with_no_menu_stops_nothing(self) -> None:
        form = ui.WordsForm("c1", "t", self.step)
        _empty_words(form)
        with (
            patch.object(ui, "_answer_first", AsyncMock()),
            patch.object(ui, "open_parts", AsyncMock()) as redrawn,
        ):
            await form.on_submit(self.interaction)  # type: ignore[arg-type]
        redrawn.assert_awaited_once()


class EveryFormKnowsItsMenu(unittest.TestCase):
    """Watch for (#4504): a new form on these screens that opens straight with `send_modal`, or
    one that writes its own `on_submit` (skipping the stop), brings the blank screen back."""

    def test_forms_open_only_through_open_form(self) -> None:
        tree = ast.parse(inspect.getsource(ui))
        callers = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute) and node.attr == "send_modal"
        }
        self.assertEqual(callers, {"_open_form"})

    def test_every_form_is_a_form_that_stops_its_menu(self) -> None:
        modals = {
            c
            for c in vars(ui).values()
            if isinstance(c, type)
            and issubclass(c, discord.ui.Modal)
            and c.__module__ == ui.__name__
            and c is not ui._Form
        }
        self.assertEqual({c.__name__ for c in modals}, {"AddForm", "DaysForm", "WordsForm"})
        for form in modals:
            self.assertTrue(issubclass(form, ui._Form), form.__name__)
            self.assertIs(form.on_submit, ui._Form.on_submit, form.__name__)
            self.assertIn("_submit", vars(form), form.__name__)
