"""AI model tiers (#1006): the settings, the tier of each job, the fallback to the next tier
down, the out-of-funds error never falling back, and the one usage line per call."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from dmbot import ai as ai_module
from dmbot.ai import (
    DEFAULT_MODELS,
    FEATURE_TIERS,
    AIError,
    AIModels,
    AIModelTier,
    AIOutOfFunds,
    AnthropicClient,
    TierClient,
)
from dmbot.config import (
    BOTH_MODEL_NOTICE,
    OLD_MODEL_NOTICE,
    ConfigError,
    load_settings,
    parse_ai_models,
)

MODELS = AIModels(fast="claude-fast-1", careful="claude-careful-1", deep="claude-deep-1")
BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgres://x"}
OK = {
    "content": [{"type": "text", "text": "the answer"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 120, "output_tokens": 7, "cache_read_input_tokens": 90},
}
FUNDS = json.dumps(
    {"error": {"type": "invalid_request_error", "message": "Your credit balance is too low"}}
)


def env(**values: str) -> Any:
    return lambda name: values.get(name, "")


class Settings(unittest.TestCase):
    def test_each_tier_has_its_default(self) -> None:
        models, notice = parse_ai_models(env())
        self.assertEqual(models, DEFAULT_MODELS)
        self.assertEqual(notice, "")
        self.assertIn("haiku", models.fast)
        self.assertIn("sonnet", models.careful)
        self.assertIn("opus", models.deep)

    def test_each_tier_can_be_set(self) -> None:
        models, notice = parse_ai_models(
            env(
                AI_MODEL_FAST="claude-a-1",
                AI_MODEL_CAREFUL="claude-b-2",
                AI_MODEL_DEEP="claude-c-3",
            )
        )
        self.assertEqual(models, AIModels("claude-a-1", "claude-b-2", "claude-c-3"))
        self.assertEqual(notice, "")

    def test_the_old_name_is_read_as_fast_with_a_line_saying_so(self) -> None:
        models, notice = parse_ai_models(env(AI_MODEL="claude-old-1"))
        self.assertEqual(models.fast, "claude-old-1")
        self.assertEqual(
            (models.careful, models.deep), (DEFAULT_MODELS.careful, DEFAULT_MODELS.deep)
        )
        self.assertEqual(notice, OLD_MODEL_NOTICE)
        self.assertIn("AI_MODEL_FAST", notice)

    def test_the_new_name_wins_over_the_old_one(self) -> None:
        models, notice = parse_ai_models(env(AI_MODEL="claude-old-1", AI_MODEL_FAST="claude-new-1"))
        self.assertEqual(models.fast, "claude-new-1")
        self.assertEqual(notice, BOTH_MODEL_NOTICE)

    def test_a_malformed_value_stops_start_up_naming_the_setting(self) -> None:
        for name in ("AI_MODEL_FAST", "AI_MODEL_CAREFUL", "AI_MODEL_DEEP", "AI_MODEL"):
            for bad in ("gpt-4", "claude-", "Claude Haiku", "claude-x y", "claude-é1", "x" * 200):
                with self.subTest(name=name, bad=bad), self.assertRaises(ConfigError) as caught:
                    parse_ai_models(env(**{name: bad}))
                self.assertIn(name, str(caught.exception))
                if len(bad) > 8:
                    self.assertNotIn(bad, str(caught.exception))  # (never echoes the value)

    def test_load_settings_carries_them_and_the_notice(self) -> None:
        settings = load_settings(
            {**BASE, "AI_MODEL": "claude-old-1", "AI_MODEL_DEEP": "claude-d-9"}
        )
        self.assertEqual(
            settings.ai_models, AIModels("claude-old-1", DEFAULT_MODELS.careful, "claude-d-9")
        )
        self.assertEqual(settings.ai_model_notice, OLD_MODEL_NOTICE)
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "AI_MODEL_CAREFUL": "nonsense"})
        self.assertEqual(load_settings(BASE).ai_models, DEFAULT_MODELS)

    def test_the_example_file_has_all_three(self) -> None:
        text = (Path(__file__).parents[2] / ".env.example").read_text()
        for name in ("AI_MODEL_FAST", "AI_MODEL_CAREFUL", "AI_MODEL_DEEP"):
            self.assertRegex(text, rf"(?m)^{name}=claude-")
        self.assertNotRegex(text, r"(?m)^AI_MODEL=")  # the old name is not suggested


class Tiers(unittest.TestCase):
    def test_every_job_names_a_tier(self) -> None:
        self.assertEqual(
            FEATURE_TIERS,
            {
                "names": AIModelTier.FAST,
                "topic": AIModelTier.FAST,
                "audio_check": AIModelTier.FAST,
                "sidebar": AIModelTier.FAST,
                "rules": AIModelTier.FAST,
                "cleaner": AIModelTier.FAST,
                "house_rules": AIModelTier.CAREFUL,
            },
        )
        self.assertNotIn(AIModelTier.DEEP, FEATURE_TIERS.values())  # PlotBot, later

    def test_the_bot_gives_each_job_the_client_for_its_tier(self) -> None:
        from unittest.mock import MagicMock

        from dmbot.bot import DMBot
        from dmbot.config import Settings as BotSettings

        settings = BotSettings(discord_token="t", ears_secret="s", ai_key="k", ai_models=MODELS)
        bot = DMBot(settings, SimpleNamespace(), MagicMock(), MagicMock())  # type: ignore[arg-type]
        for client, job in (
            (bot.ai, "names"),
            (bot.topic_ai, "topic"),
            (bot.audio_ai, "audio_check"),
        ):
            assert isinstance(client, TierClient)
            self.assertEqual(client.tier, FEATURE_TIERS[job], job)
            self.assertEqual(client.model, MODELS.of(FEATURE_TIERS[job]), job)
        sidebar = bot.sidebar_answers
        assert sidebar is not None
        self.assertEqual(sidebar._ai.model, MODELS.fast)  # (the sidebar stays on FAST)

    def test_without_a_key_there_are_no_clients(self) -> None:
        from unittest.mock import MagicMock

        from dmbot.bot import DMBot
        from dmbot.config import Settings as BotSettings

        settings = BotSettings(discord_token="t", ears_secret="s")
        bot = DMBot(settings, SimpleNamespace(), MagicMock(), MagicMock())  # type: ignore[arg-type]
        self.assertEqual((bot.ai, bot.topic_ai, bot.audio_ai, bot.sidebar_answers), (None,) * 4)


class Reply:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> Reply:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def json(self) -> Any:
        return self._body

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class Service:
    """Answers each model as told: `{model: (status, body)}`; anything else is fine."""

    closed = False

    def __init__(self, answers: dict[str, tuple[int, Any]]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def post(self, url: str, *, json: Any, headers: Any) -> Reply:
        self.asked.append(json["model"])
        status, body = self.answers.get(json["model"], (200, OK))
        return Reply(status, body)


class Watch:
    def __init__(self) -> None:
        self.funds = 0
        self.used: list[tuple[int, int]] = []

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.used.append((input_tokens, output_tokens))

    def out_of_funds(self) -> None:
        self.funds += 1


class Fallback(unittest.IsolatedAsyncioTestCase):
    def make(
        self, answers: dict[str, tuple[int, Any]], watch: Watch | None = None
    ) -> tuple[AnthropicClient, Service]:
        service = Service(answers)
        client = AnthropicClient("sk-secret", MODELS, session=service, watch=watch)  # type: ignore[arg-type]
        return client, service

    async def test_a_tier_that_works_answers_alone(self) -> None:
        client, service = self.make({})
        for tier in AIModelTier:
            reply = await client.complete("s", "t", tier=tier)
            self.assertEqual(reply.model, MODELS.of(tier))
        self.assertEqual(service.asked, [MODELS.fast, MODELS.careful, MODELS.deep][::1])

    async def test_a_refused_model_falls_back_one_tier_down(self) -> None:
        for status in (404, 403):
            client, service = self.make({MODELS.deep: (status, "no such model")})
            with self.assertLogs("dmbot.ai", "WARNING") as logged:
                reply = await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual((reply.model, reply.text), (MODELS.careful, "the answer"))
            self.assertEqual(service.asked, [MODELS.deep, MODELS.careful])
            self.assertIn("refused model claude-deep-1", logged.output[0])

    async def test_it_goes_all_the_way_down_if_it_must(self) -> None:
        client, service = self.make({MODELS.deep: (404, "x"), MODELS.careful: (403, "x")})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            reply = await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertEqual(reply.model, MODELS.fast)
        self.assertEqual(service.asked, [MODELS.deep, MODELS.careful, MODELS.fast])
        self.assertEqual(len([m for m in logged.output if "refused model" in m]), 2)

    async def test_the_order_is_deep_careful_fast_and_careful_never_goes_up(self) -> None:
        client, service = self.make({MODELS.careful: (404, "x")})
        with self.assertLogs("dmbot.ai", "WARNING"):
            reply = await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(reply.model, MODELS.fast)
        self.assertNotIn(MODELS.deep, service.asked)

    async def test_a_refusal_is_logged_once_and_not_asked_again(self) -> None:
        client, service = self.make({MODELS.careful: (404, "x")})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            for _ in range(3):
                await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(len([m for m in logged.output if "refused model" in m]), 1)
        self.assertEqual(service.asked, [MODELS.careful, MODELS.fast, MODELS.fast, MODELS.fast])
        self.assertEqual(client.tier(AIModelTier.CAREFUL).model, MODELS.fast)  # now says so

    async def test_the_bottom_tier_has_nowhere_to_go(self) -> None:
        client, _ = self.make({MODELS.fast: (404, "x")})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.FAST)
        client, _ = self.make({MODELS.fast: (403, "x")})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.FAST)
        self.assertIn("key", str(caught.exception))  # a refused key is still a refused key

    async def test_a_bad_key_or_a_busy_service_never_changes_tier(self) -> None:
        for status in (401, 429, 500, 529, 400):
            client, service = self.make({MODELS.deep: (status, "x")})
            with self.assertLogs("dmbot.ai"), self.assertRaises(AIError):
                await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual(service.asked, [MODELS.deep], status)

    async def test_out_of_funds_never_falls_back(self) -> None:
        for status in (400, 402, 403, 429):
            watch = Watch()
            client, service = self.make({MODELS.deep: (status, FUNDS)}, watch)
            with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIOutOfFunds):
                await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual(service.asked, [MODELS.deep], status)
            self.assertEqual(watch.funds, 1)
        # a 404 for a model is not money; a 403 about money is not a refused model
        client, service = self.make({MODELS.careful: (403, FUNDS)})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIOutOfFunds):
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(service.asked, [MODELS.careful])

    async def test_tiers_that_share_a_model_are_not_asked_twice(self) -> None:
        service = Service({"claude-same": (404, "x")})
        client = AnthropicClient("k", AIModels.same("claude-same"), session=service)  # type: ignore[arg-type]
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertEqual(service.asked, ["claude-same"])

    async def test_the_tier_client_is_the_same_call_on_one_tier(self) -> None:
        client, service = self.make({})
        deep = client.tier(AIModelTier.DEEP)
        reply = await deep.complete("s", "t", max_tokens=5)
        self.assertEqual((reply.model, service.asked), (MODELS.deep, [MODELS.deep]))
        self.assertEqual((deep.tier, deep.model), (AIModelTier.DEEP, MODELS.deep))
        self.assertNotIn("sk-secret", repr(deep) + repr(client))


class UsageLine(unittest.IsolatedAsyncioTestCase):
    async def test_one_line_per_call_with_counts_and_never_content(self) -> None:
        service = Service({})
        client = AnthropicClient("sk-secret", MODELS, session=service)  # type: ignore[arg-type]
        with self.assertLogs("dmbot.ai", "INFO") as logged:
            await client.complete(
                "SECRET SYSTEM TEXT", "PRIVATE USER WORDS", tier=AIModelTier.CAREFUL
            )
        (line,) = logged.output
        self.assertIn("tier=careful", line)
        self.assertIn("model=claude-careful-1", line)
        self.assertIn("in=120", line)
        self.assertIn("out=7", line)
        self.assertIn("cache_read=90", line)
        for private in ("SECRET SYSTEM TEXT", "PRIVATE USER WORDS", "the answer", "sk-secret"):
            self.assertNotIn(private, line)

    async def test_a_fallback_says_which_tier_was_asked_for(self) -> None:
        service = Service({MODELS.deep: (404, "x")})
        client = AnthropicClient("k", MODELS, session=service)  # type: ignore[arg-type]
        with self.assertLogs("dmbot.ai", "INFO") as logged:
            await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertTrue(any("tier=careful" in m and "asked=deep" in m for m in logged.output))

    async def test_the_watch_still_counts_tokens(self) -> None:
        watch = Watch()
        client = AnthropicClient("k", MODELS, session=Service({}), watch=watch)  # type: ignore[arg-type]
        await client.complete("s", "t", tier=AIModelTier.FAST)
        self.assertEqual(watch.used, [(120, 7)])


class NoModelNamedElsewhere(unittest.TestCase):
    # The price tables of the cost tools are lists of models, not calls to one.
    PRICE_TABLES: ClassVar[set[str]] = {"devtools/costs/prices.py", "devtools/claims/cost.py"}
    MODEL_ID = re.compile(r"claude-(?:haiku|sonnet|opus|instant|\d)[a-z0-9.\-]*")

    def test_no_model_id_in_the_code_outside_config_and_ai(self) -> None:
        root = Path(ai_module.__file__).parent
        found: dict[str, list[str]] = {}
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if relative in {"ai.py", "config.py", *self.PRICE_TABLES}:
                continue
            ids = self.MODEL_ID.findall(path.read_text())
            if ids:
                found[relative] = ids
        self.assertEqual(found, {}, "a job names a tier (dmbot.ai.FEATURE_TIERS), not a model")


if __name__ == "__main__":
    unittest.main()
