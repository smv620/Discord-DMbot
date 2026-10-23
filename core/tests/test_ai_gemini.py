"""Gemini as the recap writer (#2575): the creative tier only, behind the same client; its answers,
token counts, errors and fallbacks mapped to the ones Anthropic's give, and off unless set."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.ai import (
    AIError,
    AIModels,
    AIModelTier,
    AIOutOfFunds,
    AnthropicClient,
    GeminiClient,
    is_gemini_out_of_funds,
)
from dmbot.config import ConfigError, parse_creative_provider
from tests.test_ai import FakeResponse

ANSWER = {
    "candidates": [
        {
            "content": {"parts": [{"text": "The "}, {"text": "story.", "thought": False}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 900,
        "candidatesTokenCount": 300,
        "thoughtsTokenCount": 50,
    },
}


class Session:
    """Answers each URL with its own canned response; records every request."""

    closed = False

    def __init__(self, *answers: tuple[str, FakeResponse]) -> None:
        self.answers = list(answers)
        self.sent: list[dict[str, Any]] = []

    def post(self, url: str, *, json: Any, headers: Any) -> FakeResponse:
        self.sent.append({"url": url, "json": json, "headers": headers})
        for needle, response in self.answers:
            if needle in url:
                return response
        raise AssertionError(f"no answer for {url}")


class Watch:
    def __init__(self) -> None:
        self.recorded: list[tuple[int, int, str | None]] = []
        self.funds = 0

    def record(self, input_tokens: int, output_tokens: int, feature: str | None = None) -> None:
        self.recorded.append((input_tokens, output_tokens, feature))

    def out_of_funds(self) -> None:
        self.funds += 1


MODELS = AIModels(
    fast="claude-fast", careful="claude-careful", deep="claude-deep",
    creative="gemini-writer", formatting="claude-format",
)  # fmt: skip


CLAUDE_OK = FakeResponse(200, {"content": [{"type": "text", "text": "c"}]})


def rig(gemini_response: FakeResponse, claude_response: FakeResponse | None = None) -> Any:
    watch = Watch()
    gemini_session = Session(("generateContent", gemini_response))
    claude_session = Session(
        (
            "anthropic",
            claude_response or FakeResponse(200, {"content": [{"type": "text", "text": "c"}]}),
        )
    )
    gemini = GeminiClient("g-secret-key", session=gemini_session, watch=watch)  # type: ignore[arg-type]
    client = AnthropicClient(
        "a-secret-key",
        MODELS,
        session=claude_session,
        watch=watch,
        gemini=gemini,  # type: ignore[arg-type]
    )
    return client, gemini_session, claude_session, watch


class TheGeminiClient(unittest.IsolatedAsyncioTestCase):
    async def test_an_answer_with_its_token_counts_thinking_included(self) -> None:
        client, gem, _, watch = rig(FakeResponse(200, ANSWER))
        reply = await client.complete("sys", "hi", tier=AIModelTier.CREATIVE, feature="recap_write")
        self.assertEqual((reply.text, reply.cut, reply.refused), ("The story.", False, False))
        self.assertEqual((reply.input_tokens, reply.output_tokens), (900, 350))
        self.assertEqual(reply.model, "gemini-writer")
        self.assertEqual(watch.recorded, [(900, 350, "recap_write")])
        sent = gem.sent[0]
        self.assertTrue(sent["url"].endswith("/models/gemini-writer:generateContent"))
        self.assertEqual(sent["headers"]["x-goog-api-key"], "g-secret-key")
        self.assertEqual(sent["json"]["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(sent["json"]["contents"][0]["parts"], [{"text": "hi"}])
        self.assertEqual(sent["json"]["generationConfig"], {"maxOutputTokens": 8000})

    async def test_a_schema_asks_for_json(self) -> None:
        client, gem, _, _ = rig(FakeResponse(200, ANSWER))
        await client.complete("s", "t", tier=AIModelTier.CREATIVE, schema={"type": "object"})
        config = gem.sent[0]["json"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["responseJsonSchema"], {"type": "object"})

    async def test_cut_off_and_blocked_answers_are_marked(self) -> None:
        cut = {**ANSWER, "candidates": [{**ANSWER["candidates"][0], "finishReason": "MAX_TOKENS"}]}  # type: ignore[index]
        client, *_ = rig(FakeResponse(200, cut))
        self.assertTrue((await client.complete("s", "t", tier=AIModelTier.CREATIVE)).cut)
        blocked = {"candidates": [{"finishReason": "SAFETY"}], "usageMetadata": {}}
        client, *_ = rig(FakeResponse(200, blocked))
        reply = await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertTrue(reply.refused)

    async def test_the_key_is_never_in_the_repr_or_an_error(self) -> None:
        client, *_ = rig(FakeResponse(400, json.dumps({"error": {"message": "API key not valid"}})))
        self.assertNotIn("secret", repr(client) + repr(client._gemini))
        with self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertNotIn("secret", str(caught.exception))

    async def test_only_the_creative_tier_goes_to_gemini(self) -> None:
        client, gem, claude, _ = rig(FakeResponse(200, ANSWER))
        for tier in (
            AIModelTier.FAST,
            AIModelTier.CAREFUL,
            AIModelTier.DEEP,
            AIModelTier.FORMATTING,
        ):
            await client.complete("s", "t", tier=tier)
        self.assertEqual(len(gem.sent), 0)
        self.assertEqual(len(claude.sent), 4)
        await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual((len(gem.sent), len(claude.sent)), (1, 4))

    async def test_with_gemini_off_no_gemini_call_is_ever_made(self) -> None:
        claude = Session(
            ("anthropic", FakeResponse(200, {"content": [{"type": "text", "text": "c"}]}))
        )
        client = AnthropicClient("a", AIModels.same("claude-x"), session=claude)  # type: ignore[arg-type]
        reply = await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual(reply.text, "c")
        self.assertIn("anthropic", claude.sent[0]["url"])


class TheErrors(unittest.IsolatedAsyncioTestCase):
    async def test_money_runs_out_and_nothing_falls_back(self) -> None:
        message = "Your prepayment credits are depleted."
        body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": message}})
        client, _, claude, watch = rig(FakeResponse(429, body))
        with self.assertRaises(AIOutOfFunds):
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual((watch.funds, len(claude.sent)), (1, 0))

    async def test_a_plain_rate_limit_is_busy_not_money(self) -> None:
        body = json.dumps(
            {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Too many requests."}}
        )
        self.assertFalse(is_gemini_out_of_funds(429, body))
        client, _, claude, _ = rig(FakeResponse(429, body))
        with self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertNotIsInstance(caught.exception, AIOutOfFunds)
        self.assertEqual(len(claude.sent), 0)

    async def test_googles_ordinary_quota_message_is_busy_not_money(self) -> None:
        message = "You exceeded your current quota, please check your plan and billing details."
        body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "message": message}})
        self.assertFalse(is_gemini_out_of_funds(429, body))
        client, _, _, watch = rig(FakeResponse(429, body))
        with self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertNotIsInstance(caught.exception, AIOutOfFunds)
        self.assertEqual(watch.funds, 0)

    async def test_a_spend_cap_or_billing_off_is_money(self) -> None:
        for message in ("Your spend cap was reached.", "Billing is not enabled for this project."):
            self.assertTrue(
                is_gemini_out_of_funds(429, json.dumps({"error": {"message": message}})),
                message,
            )

    async def test_with_no_fallback_a_missing_model_is_an_error_not_another_writer(self) -> None:
        missing = FakeResponse(404, json.dumps({"error": {"status": "NOT_FOUND"}}))
        gem = Session(("generateContent", missing))
        claude = Session(("anthropic", CLAUDE_OK))
        client = AnthropicClient(
            "a",
            MODELS,
            session=claude,
            fallback=False,  # type: ignore[arg-type]
            gemini=GeminiClient("g", session=gem),  # type: ignore[arg-type]
        )
        with self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual(len(claude.sent), 0)

    async def test_an_empty_cut_off_answer_is_marked_cut(self) -> None:
        thinking = {
            "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {"promptTokenCount": 10, "thoughtsTokenCount": 8000},
        }
        client, *_ = rig(FakeResponse(200, thinking))
        reply = await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual((reply.text, reply.cut, reply.output_tokens), ("", True, 8000))

    async def test_a_bad_key_is_told_plainly(self) -> None:
        error = {"message": "API key not valid.", "details": [{"reason": "API_KEY_INVALID"}]}
        body = json.dumps({"error": error})
        client, *_ = rig(FakeResponse(400, body))
        with self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertIn("key", str(caught.exception))

    async def test_a_model_that_is_not_there_falls_to_the_next_tier_once_logged(self) -> None:
        client, gem, claude, _ = rig(
            FakeResponse(404, json.dumps({"error": {"status": "NOT_FOUND"}}))
        )
        with self.assertLogs("dmbot.ai", level="WARNING") as logged:
            reply = await client.complete("s", "t", tier=AIModelTier.CREATIVE)
        self.assertEqual(reply.text, "c")  # Anthropic's careful model answered
        self.assertEqual((len(gem.sent), len(claude.sent)), (1, 1))
        self.assertIn("gemini-writer", logged.output[0])
        self.assertNotIn("secret", logged.output[0])

    async def test_a_server_error_is_busy(self) -> None:
        client, *_ = rig(FakeResponse(503, "overloaded"))
        with self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.CREATIVE)

    async def test_a_log_line_has_counts_and_no_content(self) -> None:
        client, *_ = rig(FakeResponse(200, ANSWER))
        with self.assertLogs("dmbot.ai", level="INFO") as logged:
            await client.complete("secret system", "secret text", tier=AIModelTier.CREATIVE)
        line = logged.output[0]
        self.assertIn("in=900 out=350", line)
        self.assertNotIn("secret", line)


class TheSettings(unittest.TestCase):
    def get(self, **env: str) -> Any:
        return lambda name: env.get(name, "")

    def test_it_is_off_by_default(self) -> None:
        provider, models, notice = parse_creative_provider(self.get(), MODELS)
        self.assertEqual((provider, models, notice), ("anthropic", MODELS, ""))

    def test_gemini_needs_a_gemini_model_name(self) -> None:
        with self.assertRaises(ConfigError):
            parse_creative_provider(
                self.get(AI_PROVIDER_CREATIVE="gemini", GEMINI_API_KEY="k"), MODELS
            )
        with self.assertRaises(ConfigError):
            parse_creative_provider(
                self.get(
                    AI_PROVIDER_CREATIVE="gemini",
                    AI_MODEL_CREATIVE="claude-opus-5-5",
                    GEMINI_API_KEY="k",
                ),
                MODELS,
            )

    def test_an_unknown_provider_stops_start_up(self) -> None:
        with self.assertRaises(ConfigError):
            parse_creative_provider(self.get(AI_PROVIDER_CREATIVE="other"), MODELS)

    def test_no_key_means_the_writer_stays_on_anthropic_with_a_plain_line(self) -> None:
        env = self.get(AI_PROVIDER_CREATIVE="gemini", AI_MODEL_CREATIVE="gemini-writer")
        provider, models, notice = parse_creative_provider(env, MODELS)
        self.assertEqual(provider, "anthropic")
        self.assertTrue(models.creative.startswith("claude-"))
        self.assertIn("GEMINI_API_KEY", notice)

    def test_provider_gemini_with_no_key_and_no_model_does_not_stop_start_up(self) -> None:
        provider, models, notice = parse_creative_provider(
            self.get(AI_PROVIDER_CREATIVE="gemini"), MODELS
        )
        self.assertEqual(provider, "anthropic")
        self.assertTrue(models.creative.startswith("claude-"))
        self.assertIn("GEMINI_API_KEY", notice)

    def test_with_a_key_gemini_writes(self) -> None:
        env = self.get(
            AI_PROVIDER_CREATIVE="gemini", AI_MODEL_CREATIVE="gemini-writer", GEMINI_API_KEY="k"
        )
        provider, models, notice = parse_creative_provider(env, MODELS)
        self.assertEqual((provider, models.creative, notice), ("gemini", "gemini-writer", ""))

    def test_the_old_claude_check_does_not_reject_a_gemini_name(self) -> None:
        from dmbot.config import parse_ai_models

        env = self.get(AI_PROVIDER_CREATIVE="gemini", AI_MODEL_CREATIVE="gemini-writer")
        models, _ = parse_ai_models(env)
        self.assertEqual(models.creative, "gemini-writer")
