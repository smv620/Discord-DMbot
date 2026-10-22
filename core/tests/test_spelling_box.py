"""The Fix spelling box (#2332): a pronunciation typed where the spelling goes gets a plain
line, and nothing is saved; an ordinary name with quotes still saves."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.ui import name_card


async def submit(value: str, *, spoken: bool = False) -> tuple[AsyncMock, AsyncMock]:
    """Submit the form with `value`; returns (what was told, the rename)."""
    form = name_card.FixSpellingForm("c" * 32, "e" * 32, "Brannoch", spoken=spoken)
    form.name._value = value
    if spoken:
        form.said_as._value = ""
    memory = MagicMock()
    memory.rename_entity = AsyncMock(
        return_value=SimpleNamespace(value=SimpleNamespace(name="Brannock"), batch=1)
    )
    memory.set_said_as = AsyncMock()
    told = AsyncMock()
    campaign = MagicMock()
    with (
        patch.object(name_card, "_answer_first", AsyncMock()),
        patch.object(name_card, "_current", AsyncMock(return_value=(campaign, "Brannoch"))),
        patch.object(name_card, "_memory", lambda _it: memory),
        patch.object(name_card, "_tell", told),
        patch.object(name_card, "known_as", AsyncMock(return_value=None)),
        patch.object(name_card, "_respell_past", AsyncMock(return_value=MagicMock(text=""))),
        patch.object(name_card, "show_card", AsyncMock()),
        patch.object(name_card, "changed", MagicMock()),
    ):
        await form.on_submit(MagicMock())
    return told, memory.rename_entity


class SpellingBox(unittest.IsolatedAsyncioTestCase):
    async def test_a_pronunciation_gets_a_plain_line_and_saves_nothing(self) -> None:
        told, rename = await submit('Brannock pronounced "BRAN-uck"')
        told.assert_awaited_once()
        assert told.await_args is not None
        self.assertEqual(told.await_args.args[1], name_card.SPELLING_ONLY)
        rename.assert_not_awaited()

    async def test_the_line_points_to_the_box_only_when_the_form_has_one(self) -> None:
        told, _ = await submit("Brannock sounds like BRAN-uck", spoken=True)
        assert told.await_args is not None
        self.assertEqual(told.await_args.args[1], name_card.SPELLING_AND_SOUND)
        self.assertIn("How to say it box", name_card.SPELLING_AND_SOUND)
        self.assertNotIn("box", name_card.SPELLING_ONLY)

    async def test_ordinary_names_with_quotes_and_cue_words_still_save(self) -> None:
        for name in (
            "O'Brien", '"The Hound"', 'Brannock "the Bold"', "Pronouncer Vex", "Sounds Like",
        ):  # fmt: skip
            told, rename = await submit(name)
            rename.assert_awaited_once()
            for call in told.await_args_list:
                self.assertNotIn("how to say the name", str(call))


if __name__ == "__main__":
    unittest.main()
