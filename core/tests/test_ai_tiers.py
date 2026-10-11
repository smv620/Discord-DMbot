"""AI model tiers (#1006): the settings, the tier of each job, the fallback to the next tier
down, the out-of-funds error never falling back, and the one usage line per call."""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

from dmbot import ai as ai_module
from dmbot.ai import (
    DEFAULT_MODELS,
    FEATURE_TIERS,
    AIError,
    AIModels,
    AIModelTier,
    AIOutOfFunds,
    AnthropicClient,
    Feature,
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

    def test_quotes_and_spaces_a_phone_adds_are_dropped_but_inside_ones_are_not_allowed(
        self,
    ) -> None:
        models, _ = parse_ai_models(
            env(AI_MODEL_FAST=' "claude-a-1" ', AI_MODEL_DEEP="'claude-c-3'")
        )
        self.assertEqual((models.fast, models.deep), ("claude-a-1", "claude-c-3"))
        for bad in ("claude a-1", "Claude-a-1", "claude-A-1", "claude-" + "x" * 90):
            with self.assertRaises(ConfigError):
                parse_ai_models(env(AI_MODEL_CAREFUL=bad))

    def test_the_error_says_what_to_do_and_that_empty_is_fine(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            parse_ai_models(env(AI_MODEL_FAST="gpt-4"))
        text = str(caught.exception)
        self.assertIn("AI_MODEL_FAST in .env isn't a Claude model name", text)
        self.assertIn(DEFAULT_MODELS.fast, text)
        self.assertIn("leave it empty", text)

    def test_the_old_name_notice_says_it_now_covers_every_quick_job(self) -> None:
        self.assertIn("quick jobs", OLD_MODEL_NOTICE)
        self.assertIn("rename it to AI_MODEL_FAST", OLD_MODEL_NOTICE)

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
                Feature.NAMES: AIModelTier.CAREFUL,
                Feature.TOPIC: AIModelTier.FAST,
                Feature.AUDIO_CHECK: AIModelTier.FAST,
                Feature.SIDEBAR: AIModelTier.FAST,
                Feature.RULES: AIModelTier.FAST,
                Feature.CLEANER: AIModelTier.FAST,
                Feature.HOUSE_RULES: AIModelTier.CAREFUL,
                Feature.SIDEBAR_RETRY: AIModelTier.CAREFUL,
                Feature.RULES_CONFIRM: AIModelTier.CAREFUL,
            },
        )
        self.assertEqual(set(FEATURE_TIERS), set(Feature))  # none left out
        self.assertNotIn(AIModelTier.DEEP, FEATURE_TIERS.values())  # PlotBot, later

    def test_the_bot_gives_each_job_the_client_for_its_tier(self) -> None:
        from unittest.mock import MagicMock

        from dmbot.bot import DMBot
        from dmbot.config import Settings as BotSettings

        settings = BotSettings(discord_token="t", ears_secret="s", ai_key="k", ai_models=MODELS)
        bot = DMBot(settings, SimpleNamespace(), MagicMock(), MagicMock())  # type: ignore[arg-type]
        for client, job in (
            (bot.ai, Feature.NAMES),
            (bot.topic_ai, Feature.TOPIC),
            (bot.audio_ai, Feature.AUDIO_CHECK),
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


PERMISSION = json.dumps(
    {"error": {"type": "permission_error", "message": "Your key can't use this model"}}
)
NOT_FOUND = json.dumps({"error": {"type": "not_found_error", "message": "model: x"}})
BLOCKED = "<html>Access denied by the gateway</html>"
GONE = (404, NOT_FOUND)
DENIED = (403, PERMISSION)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Fallback(unittest.IsolatedAsyncioTestCase):
    def make(
        self, answers: dict[str, tuple[int, Any]], watch: Watch | None = None
    ) -> tuple[AnthropicClient, Service]:
        service = Service(answers)
        self.clock = Clock()
        session: Any = service
        client = AnthropicClient(
            "sk-secret", MODELS, session=session, watch=watch, clock=self.clock
        )
        return client, service

    async def test_a_tier_that_works_answers_alone(self) -> None:
        client, service = self.make({})
        for tier in AIModelTier:
            reply = await client.complete("s", "t", tier=tier)
            self.assertEqual(reply.model, MODELS.of(tier))
        self.assertEqual(service.asked, [MODELS.fast, MODELS.careful, MODELS.deep])

    async def test_a_model_that_is_not_available_falls_back_one_tier_down(self) -> None:
        for answer in (GONE, DENIED):
            client, service = self.make({MODELS.deep: answer})
            with self.assertLogs("dmbot.ai", "WARNING") as logged:
                reply = await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual((reply.model, reply.text), (MODELS.careful, "the answer"))
            self.assertEqual(service.asked, [MODELS.deep, MODELS.careful])
            (line,) = logged.output
            self.assertIn("claude-deep-1 (deep jobs) isn't available to this key", line)
            self.assertIn("Using claude-careful-1 instead", line)
            self.assertIn("AI_MODEL_DEEP", line)  # what to check, in the owner's words

    async def test_it_goes_all_the_way_down_if_it_must(self) -> None:
        client, service = self.make({MODELS.deep: GONE, MODELS.careful: DENIED})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            reply = await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertEqual(reply.model, MODELS.fast)
        self.assertEqual(service.asked, [MODELS.deep, MODELS.careful, MODELS.fast])
        self.assertEqual(len(logged.output), 2)

    async def test_careful_never_goes_up(self) -> None:
        client, service = self.make({MODELS.careful: GONE})
        with self.assertLogs("dmbot.ai", "WARNING"):
            reply = await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(reply.model, MODELS.fast)
        self.assertNotIn(MODELS.deep, service.asked)

    async def test_it_is_said_once_and_not_asked_again_for_a_while(self) -> None:
        client, service = self.make({MODELS.careful: GONE})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            for _ in range(3):
                await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(len(logged.output), 1)
        self.assertEqual(service.asked, [MODELS.careful, MODELS.fast, MODELS.fast, MODELS.fast])
        self.assertEqual(client.tier(AIModelTier.CAREFUL).model, MODELS.fast)  # now says so

    async def test_a_fixed_model_is_used_again_after_a_while(self) -> None:
        client, service = self.make({MODELS.careful: GONE})
        with self.assertLogs("dmbot.ai", "WARNING"):
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        service.answers.clear()  # the model is available now (renamed in .env, or access given)
        self.clock.now += ai_module.REFUSED_RETRY_S - 1
        self.assertEqual(
            (await client.complete("s", "t", tier=AIModelTier.CAREFUL)).model, MODELS.fast
        )
        self.clock.now += 2
        reply = await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(reply.model, MODELS.careful)
        self.assertEqual(client.tier(AIModelTier.CAREFUL).model, MODELS.careful)
        await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(service.asked[-2:], [MODELS.careful, MODELS.careful])  # no more skipping

    async def test_still_not_available_later_is_not_said_again(self) -> None:
        client, service = self.make({MODELS.careful: GONE})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)
            self.clock.now += ai_module.REFUSED_RETRY_S + 1
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)  # tried afresh, refused again
        self.assertEqual(len(logged.output), 1)
        self.assertEqual(service.asked.count(MODELS.careful), 2)

    async def test_two_calls_at_once_say_it_once(self) -> None:
        client, service = self.make({MODELS.careful: GONE})
        with self.assertLogs("dmbot.ai", "WARNING") as logged:
            replies = await asyncio.gather(
                *(client.complete("s", "t", tier=AIModelTier.CAREFUL) for _ in range(3))
            )
        self.assertEqual({r.model for r in replies}, {MODELS.fast})
        self.assertEqual(len(logged.output), 1)
        self.assertLessEqual(service.asked.count(MODELS.careful), 3)

    async def test_nothing_is_marked_if_the_lower_tier_fails_too(self) -> None:
        # A bad key looks the same on every tier: no model is blamed for it.
        client, _ = self.make({MODELS.deep: DENIED, MODELS.careful: DENIED, MODELS.fast: DENIED})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertIn("key", str(caught.exception))
        self.assertEqual(client._refused, {})
        self.assertEqual(client.tier(AIModelTier.DEEP).model, MODELS.deep)

    async def test_the_bottom_tier_has_nowhere_to_go(self) -> None:
        client, _ = self.make({MODELS.fast: GONE})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.FAST)
        client, _ = self.make({MODELS.fast: DENIED})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError) as caught:
            await client.complete("s", "t", tier=AIModelTier.FAST)
        self.assertIn("key", str(caught.exception))  # a refused key is still a refused key

    async def test_a_blocked_request_is_not_a_missing_model(self) -> None:
        client, service = self.make({MODELS.deep: (403, BLOCKED)})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertEqual(service.asked, [MODELS.deep])  # no fallback, nothing marked
        self.assertEqual(client._refused, {})

    async def test_a_bad_key_or_a_busy_service_never_changes_tier(self) -> None:
        for status in (401, 429, 500, 529, 400):
            client, service = self.make({MODELS.deep: (status, "x")})
            with self.assertLogs("dmbot.ai"), self.assertRaises(AIError):
                await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual(service.asked, [MODELS.deep], status)

    async def test_a_dropped_connection_does_not_blame_the_model(self) -> None:
        import aiohttp

        class Broken(Service):
            def post(self, url: str, *, json: Any, headers: Any) -> Any:
                raise aiohttp.ClientError("reset")

        service = Broken({})
        client = AnthropicClient("k", MODELS, session=service)  # type: ignore[arg-type]
        with self.assertLogs("dmbot.ai"), self.assertRaises(AIError):
            await client.complete("s", "t", tier=AIModelTier.DEEP)
        self.assertEqual(client._refused, {})

    async def test_out_of_funds_never_falls_back(self) -> None:
        for status in (400, 402, 403, 429):
            watch = Watch()
            client, service = self.make({MODELS.deep: (status, FUNDS)}, watch)
            with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIOutOfFunds):
                await client.complete("s", "t", tier=AIModelTier.DEEP)
            self.assertEqual(service.asked, [MODELS.deep], status)
            self.assertEqual(watch.funds, 1)
        # a funds error that looks like a permission one is still about money
        both = json.dumps(
            {"error": {"type": "permission_error", "message": "Your credit balance is too low"}}
        )
        client, service = self.make({MODELS.careful: (403, both)})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIOutOfFunds):
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(service.asked, [MODELS.careful])

    async def test_tiers_that_share_a_model_are_not_asked_twice(self) -> None:
        service = Service({"claude-same": GONE})
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


class Timeouts(unittest.IsolatedAsyncioTestCase):
    async def test_the_slower_tiers_get_longer_to_answer(self) -> None:
        self.assertEqual(ai_module.TIER_TIMEOUT_S[AIModelTier.FAST], ai_module.REQUEST_TIMEOUT_S)
        for tier in (AIModelTier.CAREFUL, AIModelTier.DEEP):
            self.assertGreater(ai_module.TIER_TIMEOUT_S[tier], ai_module.REQUEST_TIMEOUT_S)

    async def test_a_request_that_takes_too_long_is_a_busy_error_not_a_hang(self) -> None:
        class Hangs(Service):
            def post(self, url: str, *, json: Any, headers: Any) -> Any:
                class Never:
                    async def __aenter__(self) -> Any:
                        await asyncio.sleep(60)

                    async def __aexit__(self, *_: Any) -> None:
                        return None

                return Never()

        client = AnthropicClient("k", MODELS, session=Hangs({}))  # type: ignore[arg-type]
        with (
            patch.dict(ai_module.TIER_TIMEOUT_S, {AIModelTier.CAREFUL: 0.05}),
            self.assertLogs("dmbot.ai"),
            self.assertRaises(AIError) as caught,
        ):
            await client.complete("s", "t", tier=AIModelTier.CAREFUL)
        self.assertEqual(str(caught.exception), ai_module.BUSY)
        self.assertEqual(client._refused, {})  # a slow answer doesn't blame the model


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
        service = Service({MODELS.deep: GONE})
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
