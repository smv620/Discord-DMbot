"""A hand-over accepted on the website is noted to the DMs privately, with a pointer in the DM Bot
channel that names no one (#3874); without a note sender the old screen post stays."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import handover
from dmbot.dm_screen.site_offers import SiteOffers


def offers(**kw: object) -> SiteOffers:
    return SiteOffers(
        MagicMock(),
        get_guild=lambda _: None,
        guild_ids=lambda: [],
        wait_until_ready=AsyncMock(),
        spawn=lambda coro, _name: coro.close(),
        **kw,  # type: ignore[arg-type]
    )


CAMPAIGN = SimpleNamespace(name="Frostmaiden", dm_screen_channel_id=555, guild_id=1, id="c" * 32)
OFFER = SimpleNamespace(to_name="Bea", from_name="Sam", to_account=2, from_account=1)


class Note(unittest.IsolatedAsyncioTestCase):
    async def test_the_note_sender_gets_the_words_and_a_pointer_without_names(self) -> None:
        note = AsyncMock()
        post = AsyncMock()
        await offers(note=note, post=post)._screen_note(CAMPAIGN, OFFER)  # type: ignore[arg-type]
        post.assert_not_awaited()
        told, text, pointer = note.await_args.args
        self.assertIs(told, CAMPAIGN)
        self.assertIn("Frostmaiden", text)
        self.assertEqual(pointer, handover.HANDOVER_POINTER)
        self.assertNotIn("Frostmaiden", pointer)

    async def test_a_failing_note_never_raises(self) -> None:
        note = AsyncMock(side_effect=RuntimeError("down"))
        await offers(note=note)._screen_note(CAMPAIGN, OFFER)  # type: ignore[arg-type]

    async def test_without_a_note_sender_the_screen_post_is_kept(self) -> None:
        post = AsyncMock()
        await offers(post=post)._screen_note(CAMPAIGN, OFFER)  # type: ignore[arg-type]
        self.assertEqual(post.await_args.args[0], 555)
