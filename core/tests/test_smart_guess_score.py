"""The smart-guess measure (#2959): scoring against the invented key, and a run with the AI
canned. No network."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.ai import Reply
from dmbot.devtools import smart_guess_score as sg


class _Canned:
    """Calls every statement world knowledge except the ones whose line holds `secret`."""

    async def complete(self, system: str, user: str, **_: Any) -> Reply:
        numbered = [x for x in user.splitlines() if x[:1].isdigit()]
        world = [int(x.split(".")[0]) for x in numbered if "secret" not in x.casefold()]
        return Reply(json.dumps({"world": world}), False, 1, 1)


class TheMeasure(unittest.IsolatedAsyncioTestCase):
    def test_the_key_is_invented_and_well_formed(self) -> None:
        key = json.loads(sg.DEFAULT_KEY.read_text(encoding="utf-8"))
        self.assertEqual(len(key["documents"]), 3)
        for doc in key["documents"]:
            for s in doc["statements"]:
                self.assertIn(s["kind"], ("name", "connection"))
                self.assertIsInstance(s["world"], bool)

    def test_a_spoiler_called_world_knowledge_is_counted_apart(self) -> None:
        doc = {"statements": [{"world": True}, {"world": False}, {"world": False}, {"world": True}]}
        got = sg.score(doc, {0, 1}, True)
        self.assertEqual(
            (got.right_world, got.right_spoiler, got.leaked, got.held_back), (1, 1, 1, 1)
        )
        self.assertIn("spoilers called world knowledge 1", got.line("x"))

    async def test_a_run_gives_one_line_per_document_and_the_total(self) -> None:
        key = json.loads(sg.DEFAULT_KEY.read_text(encoding="utf-8"))
        lines = await sg.run(key, _Canned())
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[-1].startswith("all: agrees on "))


if __name__ == "__main__":
    unittest.main()
