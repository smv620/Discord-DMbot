"""The disk check (#4075): the thresholds, each level said at most once an hour, the owner told at
the alert level by the usual fixed message, a path that cannot be read, and the settings. Invented
numbers; no real disk is read."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path

from dmbot.alerts import Problem
from dmbot.config import ConfigError, Settings, load_settings
from dmbot.disk_check import ALERT, OK, SAY_EVERY_S, WARN, DiskWatch, level_of, places_for
from tests.test_config import BASE

GB = 1024**3


class TheLevels(unittest.TestCase):
    def test_below_warn_and_alert(self) -> None:
        self.assertEqual(level_of(40, 85, 95), OK)
        self.assertEqual(level_of(84.9, 85, 95), OK)
        self.assertEqual(level_of(85, 85, 95), WARN)
        self.assertEqual(level_of(94.9, 85, 95), WARN)
        self.assertEqual(level_of(95, 85, 95), ALERT)
        self.assertEqual(level_of(100, 85, 95), ALERT)

    def test_the_places_are_the_data_volume_and_the_temp_directory(self) -> None:
        got = places_for(Path("/data"))
        self.assertEqual(got["data"], Path("/data"))
        self.assertIn("temp", got)


class TheWatch(unittest.TestCase):
    def watch(self, used_pct: int) -> tuple[DiskWatch, list[Problem], list[float]]:
        alerts: list[Problem] = []
        clock = [0.0]
        self.used = used_pct

        def usage(_path: Path) -> tuple[int, int, int]:
            total = 100 * GB
            return total, self.used * GB, (100 - self.used) * GB

        watch = DiskWatch(
            {"data": Path("/data")}, usage=usage, alert=alerts.append, clock=lambda: clock[0]
        )
        return watch, alerts, clock

    def test_a_healthy_disk_says_nothing(self) -> None:
        watch, alerts, _ = self.watch(40)
        with self.assertNoLogs("dmbot.disk_check", level="WARNING"):
            found = watch.check()
        self.assertEqual([(r.path, r.used_pct, r.free_gb) for r in found], [("data", 40, 60.0)])
        self.assertEqual(alerts, [])

    def test_the_warning_level_logs_a_warning_with_counts_only_and_tells_no_one(self) -> None:
        watch, alerts, _ = self.watch(88)
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            watch.check()
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(logged.records[0].levelno, logging.WARNING)
        self.assertEqual(
            logged.records[0].getMessage(), "Disk check: the data disk is 88% full, 12.0 GB free"
        )
        self.assertEqual(alerts, [])

    def test_the_alert_level_logs_an_error_and_tells_the_owner(self) -> None:
        watch, alerts, _ = self.watch(96)
        with self.assertLogs("dmbot.disk_check", level="ERROR") as logged:
            watch.check()
        self.assertEqual(logged.records[0].levelno, logging.ERROR)
        self.assertEqual(alerts, [Problem.DISK_FULL])

    def test_each_level_is_said_once_an_hour_and_again_after(self) -> None:
        watch, alerts, clock = self.watch(96)
        watch.check()
        clock[0] += 600
        watch.check()
        self.assertEqual(len(alerts), 1)  # (ten minutes later: still the same hour)
        clock[0] += SAY_EVERY_S
        watch.check()
        self.assertEqual(len(alerts), 2)

    def test_a_disk_that_got_worse_is_said_at_the_new_level_at_once(self) -> None:
        watch, alerts, clock = self.watch(88)
        with self.assertLogs("dmbot.disk_check", level="WARNING"):
            watch.check()
        self.used = 97
        clock[0] += 60
        with self.assertLogs("dmbot.disk_check", level="ERROR"):
            watch.check()
        self.assertEqual(alerts, [Problem.DISK_FULL])

    def test_a_path_that_cannot_be_read_is_one_warning_and_the_others_go_on(self) -> None:
        alerts: list[Problem] = []

        def usage(path: Path) -> tuple[int, int, int]:
            if path == Path("/gone"):
                raise FileNotFoundError(str(path))
            return 100 * GB, 97 * GB, 3 * GB

        watch = DiskWatch(
            {"gone": Path("/gone"), "temp": Path("/tmp")}, usage=usage, alert=alerts.append
        )
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            found = watch.check()
        self.assertEqual(
            [r.path for r in found], ["temp"]
        )  # (the readable one was still looked at)
        self.assertEqual(alerts, [Problem.DISK_FULL])
        messages = [r.getMessage() for r in logged.records]
        self.assertTrue(
            any("couldn't read the gone disk (FileNotFoundError)" in m for m in messages)
        )
        self.assertFalse(any("/gone" in m for m in messages))  # (the name of the place, not a path)

    def test_the_owner_message_is_fixed_words_with_the_issue_and_nothing_else(self) -> None:
        text = Problem.DISK_FULL.value
        self.assertIn("free space before the database stops", text)
        self.assertIn("#4075", text)


class TheSettings(unittest.TestCase):
    def settings(self, **env: str) -> Settings:
        return load_settings({**BASE, **env})

    def test_the_defaults_are_85_and_95(self) -> None:
        got = self.settings()
        self.assertEqual((got.disk_warn_pct, got.disk_alert_pct), (85, 95))

    def test_they_can_be_set(self) -> None:
        got = self.settings(DMBOT_DISK_WARN_PCT="70", DMBOT_DISK_ALERT_PCT="90")
        self.assertEqual((got.disk_warn_pct, got.disk_alert_pct), (70, 90))

    def test_a_bad_value_or_warn_not_below_alert_refuses_to_start(self) -> None:
        for env in (
            {"DMBOT_DISK_WARN_PCT": "abc"},
            {"DMBOT_DISK_ALERT_PCT": "0"},
            {"DMBOT_DISK_ALERT_PCT": "100"},
            {"DMBOT_DISK_WARN_PCT": "96"},
            {"DMBOT_DISK_WARN_PCT": "90", "DMBOT_DISK_ALERT_PCT": "90"},
        ):
            with self.subTest(env=env), self.assertRaises(ConfigError):
                self.settings(**env)
