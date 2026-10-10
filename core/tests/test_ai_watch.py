"""Telling the admins when DMbot's AI account is out of funds (#972): the refusal is
recognised and other errors aren't mistaken for it, the DM gets plain words, each admin hears
at most once a day (surviving a restart), nothing is sent with no admins set, a bad setting
stops start-up, and a daily usage line is logged with counts only."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dmbot.ai import OUT_OF_FUNDS, AIError, AIOutOfFunds, AnthropicClient, is_out_of_funds
from dmbot.ai_watch import ADMIN_NOTICE, NOTICE_EVERY_S, STATE_FILE, AIWatch
from dmbot.config import ConfigError, load_settings
from tests.test_ai import FakeResponse, FakeSession

PRIMARY, SECONDARY = 123456789012345678, 876543210987654321
LOW = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
)
LIMIT = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "You have reached your specified workspace API usage limits.",
        },
    }
)
BAD = json.dumps(
    {"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens: too big"}}
)
BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Recognising(unittest.TestCase):
    def test_out_of_credit_and_the_spend_limit_are_recognised(self) -> None:
        self.assertTrue(is_out_of_funds(400, LOW))
        self.assertTrue(is_out_of_funds(400, LIMIT))
        self.assertTrue(is_out_of_funds(402, ""))
        self.assertTrue(
            is_out_of_funds(429, json.dumps({"error": {"type": "billing_error", "message": ""}}))
        )

    def test_other_errors_are_not(self) -> None:
        self.assertFalse(is_out_of_funds(400, BAD))
        self.assertFalse(is_out_of_funds(400, "not json"))
        self.assertFalse(is_out_of_funds(401, LOW))
        self.assertFalse(is_out_of_funds(500, LOW))
        self.assertFalse(
            is_out_of_funds(
                429, json.dumps({"error": {"type": "rate_limit_error", "message": "x"}})
            )
        )

    def test_the_dm_is_told_in_plain_words(self) -> None:
        for word in ("paused", "topping up", "keep playing"):
            self.assertIn(word, OUT_OF_FUNDS)
        self.assertNotIn("Anthropic", OUT_OF_FUNDS)  # nothing about the account for players


class Watching(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.clock = Clock()
        self.sent: list[tuple[int, str]] = []
        self.broken: set[int] = set()

    async def send(self, user_id: int, text: str) -> None:
        if user_id in self.broken:
            raise RuntimeError("private messages are off")
        self.sent.append((user_id, text))

    def watch(self, admins: dict[str, int]) -> AIWatch:
        return AIWatch(
            admins=admins, state_dir=Path(self.dir.name), send=self.send, clock=self.clock
        )

    def client(self, watch: AIWatch, status: int, body: str) -> AnthropicClient:
        session: Any = FakeSession(FakeResponse(status, body))
        return AnthropicClient("sk-secret", "m", session=session, watch=watch)

    async def test_the_call_raises_and_both_admins_are_told(self) -> None:
        watch = self.watch({"primary": PRIMARY, "secondary": SECONDARY})
        with self.assertLogs("dmbot.ai_watch", "ERROR"), self.assertRaises(AIOutOfFunds) as caught:
            await self.client(watch, 400, LOW).complete("s", "t")
        self.assertEqual(str(caught.exception), OUT_OF_FUNDS)
        await watch.wait()
        self.assertEqual({u for u, _ in self.sent}, {PRIMARY, SECONDARY})
        by_user = dict(self.sent)
        self.assertEqual(by_user[PRIMARY], ADMIN_NOTICE.format(role="primary"))
        self.assertIn("primary admin", by_user[PRIMARY])
        self.assertIn("secondary admin", by_user[SECONDARY])
        self.assertIn("https://console.anthropic.com/settings/billing", by_user[PRIMARY])

    async def test_a_bad_request_is_a_plain_failure_and_tells_nobody(self) -> None:
        watch = self.watch({"primary": PRIMARY})
        with self.assertLogs("dmbot.ai", "ERROR"), self.assertRaises(AIError) as caught:
            await self.client(watch, 400, BAD).complete("s", "t")
        self.assertNotIsInstance(caught.exception, AIOutOfFunds)
        await watch.wait()
        self.assertEqual(self.sent, [])

    async def test_each_admin_hears_at_most_once_a_day(self) -> None:
        watch = self.watch({"primary": PRIMARY})
        with self.assertLogs("dmbot.ai_watch", "ERROR"):
            for _ in range(3):  # many calls fail at once
                watch.out_of_funds()
            await watch.wait()
            self.clock.now += NOTICE_EVERY_S - 60
            watch.out_of_funds()
            await watch.wait()
            self.assertEqual(len(self.sent), 1)
            self.clock.now += 120
            watch.out_of_funds()
            await watch.wait()
        self.assertEqual(len(self.sent), 2)

    async def test_a_restart_does_not_repeat_it(self) -> None:
        with self.assertLogs("dmbot.ai_watch", "ERROR"):
            first = self.watch({"primary": PRIMARY})
            first.out_of_funds()
            await first.wait()
            again = self.watch({"primary": PRIMARY})  # a new process, same data folder
            again.out_of_funds()
            await again.wait()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue((Path(self.dir.name) / STATE_FILE).exists())

    async def test_only_one_admin_set(self) -> None:
        watch = self.watch({"secondary": SECONDARY})
        with self.assertLogs("dmbot.ai_watch", "ERROR"):
            watch.out_of_funds()
            await watch.wait()
        self.assertEqual([u for u, _ in self.sent], [SECONDARY])

    async def test_neither_set_only_logs(self) -> None:
        watch = self.watch({})
        with self.assertLogs("dmbot.ai_watch", "ERROR") as logged:
            watch.out_of_funds()
            await watch.wait()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(logged.records), 1)

    async def test_one_failed_send_does_not_stop_the_other(self) -> None:
        self.broken = {PRIMARY}
        watch = self.watch({"primary": PRIMARY, "secondary": SECONDARY})
        with self.assertLogs("dmbot.ai_watch"):
            watch.out_of_funds()
            await watch.wait()
        self.assertEqual([u for u, _ in self.sent], [SECONDARY])
        self.broken = set()  # the failed one is tried again, the told one is not
        with self.assertLogs("dmbot.ai_watch", "ERROR"):
            self.clock.now += 4000
            watch.out_of_funds()
            await watch.wait()
        self.assertEqual([u for u, _ in self.sent], [SECONDARY, PRIMARY])

    async def test_the_error_line_is_logged_at_most_once_an_hour(self) -> None:
        watch = self.watch({})
        with self.assertLogs("dmbot.ai_watch", "ERROR") as logged:
            watch.out_of_funds()
            watch.out_of_funds()
            self.clock.now += 3601
            watch.out_of_funds()
        self.assertEqual(len(logged.records), 2)

    async def test_a_daily_usage_line_with_counts_only(self) -> None:
        watch = self.watch({})
        watch.record(100, 10)
        self.clock.now += 3600
        watch.record(50, 5)
        with self.assertLogs("dmbot.ai_watch", "INFO") as logged:
            self.clock.now += 24 * 3600 + 10
            watch.record(1, 1)
        line = logged.records[0].getMessage()
        self.assertIn("calls=1", line)  # the first two are older than 24 hours
        self.assertIn("input_tokens=1", line)
        self.assertNotIn("sk-", line)

    async def test_the_client_reports_its_token_counts(self) -> None:
        watch = self.watch({})
        body = {"content": [], "usage": {"input_tokens": 20, "output_tokens": 3}}
        await self.client(watch, 200, json.dumps(body)).complete("s", "t")
        self.assertEqual(next(iter(watch._calls))[1:], (20, 3))


class Settings(unittest.TestCase):
    def test_empty_by_default(self) -> None:
        s = load_settings(BASE)
        self.assertEqual((s.admin_primary_id, s.admin_secondary_id), (None, None))

    def test_both_set(self) -> None:
        s = load_settings(
            {
                **BASE,
                "DMBOT_ADMIN_PRIMARY_ID": str(PRIMARY),
                "DMBOT_ADMIN_SECONDARY_ID": str(SECONDARY),
            }
        )
        self.assertEqual((s.admin_primary_id, s.admin_secondary_id), (PRIMARY, SECONDARY))

    def test_a_bad_value_stops_start_up_naming_the_setting_not_the_value(self) -> None:
        for name in ("DMBOT_ADMIN_PRIMARY_ID", "DMBOT_ADMIN_SECONDARY_ID"):
            for bad in ("12345", "abc12345678901234", "1" * 21, "123456789012345678 9"):
                with self.assertRaises(ConfigError) as caught:
                    load_settings({**BASE, name: bad})
                self.assertIn(name, str(caught.exception))
                self.assertNotIn(bad, str(caught.exception))
