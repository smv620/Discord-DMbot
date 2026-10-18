"""Owner alerts (#1907): one fixed sentence per problem, once an hour, five a day, Discord when
mail is what failed, nothing when the owner's address isn't set. Addresses are example.com."""

from __future__ import annotations

import ast
import logging
import unittest
from email.message import EmailMessage
from pathlib import Path

from dmbot import alerts, mail
from dmbot.alerts import Alerter, Outage, Problem
from dmbot.mail import MailConfigError, MailSettings, load_mail_settings

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USER": "sender@example.com",
    "SMTP_APP_PASSWORD": "not-a-real-password",
    "MAIL_FROM": "DMbot <sender@example.com>",
    "MAIL_ALERTS_FROM": "DMbot alerts <alerts@example.com>",
    "OWNER_ALERT_EMAIL": "owner@example.com",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self, env: dict[str, str] | None = None, *, fail_mail: bool = False) -> None:
        self.sent: list[EmailMessage] = []
        self.told: list[str] = []
        self.fail_mail = fail_mail
        self.clock = Clock()
        self.alerter = Alerter(
            load_mail_settings(ENV if env is None else env),
            transport=self._transport,
            tell_owner=self._tell,
            clock=self.clock,
        )

    async def _transport(self, message: EmailMessage) -> None:
        if self.fail_mail:
            raise OSError("smtp refused owner@example.com")
        self.sent.append(message)

    async def _tell(self, text: str) -> None:
        self.told.append(text)


class Sender(unittest.TestCase):
    def test_the_reserved_address_is_refused_for_every_from_setting(self) -> None:
        for name in ("MAIL_FROM", "MAIL_SUPPORT_FROM", "MAIL_ALERTS_FROM"):
            for value in ("admin@getdmbot.com", "DMbot <Admin@GetDMbot.com>"):
                with self.assertRaises(MailConfigError) as caught:
                    load_mail_settings({**ENV, name: value})
                self.assertIn(name, str(caught.exception))

    def test_other_getdmbot_addresses_are_fine(self) -> None:
        s = load_mail_settings({**ENV, "MAIL_FROM": "DMbot <accounts@getdmbot.com>"})
        self.assertEqual(s.sender, "DMbot <accounts@getdmbot.com>")

    def test_the_owner_address_is_one_address_and_never_shown(self) -> None:
        with self.assertRaises(MailConfigError):
            load_mail_settings({**ENV, "OWNER_ALERT_EMAIL": "a@example.com, b@example.com"})
        with self.assertRaises(MailConfigError):
            load_mail_settings({**ENV, "MAIL_ALERTS_REPLY_TO": "not an address"})
        self.assertNotIn("owner@example.com", repr(load_mail_settings(ENV)))

    def test_other_mail_stops_short_of_the_cap_by_the_kept_back_slots(self) -> None:
        s = MailSettings(daily_cap=400)
        self.assertEqual(s.mail_cap, 390)
        self.assertEqual(s.offer_cap, 292)  # 3/4 of what other mail may use
        self.assertEqual(2 * alerts.ALERT_DAILY_MAX, mail.ALERT_RESERVE)  # the bot and the website
        with self.assertRaises(MailConfigError):  # a cap the kept-back slots would swallow
            load_mail_settings({**ENV, "MAIL_DAILY_CAP": str(mail.ALERT_RESERVE)})


class Sending(unittest.IsolatedAsyncioTestCase):
    async def test_one_fixed_sentence_goes_to_the_owner(self) -> None:
        rig = Rig({**ENV, "MAIL_ALERTS_REPLY_TO": "ops@example.com"})
        self.assertTrue(await rig.alerter.alert(Problem.AI_OUT_OF_FUNDS))
        (message,) = rig.sent
        self.assertEqual(message["To"], "owner@example.com")
        self.assertEqual(message["From"], "DMbot alerts <alerts@example.com>")
        self.assertEqual(message["Reply-To"], "ops@example.com")
        self.assertEqual(message.get_content().strip(), Problem.AI_OUT_OF_FUNDS.value)
        self.assertEqual(rig.told, [])

    async def test_no_reply_to_unless_set(self) -> None:
        rig = Rig()
        await rig.alerter.alert(Problem.STRIPE_KEY_REFUSED)
        self.assertIsNone(rig.sent[0]["Reply-To"])

    async def test_one_email_per_problem_per_hour(self) -> None:
        rig = Rig()
        self.assertTrue(await rig.alerter.alert(Problem.AI_KEY_REFUSED))
        self.assertFalse(await rig.alerter.alert(Problem.AI_KEY_REFUSED))
        self.assertTrue(await rig.alerter.alert(Problem.DEEPGRAM_KEY_REFUSED))  # another problem
        rig.clock.now += alerts.ALERT_EVERY_S - 1
        self.assertFalse(await rig.alerter.alert(Problem.AI_KEY_REFUSED))
        rig.clock.now += 1
        self.assertTrue(await rig.alerter.alert(Problem.AI_KEY_REFUSED))
        self.assertEqual(len(rig.sent), 3)

    async def test_the_kept_back_slots_are_a_daily_ceiling(self) -> None:
        rig = Rig()
        problems = [p for p in Problem if p not in alerts.BY_DISCORD_ONLY]
        for _ in range(2):  # a day's worth of hours, over and over
            for problem in problems:
                await rig.alerter.alert(problem)
                rig.clock.now += alerts.ALERT_EVERY_S
        self.assertEqual(len(rig.sent), alerts.ALERT_DAILY_MAX)  # the rest went to Discord
        self.assertGreater(len(rig.told), 0)
        rig.clock.now += alerts.ALERT_WINDOW_S
        self.assertTrue(await rig.alerter.alert(Problem.AI_KEY_REFUSED))
        self.assertEqual(len(rig.sent), alerts.ALERT_DAILY_MAX + 1)

    async def test_failing_emails_tell_discord_once_an_hour_and_use_no_slot(self) -> None:
        rig = Rig(fail_mail=True)
        with self.assertLogs("dmbot.alerts", logging.ERROR):
            for problem in (Problem.AI_KEY_REFUSED, Problem.DEEPGRAM_KEY_REFUSED):
                await rig.alerter.alert(problem)
        self.assertEqual(rig.told, [f"⚠️ {Problem.MAIL_FAILED.value}"])
        rig.fail_mail = False
        for problem in [p for p in Problem if p not in alerts.BY_DISCORD_ONLY]:
            await rig.alerter.alert(problem)
        self.assertGreaterEqual(len(rig.sent), 3)  # the failed sends took no slots

    async def test_when_mail_failed_the_owner_is_told_on_discord_and_the_log(self) -> None:
        rig = Rig()
        with self.assertLogs("dmbot.alerts", logging.ERROR) as logged:
            self.assertTrue(await rig.alerter.alert(Problem.MAIL_FAILED))
        self.assertEqual(rig.sent, [])  # not by email: email is what broke
        self.assertEqual(rig.told, [f"⚠️ {Problem.MAIL_FAILED.value}"])
        self.assertIn("MAIL_FAILED", "\n".join(logged.output))

    async def test_an_email_that_cannot_go_falls_back_to_discord(self) -> None:
        rig = Rig(fail_mail=True)
        with self.assertLogs("dmbot.alerts", logging.ERROR) as logged:
            self.assertTrue(await rig.alerter.alert(Problem.AI_OUT_OF_FUNDS))
        self.assertEqual(rig.told, [f"⚠️ {Problem.MAIL_FAILED.value}"])
        self.assertNotIn("owner@example.com", "\n".join(logged.output))  # the address isn't logged

    async def test_without_the_owner_address_nothing_is_emailed_and_the_log_says_so_once(
        self,
    ) -> None:
        env = {k: v for k, v in ENV.items() if k != "OWNER_ALERT_EMAIL"}
        rig = Rig(env)
        with self.assertLogs("dmbot.alerts", logging.INFO) as logged:
            rig.alerter.say_if_off()
            rig.alerter.say_if_off()
            await rig.alerter.alert(Problem.AI_OUT_OF_FUNDS)
        self.assertEqual(rig.sent, [])
        off = [line for line in logged.output if "alerts are off" in line]
        self.assertEqual(len(off), 1)

    async def test_with_nothing_to_tell_discord_with_it_only_logs(self) -> None:
        alerter = Alerter(load_mail_settings({}), clock=Clock())
        with self.assertLogs("dmbot.alerts", logging.ERROR):
            self.assertFalse(await alerter.alert(Problem.DATABASE_DOWN))

    async def test_raise_alert_reaches_the_installed_alerter_and_never_raises(self) -> None:
        rig = Rig()
        alerts.install(rig.alerter)
        try:
            alerts.raise_alert(Problem.STRIPE_KEY_REFUSED)
            await _drain()
        finally:
            alerts.install(None)
        self.assertEqual(len(rig.sent), 1)
        with self.assertLogs("dmbot.alerts", logging.ERROR):
            alerts.raise_alert(Problem.STRIPE_KEY_REFUSED)  # no alerter: only the log


async def _drain() -> None:
    import asyncio

    await asyncio.gather(*alerts._tasks)


class NoSecrets(unittest.TestCase):
    def test_every_sentence_is_fixed_text_with_no_blank_to_fill(self) -> None:
        for problem in Problem:
            text = problem.value
            self.assertTrue(text.endswith("."), problem.name)
            for marker in ("{", "}", "%s", "%d"):
                self.assertNotIn(marker, text, problem.name)

    def test_every_caller_names_a_problem_and_passes_nothing_it_read(self) -> None:
        calls = 0
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and (
                        (isinstance(node.func, ast.Name) and node.func.id == "raise_alert")
                        or (isinstance(node.func, ast.Attribute) and node.func.attr == "alert")
                    )
                    and path.name != "alerts.py"
                ):
                    continue
                calls += 1
                (arg,) = node.args
                self.assertFalse(node.keywords, path.name)
                self.assertIsInstance(arg, ast.Attribute, f"{path.name}:{node.lineno}")
                assert isinstance(arg, ast.Attribute)
                owner = arg.value  # Problem.X, or alerts.Problem.X
                name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
                self.assertEqual(name, "Problem", path.name)
        self.assertGreaterEqual(calls, 6)  # AI x2, Deepgram x3, Stripe, mail x2, database


class OutageWindow(unittest.TestCase):
    def test_failures_in_a_row_count_and_an_answer_starts_over(self) -> None:
        clock = Clock()
        outage = Outage(600, clock=clock)
        self.assertFalse(outage.failed())
        clock.now += 599
        self.assertFalse(outage.failed())
        clock.now += 1
        self.assertTrue(outage.failed())
        outage.worked()
        self.assertFalse(outage.failed())

    def test_a_failure_long_after_the_last_one_is_not_ten_minutes_of_outage(self) -> None:
        clock = Clock()
        outage = Outage(600, clock=clock)
        outage.failed()
        clock.now += 2 * 3600  # nobody was playing
        self.assertFalse(outage.failed())
