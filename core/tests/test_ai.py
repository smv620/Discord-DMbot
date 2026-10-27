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

    async def test_the_system_text_is_marked_for_caching_only_when_asked(self) -> None:
        body = {"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 1}}
        ai, session = self.client(200, body)
        await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual(session.sent["json"]["system"], "rules")  # as before for everyone else
        await ai.complete("rules", "doc", tier=AIModelTier.FAST, cache_system=True)
        self.assertEqual(
            session.sent["json"]["system"],
            [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}],
        )
        await ai.tier(AIModelTier.FAST, cache_system=True).complete("rules", "doc")
        self.assertIsInstance(session.sent["json"]["system"], list)  # a tier client remembers it
        await ai.tier(AIModelTier.FAST).complete("rules", "doc")
        self.assertEqual(session.sent["json"]["system"], "rules")

    async def test_a_schema_is_sent_as_structured_outputs_only_when_asked(self) -> None:
        """#2039: `output_config.format` with `type: json_schema`, no beta header."""
        body = {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 1}}
        ai, session = self.client(200, body)
        await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertNotIn("output_config", session.sent["json"])
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        await ai.complete("rules", "doc", tier=AIModelTier.FAST, schema=schema)
        self.assertEqual(
            session.sent["json"]["output_config"],
            {"format": {"type": "json_schema", "schema": schema}},
        )
        self.assertNotIn("anthropic-beta", session.sent["headers"])
        await ai.tier(AIModelTier.CAREFUL).complete("rules", "doc", schema=schema)  # a tier client
        self.assertEqual(session.sent["json"]["output_config"]["format"]["schema"], schema)

    async def test_images_go_in_before_the_text_as_base64_and_only_when_given(self) -> None:
        """#2450: an image block for each image, then the text; nothing changes without one."""
        import base64

        from dmbot.ai import Image

        body = {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 1}}
        ai, session = self.client(200, body)
        await ai.complete("rules", "doc", tier=AIModelTier.FAST)
        self.assertEqual(session.sent["json"]["messages"], [{"role": "user", "content": "doc"}])
        picture = Image("image/png", b"\x89PNG\r\n\x1a\nbytes")
        await ai.tier(AIModelTier.FAST).complete("rules", "look", images=[picture])
        (message,) = session.sent["json"]["messages"]
        self.assertEqual(
            message["content"],
            [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(picture.data).decode(),
                    },
                },
                {"type": "text", "text": "look"},
            ],
        )
        self.assertNotIn("PNG", repr(picture))  # the bytes are never in a log line or a repr

    async def test_a_refusal_is_flagged_and_a_schema_the_service_rejects_fails_in_plain_words(
        self,
    ) -> None:
        body = {"content": [], "stop_reason": "refusal", "usage": {"input_tokens": 1}}
        ai, _ = self.client(200, body)
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST, schema={"type": "object"})
        self.assertTrue(reply.refused)
        ai, _ = self.client(400, "output_config: schema not supported")
        with self.assertRaises(AIError):  # no silent fallback to unstructured text (#2039)
            await ai.complete("rules", "doc", tier=AIModelTier.FAST, schema={"type": "object"})

    async def test_cache_writes_and_reads_are_counted_for_the_spend_watch(self) -> None:
        body = {
            "content": [{"type": "text", "text": "x"}],
            "usage": {
                "input_tokens": 200,
                "output_tokens": 10,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 500,
            },
        }
        seen: list[tuple[int, int]] = []

        class Watch:
            def record(self, tokens_in: int, tokens_out: int, feature: object = None) -> None:
                seen.append((tokens_in, tokens_out))

            def out_of_funds(self) -> None:
                return None

        session = FakeSession(FakeResponse(200, body))
        ai = AnthropicClient(
            "sk-secret",
            AIModels.same("m"),
            session=session,  # type: ignore[arg-type]
            watch=Watch(),
        )
        reply = await ai.complete("rules", "doc", tier=AIModelTier.FAST, cache_system=True)
        self.assertEqual((reply.cache_write_tokens, reply.cache_read_tokens), (1000, 500))
        self.assertEqual(seen, [(200 + 1000 + 50, 10)])  # a write in full, a read at a tenth

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
