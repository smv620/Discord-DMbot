"""The Fix spelling box (#2332): a pronunciation typed where the spelling goes gets a plain
line, and nothing is saved."""

from __future__ import annotations

import unittest

from dmbot.ui import name_card


class SpellingBox(unittest.IsolatedAsyncioTestCase):
    async def test_a_pronunciation_in_the_spelling_box_gets_a_plain_line_and_saves_nothing(
        self,
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        form = name_card.FixSpellingForm("c" * 32, "e" * 32, "Brannoch")
        form.name._value = 'Brannock pronounced "BRAN-uck"'
        memory = MagicMock()
        memory.rename_entity = AsyncMock()
        told = AsyncMock()
        with (
            patch.object(name_card, "_answer_first", AsyncMock()),
            patch.object(name_card, "_current", AsyncMock(return_value=(MagicMock(), "Brannoch"))),
            patch.object(name_card, "_memory", lambda _it: memory),
            patch.object(name_card, "_tell", told),
        ):
            await form.on_submit(MagicMock())
        told.assert_awaited_once()
        self.assertEqual(told.await_args.args[1], name_card.SPELLING_NOT_SOUND)
        memory.rename_entity.assert_not_awaited()
