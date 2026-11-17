"""A menu's timeout never blanks a newer screen (#4521). A form a menu opened can be sent, and a
new screen then takes the same message; discord.py never tells the old menu, so its timeout used to
edit the message to "This menu timed out" ten minutes later. The timeout now looks at what the
message shows and edits only while the menu's own buttons are still there. Stand-ins; no network."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from dmbot.ui.dmbot_commands import TIMED_OUT, _Button, _Menu, showing_menu


async def _noop(interaction: Any) -> None: ...


def menu() -> _Menu:
    made = _Menu()
    made.add_item(_Button(_noop, label="One"))
    made.add_item(_Button(_noop, label="Two"))
    return made


def edits(made: _Menu) -> Any:
    """The stand-in origin's edit, typed loosely (it is a stand-in)."""
    return cast(Any, made.origin).edit_original_response


def origin_showing(*ids: str) -> Any:
    """An interaction whose answer's message carries buttons with these custom ids."""
    row = SimpleNamespace(children=[SimpleNamespace(custom_id=i) for i in ids])
    message = SimpleNamespace(components=[row])
    return SimpleNamespace(
        original_response=AsyncMock(return_value=message), edit_original_response=AsyncMock()
    )


class TheTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_a_menu_still_on_its_message_says_it_timed_out(self) -> None:
        made = menu()
        shown = [cast(Any, i).custom_id for i in made.children]
        made.origin = origin_showing(*shown)
        await made.on_timeout()
        edits(made).assert_awaited_once_with(content=TIMED_OUT, view=None)

    async def test_a_newer_screen_on_the_message_is_left_alone(self) -> None:
        made = menu()
        made.origin = origin_showing("another-screens-button")
        await made.on_timeout()
        edits(made).assert_not_awaited()

    async def test_a_message_with_no_buttons_is_left_alone(self) -> None:
        made = menu()
        made.origin = origin_showing()
        await made.on_timeout()
        edits(made).assert_not_awaited()

    async def test_a_menu_with_no_buttons_of_its_own_still_says_so(self) -> None:
        made = _Menu()
        made.origin = origin_showing("x")
        await made.on_timeout()
        edits(made).assert_awaited_once()

    async def test_a_message_that_cannot_be_read_is_not_edited_and_never_raises(self) -> None:
        made = menu()
        made.origin = origin_showing()
        gone = discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="gone")), "gone")
        cast(Any, made.origin).original_response = AsyncMock(side_effect=gone)
        await made.on_timeout()
        edits(made).assert_not_awaited()

    async def test_showing_menu_reads_select_menus_too(self) -> None:
        made = _Menu()
        pick: discord.ui.Select[Any] = discord.ui.Select(options=[discord.SelectOption(label="a")])
        made.add_item(pick)
        self.assertTrue(await showing_menu(origin_showing(pick.custom_id), made))
        self.assertFalse(await showing_menu(origin_showing("other"), made))


if __name__ == "__main__":
    unittest.main()
