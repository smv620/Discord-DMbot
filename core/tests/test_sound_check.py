"""The one-call sound check (#3996): the answer's kind is read from its first bytes, the credits
are the account's own difference, a refusal says nothing of the company's words, and nothing is
run without --yes. A stand-in transport, no network."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from dmbot.devtools import sound_check as sc


class _Transport:
    def __init__(self, status: int, sound: bytes, used: tuple[int, int]) -> None:
        self.status, self.sound, self.counts = status, sound, list(used)
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
        self.calls.append((method, path, body))
        if path.startswith("/v1/user/subscription"):
            return 200, json.dumps({"character_count": self.counts.pop(0)}).encode()
        return self.status, self.sound

    async def close(self) -> None:
        return None


class TheCheck(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, transport: _Transport, model: str = sc.DEFAULT_MODEL) -> list[str]:
        with mock.patch.object(sc, "HttpTransport", lambda key: transport):
            return await sc.check("key", model, 5.0, True)

    async def test_it_asks_with_loop_and_the_model_and_reads_the_credits_difference(self) -> None:
        t = _Transport(200, b"ID3" + b"x" * 40, (1000, 1200))
        lines = await self.run_with(t)
        post = next(c for c in t.calls if c[0] == "POST")
        self.assertEqual(
            post[2],
            {
                "text": sc.PROMPT,
                "duration_seconds": 5.0,
                "loop": True,
                "model_id": sc.DEFAULT_MODEL,
            },
        )
        self.assertEqual(lines[0], f"model {sc.DEFAULT_MODEL}, loop on, 5 s: HTTP 200")
        self.assertEqual(lines[1], "answer: 43 bytes, mp3")
        self.assertIn("credits used by this call: 200", lines[2])

    async def test_a_refusal_says_so_without_the_companys_words(self) -> None:
        lines = await self.run_with(_Transport(422, b"loop is not allowed", (5, 5)))
        self.assertEqual(lines[0], f"model {sc.DEFAULT_MODEL}, loop on, 5 s: HTTP 422")
        self.assertNotIn("loop is not allowed", " ".join(lines))

    async def test_an_unreadable_account_count_is_said_not_guessed(self) -> None:
        class NoCount(_Transport):
            async def __call__(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
                if path.startswith("/v1/user/subscription"):
                    return 403, b""
                return await super().__call__(method, path, body)

        lines = await self.run_with(NoCount(200, b"\xff\xfb" + b"x" * 10, (0, 0)))
        self.assertTrue(lines[-1].startswith("credits: the account's count could not be read"))

    def test_the_kind_of_an_answer_comes_from_its_first_bytes(self) -> None:
        self.assertEqual(sc.kind_of(b"ID3\x04"), "mp3")
        self.assertEqual(sc.kind_of(b"\xff\xfbabc"), "mp3")
        self.assertEqual(sc.kind_of(b"<html>"), "not an mp3")
        self.assertEqual(sc.kind_of(b""), "not an mp3")

    def test_nothing_is_run_without_yes_and_a_missing_key_is_refused(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("sys.argv", ["x", "--yes"]),
        ):
            self.assertEqual(sc.main(), 2)
        with (
            mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "k"}),
            mock.patch("sys.argv", ["x"]),
            mock.patch("asyncio.run") as ran,
        ):
            self.assertEqual(sc.main(), 0)
            ran.assert_not_called()
