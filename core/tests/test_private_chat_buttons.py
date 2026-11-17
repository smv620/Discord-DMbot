"""A button on a private message works with no server (#4533, follow-up to #4528).

A DM answers many of DMbot's questions in a private chat, where `interaction.guild` is None. A
button written for a server channel that reads `interaction.guild` and answers "Use this in a
server." there is broken for that DM. This walks every registered `DynamicItem`, builds one from
a sample id made from its own pattern, presses it as a user with no server, and fails on any that
answers "Use this in a server". Pressed against a stand-in client, so a handler may stop early on
the stand-in's answers; what is checked is only that none of them refuses for want of a server.

Not covered, said plainly: a non-DM's press and "reaches its campaign lookup" are per button and
need that button's own fixtures (each module's tests); and `heard_review.py` and
`said_as_review.py` read `interaction.guild` and do nothing when it is None (they do not answer
with the sentence, so this walk cannot see them): `KNOWN_NEEDS_A_SERVER` lists them, and the test
below names them so they are not forgotten (filed as #4630).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from tests.sample_custom_ids import sample
from tests.test_dynamic_items import registered_items

# Server-only on purpose: the item and why. Only the main menu's own button answers so.
SERVER_ONLY: dict[str, str] = {
    "ImportButton": "📥 Import sessions on the main menu: it starts in a server channel (#2154)",
}

# Read `interaction.guild` and do nothing with none (found by reading, not by the walk).
# (`ui/said_as_review.py` does the same, but it is a view, not a registered item: not walked.)
KNOWN_NEEDS_A_SERVER: dict[str, str] = {
    "HeardReviewButton": "ui/heard_review.py current_text: guild is None returns None",
}

SERVER_WORDS = "in a server"


async def press(cls: type[discord.ui.DynamicItem[Any]]) -> list[str]:
    """Build the item from a sample id and press it with no server; what it said to the user."""
    template = cls.__discord_ui_compiled_template__
    custom_id = sample(template)
    match = template.fullmatch(custom_id)
    assert match is not None, f"{cls.__name__}: the sample id does not match its pattern"
    said: list[str] = []

    async def say(*args: Any, **kwargs: Any) -> None:
        said.append(str(args[0]) if args else str(kwargs.get("content", "")))

    it = MagicMock(spec=discord.Interaction)
    it.guild = None
    it.guild_id = None
    it.channel = None
    it.message = MagicMock()
    it.user = SimpleNamespace(id=7, mention="<@7>", display_name="x", name="x")
    it.client = AsyncMock()
    it.response = MagicMock()
    it.response.send_message = say
    it.response.edit_message = say
    it.response.defer = AsyncMock()
    it.response.is_done = lambda: False
    it.response.send_modal = AsyncMock()
    it.followup = MagicMock()
    it.followup.send = say
    it.edit_original_response = say
    item = await cls.from_custom_id(it, discord.ui.Button(custom_id=custom_id), match)
    with contextlib.suppress(Exception):  # (the stand-in client answers little: only what was
        await asyncio.wait_for(item.callback(it), 5)  # said matters)
    return said


class WithNoServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)  # (handlers log their stand-in failures)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_every_registered_item_has_a_sample_id_that_matches_its_pattern(self) -> None:
        items = registered_items()
        self.assertGreater(len(items), 100)
        for cls in items:
            template = cls.__discord_ui_compiled_template__  # type: ignore[attr-defined]
            self.assertIsNotNone(template.fullmatch(sample(template)), cls.__name__)

    async def test_no_registered_button_refuses_for_want_of_a_server(self) -> None:
        refused: list[str] = []
        for cls in sorted(registered_items(), key=lambda c: (c.__module__, c.__name__)):
            if SERVER_WORDS in " ".join(await press(cls)) and cls.__name__ not in SERVER_ONLY:
                refused.append(f"{cls.__module__}.{cls.__name__}")
        self.assertEqual(
            refused,
            [],
            "these answer 'Use this in a server' to a user in a private chat; make them find "
            "the campaign's server from the campaign (see ui/names.py `_campaign_for`), or list "
            "them in SERVER_ONLY with a reason",
        )

    async def test_the_server_only_list_is_still_true_and_still_needed(self) -> None:
        by_name = {c.__name__: c for c in registered_items()}
        for name in SERVER_ONLY:
            self.assertIn(name, by_name, f"{name} is no longer registered: drop it from the list")
            said = " ".join(await press(by_name[name]))
            self.assertIn(SERVER_WORDS, said, f"{name} no longer needs a server: drop it")

    def test_the_two_known_ones_are_still_registered(self) -> None:
        names = {c.__name__ for c in registered_items()}
        for name in KNOWN_NEEDS_A_SERVER:
            self.assertIn(name, names, f"{name} was renamed or removed: update the list")
