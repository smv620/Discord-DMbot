"""Generous output limits (#2596): no background call is sent below the floor, a table answer keeps
its short cap (times four), the wait matches, and a long answer is logged against its estimate."""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

from dmbot import ai
from dmbot.ai import AIModels, AIModelTier, AnthropicClient, Feature
from tests.test_ai import FakeResponse, FakeSession

BODY = {
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 7},
}


def client(
    body: Any = BODY, model: str = "claude-sonnet-5-5"
) -> tuple[AnthropicClient, FakeSession]:
    session = FakeSession(FakeResponse(200, body))
    return AnthropicClient("sk-secret", AIModels.same(model), session=session), session  # type: ignore[arg-type]


class TheRoom(unittest.IsolatedAsyncioTestCase):
    async def test_a_background_call_is_never_sent_below_the_floor(self) -> None:
        for tier in AIModelTier:
            for estimate in (200, 800, 1500, 4000):
                with self.subTest(tier=tier.value, estimate=estimate):
                    ai_client, session = client()
                    await ai_client.complete("s", "t", tier=tier, max_tokens=estimate)
                    self.assertGreaterEqual(session.sent["json"]["max_tokens"], ai.OUTPUT_FLOOR)

    async def test_a_bigger_number_than_the_floor_is_kept(self) -> None:
        ai_client, session = client()
        await ai_client.complete("s", "t", tier=AIModelTier.CAREFUL, max_tokens=30_000)
        self.assertEqual(session.sent["json"]["max_tokens"], 30_000)

    async def test_a_table_answer_keeps_its_short_cap_times_four(self) -> None:
        for feature in ("sidebar", "sidebar_retry", "fightbot", "audio_check", "time_suggest"):
            with self.subTest(feature=feature):
                ai_client, session = client()
                await ai_client.complete(
                    "s", "t", tier=AIModelTier.FAST, max_tokens=300, feature=feature
                )
                self.assertEqual(session.sent["json"]["max_tokens"], 1200)

    async def test_every_live_feature_is_a_real_feature(self) -> None:
        self.assertTrue({f.value for f in Feature} >= ai.LIVE_FEATURES)

    async def test_the_floor_stays_within_what_the_model_can_write(self) -> None:
        self.assertEqual(ai.answer_room(200, "an-unknown-model"), ai.DEFAULT_MAX_OUTPUT)
        self.assertLessEqual(ai.OUTPUT_FLOOR, ai.max_output("claude-opus-5-5"))
        # never below what the caller asked for, whatever the model's limit
        self.assertEqual(ai.answer_room(40_000, "an-unknown-model"), 40_000)

    async def test_the_waits_match_a_long_answer_and_a_table_answer_stays_quick(self) -> None:
        self.assertGreaterEqual(ai.TIER_TIMEOUT_S[AIModelTier.CREATIVE], 480)
        self.assertGreaterEqual(ai.TIER_TIMEOUT_S[AIModelTier.CAREFUL], 480)
        self.assertGreaterEqual(ai.TIER_TIMEOUT_S[AIModelTier.FAST], 180)
        self.assertEqual(ai.LIVE_TIMEOUT_S, ai.REQUEST_TIMEOUT_S)

    async def test_an_answer_longer_than_the_estimate_is_logged_with_counts_only(self) -> None:
        body = {**BODY, "usage": {"input_tokens": 10, "output_tokens": 900}}
        ai_client, _ = client(body)
        with self.assertLogs("dmbot.ai", level="INFO") as logged:
            await ai_client.complete(
                "secret system", "secret text", tier=AIModelTier.FAST, max_tokens=200,
                feature="topic",
            )  # fmt: skip
        line = next(x for x in logged.output if "longer than the caller's estimate" in x)
        self.assertIn("feature=topic estimate=200 wrote=900", line)
        self.assertNotIn("secret", line)

    async def test_a_normal_answer_logs_no_such_line(self) -> None:
        ai_client, _ = client()
        with self.assertLogs("dmbot.ai", level="INFO") as logged:
            await ai_client.complete("s", "t", tier=AIModelTier.FAST, max_tokens=200)
        self.assertFalse(any("longer than the caller's estimate" in x for x in logged.output))


class TheHold(unittest.TestCase):
    def test_a_check_that_failed_on_our_side_waits_minutes_not_hours(self) -> None:
        from dmbot.recaps import critics as cr

        self.assertEqual([cr.wait_for(n, soon=True) for n in range(3)], [120, 300, 900])
        # then the usual back-off, so a long outage does not burn the day's calls
        self.assertEqual(
            [cr.wait_for(n, soon=True) for n in range(3, 8)], [900, 3600, 14400, 43200, 43200]
        )
        # the day's tally, a rolling day, still uses the long schedule
        self.assertEqual(cr.wait_for(2), 14400)


class TheListRead(unittest.IsolatedAsyncioTestCase):
    async def test_the_first_names_read_starts_at_the_floor_so_twice_is_really_more(self) -> None:
        from contextlib import nullcontext

        from dmbot.ai import Reply
        from dmbot.memory import lore_read as lr

        asked: list[int] = []

        class Ai:
            model = "claude-sonnet-5-5"

            async def complete(
                self, system: str, text: str, *, max_tokens: int, **kw: Any
            ) -> Reply:
                asked.append(max_tokens)
                return Reply('NAMES\n- A | person | quote: "a"\n', len(asked) < 2)

        await lr.read_list(Ai(), "a short text", {}, nullcontext(), lr.Stats())
        self.assertGreaterEqual(asked[0], ai.OUTPUT_FLOOR)
        self.assertGreater(asked[1], asked[0])


class GeminiRoom(unittest.TestCase):
    def test_a_gemini_model_gets_its_own_lower_ceiling(self) -> None:
        self.assertEqual(ai.max_output("gemini-2.5-pro"), ai.GEMINI_MAX_OUTPUT)
        self.assertEqual(ai.answer_room(200, "gemini-2.5-pro"), ai.GEMINI_MAX_OUTPUT)
        self.assertLess(ai.GEMINI_MAX_OUTPUT, ai.OUTPUT_FLOOR)

    def test_the_ceiling_can_be_set_and_a_bad_value_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_MAX_OUTPUT": "4000"}):
            self.assertEqual(ai.max_output("gemini-2.5-flash"), 4000)
        with mock.patch.dict(os.environ, {"GEMINI_MAX_OUTPUT": "lots"}):
            self.assertEqual(ai.max_output("gemini-2.5-flash"), ai.GEMINI_MAX_OUTPUT)
