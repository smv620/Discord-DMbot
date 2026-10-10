"""The AI client: answers, errors in plain words, and the key never shown."""

import json
import unittest
from typing import Any

from dmbot.ai import AIError, AIModels, AIModelTier, AnthropicClient


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def json(self) -> Any:
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    async def text(self) -> str:
        return str(self._body)


class FakeSession:
    closed = False

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.sent: dict[str, Any] = {}

    def post(self, url: str, *, json: Any, headers: Any) -> FakeResponse:
        self.sent = {"url": url, "json": json, "headers": headers}
        return self.response


class Client(unittest.IsolatedAsyncioTestCase):
    def client(self, status: int, body: Any) -> tuple[AnthropicClient, FakeSession]:
        session = FakeSession(FakeResponse(status, body))
        return AnthropicClient("sk-secret", AIModels.same("m"), session=session), session  # type: ignore[arg-type]

    async def test_an_answer(self) -> None:
        body = {
            "content": [{"type": "text", "text": "A | NPC\n"}, {"type": "text", "text": "B"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 120, "output_tokens": 7},
        }
        ai, session = self.client(200, body)
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual((reply.text, reply.cut), ("A | NPC\nB", True))
        self.assertEqual((reply.input_tokens, reply.output_tokens), (120, 7))
        self.assertEqual(session.sent["json"]["system"], "rules")
        self.assertNotIn("sk-secret", repr(ai))

    async def test_odd_usage_counts_as_none(self) -> None:
        ai, _ = self.client(200, {"content": [], "usage": {"input_tokens": "lots"}})
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual((reply.input_tokens, reply.output_tokens), (0, 0))
        ai, _ = self.client(200, {"content": [], "usage": {"input_tokens": True}})  # a bool
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual(reply.input_tokens, 0)
        ai, _ = self.client(200, {"content": [], "usage": None})  # not a crash
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual((reply.input_tokens, reply.output_tokens), (0, 0))

    async def test_errors_in_plain_words(self) -> None:
        for status, body, words in [
            (401, "no", "key"),
            (529, "busy", "busy"),
            (500, "oops", "busy"),
            (400, "bad", "couldn't do that"),
            (200, "not json", "busy"),
            (200, ["a list"], "couldn't do that"),
        ]:
            ai, _ = self.client(status, body)
            with self.assertRaisesRegex(AIError, words):
                await ai.complete("rules", "doc", tier=AIModelTier.FAST)


if __name__ == "__main__":
    unittest.main()
