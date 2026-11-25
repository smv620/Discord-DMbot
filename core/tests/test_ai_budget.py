"""The month's AI budget (#5270): warnings at 80% and 100% of DMBOT_AI_MONTHLY_BUDGET_USD, each
once a month, and a notice on the month's first refused call. Nothing is stopped. Counts and fixed
words only. The cost tests need Postgres."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dmbot import service_spend
from dmbot.ai import Reply
from dmbot.ai_watch import AIWatch
from dmbot.config import ConfigError, load_settings
from tests.test_ai_watch import BASE, PRIMARY, SECONDARY, Clock
from tests.test_service_spend import NOW, Spend


class Notices(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.clock = Clock()
        self.sent: list[tuple[int, str]] = []

    async def send(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))

    def watch(self) -> AIWatch:
        return AIWatch(
            admins={"primary": PRIMARY, "secondary": SECONDARY},
            state_dir=Path(self.dir.name),
            send=self.send,
            clock=self.clock,
        )

    async def test_each_admin_hears_a_key_once_and_another_key_again(self) -> None:
        watch = self.watch()
        for _ in range(3):
            watch.notify_once("budget-80-1", "eighty")
        await watch.wait()
        self.assertEqual(sorted(u for u, _ in self.sent), sorted([PRIMARY, SECONDARY]))
        watch.notify_once("budget-100-1", "hundred")
        await watch.wait()
        self.assertEqual(len(self.sent), 4)

    async def test_once_survives_a_restart(self) -> None:
        first = self.watch()
        first.notify_once("budget-80-1", "eighty")
        await first.wait()
        again = self.watch()
        again.notify_once("budget-80-1", "eighty")
        await again.wait()
        self.assertEqual(len(self.sent), 2)

    async def test_nothing_is_sent_with_no_admins(self) -> None:
        quiet = AIWatch(admins={}, state_dir=Path(self.dir.name), send=self.send, clock=self.clock)
        quiet.notify_once("k", "t")
        await quiet.wait()
        self.assertEqual(self.sent, [])


class TheSetting(unittest.TestCase):
    def test_off_by_default_and_a_number_of_dollars_when_set(self) -> None:
        self.assertEqual(load_settings(BASE).ai_monthly_budget_usd, 0.0)
        self.assertEqual(
            load_settings({**BASE, "DMBOT_AI_MONTHLY_BUDGET_USD": "250.5"}).ai_monthly_budget_usd,
            250.5,
        )

    def test_a_bad_value_stops_start_up_naming_the_setting_not_the_value(self) -> None:
        for bad in ("abc", "-1", "nan", "inf", "9" * 12):
            with self.assertRaises(ConfigError) as caught:
                load_settings({**BASE, "DMBOT_AI_MONTHLY_BUDGET_USD": bad})
            self.assertIn("DMBOT_AI_MONTHLY_BUDGET_USD", str(caught.exception))
            self.assertNotIn(bad, str(caught.exception))


class Stub:
    def __init__(self) -> None:
        self.told: list[tuple[str, str]] = []

    def notify_once(self, key: str, text: str) -> None:
        self.told.append((key, text))


class TheMonth(Spend):
    """One Sonnet 5.5 call of a million input tokens costs $2.00."""

    def call(self) -> Reply:
        return Reply("w", cut=False, model="claude-sonnet-5-5", input_tokens=1_000_000)

    async def spend(self, calls: int, notifier: Stub, budget: float) -> None:
        service_spend.watch(notifier, budget)
        for _ in range(calls):
            service_spend.record_ai(self.call(), "recap")
        await service_spend.flush()

    async def test_nothing_is_said_below_eighty_percent_or_with_no_budget(self) -> None:
        low = Stub()
        await self.spend(1, low, 10.0)  # $2 of $10
        self.assertEqual(low.told, [])
        off = Stub()
        await self.spend(5, off, 0.0)
        self.assertEqual(off.told, [])

    async def test_eighty_percent_says_so_with_the_month_in_its_key(self) -> None:
        notifier = Stub()
        await self.spend(4, notifier, 10.0)  # $8 of $10
        ((key, text),) = notifier.told
        month = service_spend.month_start(NOW)
        self.assertEqual(key, f"budget-80-{month}")
        self.assertIn("$8.00", text)
        self.assertIn("80%", text)
        self.assertIn("Nothing is stopped", text)

    async def test_a_hundred_percent_is_its_own_notice(self) -> None:
        notifier = Stub()
        await self.spend(5, notifier, 10.0)
        ((key, text),) = notifier.told
        self.assertTrue(key.startswith("budget-100-"))
        self.assertIn("100%", text)

    async def test_a_sample_runs_spend_is_not_counted(self) -> None:
        notifier = Stub()
        service_spend.configure(self.db, None, lambda: NOW, source="sample")
        await self.spend(5, notifier, 10.0)
        self.assertEqual(notifier.told, [])

    async def test_the_first_refused_call_tells_with_the_feature_word_only(self) -> None:
        notifier = Stub()
        service_spend.watch(notifier, 0.0)
        service_spend.record_refused("sidebar", "fast")
        ((key, text),) = notifier.told
        self.assertEqual(key, f"refused-{service_spend.month_start(NOW)}")
        self.assertIn("sidebar", text)
