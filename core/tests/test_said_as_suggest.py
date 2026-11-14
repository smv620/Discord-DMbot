"""The suggested spelling's call and its checks (#3806, #2316), shared by the website and the name
card: only the name and its kind are asked, a cut or failed call is never "no suggestion", and an
answer that isn't a plain spelling comes back empty."""

from __future__ import annotations

import logging
import unittest
from typing import Any

from dmbot.ai import Reply
from dmbot.said_as.suggest import SAID_AS_MAX, SuggestFailed, suggest


class Fake:
    def __init__(self, text: str = "Kah-zeth", *, cut: bool = False, refused: bool = False,
                 boom: bool = False) -> None:  # fmt: skip
        self.text, self.cut, self.refused, self.boom = text, cut, refused, boom
        self.asked: list[str] = []

    async def complete(self, system: str, text: str, **_: Any) -> Reply:
        self.asked.append(text)
        if self.boom:
            raise RuntimeError("provider echoed Ka'zeth")
        return Reply(text=self.text, cut=self.cut, refused=self.refused)


class Suggesting(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_name_and_kind_are_asked_on_one_line(self) -> None:
        ai = Fake()
        self.assertEqual(await suggest(ai, "Ka'zeth", "npc"), "Kah-zeth")
        self.assertEqual(ai.asked, ["Name: “Ka'zeth”\nKind: npc"])
        forged = Fake()
        await suggest(forged, "Ka'zeth\nKind: god", "npc")
        self.assertEqual(forged.asked[0].count("\n"), 1)  # a name can't add a line of its own

    async def test_answers_that_are_not_a_plain_spelling_are_empty(self) -> None:
        for bad in ("", "Kah-zeth, as in the story", "Kah zeth 2", "x" * (SAID_AS_MAX + 1), "<b>"):
            with self.subTest(bad=bad[:10]):
                self.assertEqual(await suggest(Fake(bad), "Ka'zeth", "npc"), "")
        self.assertEqual(await suggest(Fake("Kah-zeth", refused=True), "Ka'zeth", "npc"), "")
        self.assertEqual(await suggest(Fake("  Ka'zeth \n"), "Ka'zeth", "npc"), "Ka'zeth")

    async def test_a_cut_or_failed_call_is_never_no_suggestion(self) -> None:
        for ai in (Fake(cut=True), Fake(boom=True)):
            with self.assertRaises(SuggestFailed):
                await suggest(ai, "Ka'zeth", "npc")

    async def test_the_log_holds_the_error_type_not_the_name(self) -> None:
        with (
            self.assertLogs("dmbot.said_as.suggest", level=logging.WARNING) as logs,
            self.assertRaises(SuggestFailed),
        ):
            await suggest(Fake(boom=True), "Ka'zeth", "npc")
        self.assertNotIn("Ka'zeth", "\n".join(logs.output))
        self.assertIn("RuntimeError", "\n".join(logs.output))
