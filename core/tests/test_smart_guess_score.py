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
    def test_the_key_is_well_formed_in_the_read_s_own_shape(self) -> None:
        from dmbot.memory import background as bg

        key = json.loads(sg.DEFAULT_KEY.read_text(encoding="utf-8"))
        self.assertEqual(len(key["documents"]), 3)
        kinds = set(bg.CONNECTIONS.values())
        for doc in key["documents"]:
            for s in doc["statements"]:
                self.assertIsInstance(s["world"], bool)
                self.assertTrue("name" in s or s["how"] in kinds, s)  # (a kind the read stores)

    def test_a_hidden_identity_is_sent_as_the_read_sends_it(self) -> None:
        key = json.loads(sg.DEFAULT_KEY.read_text(encoding="utf-8"))
        lines = sg.lines_of(key["documents"][1])
        self.assertTrue(any("(hidden identity the Pale Hand)" in x for x in lines))

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
        self.assertEqual(
            lines[-1],
            "all: agrees on 15 of 21 (71%); spoilers called world knowledge 6; "
            "world knowledge called a spoiler 0",
        )
        self.assertEqual(len(lines), 4)

    async def test_a_failed_call_is_never_scored_as_a_safe_answer(self) -> None:
        class _Down:
            async def complete(self, *_: Any, **__: Any) -> Reply:
                raise RuntimeError("down")

        key = json.loads(sg.DEFAULT_KEY.read_text(encoding="utf-8"))
        lines = await sg.run(key, _Down())
        self.assertEqual(
            lines[-1],
            "all: agrees on 0 of 0 (-); spoilers called world knowledge 0; "
            "world knowledge called a spoiler 0; NOT SORTED (left out) 21",
        )

    def test_no_key_says_so(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            self.assertEqual(sg.main([]), 2)


if __name__ == "__main__":
    unittest.main()
