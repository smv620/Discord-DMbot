"""The disk check (#4075): the thresholds, each level said at most once an hour, the owner told at
the alert level by the usual fixed message, a path that cannot be read, and the settings. Invented
numbers; no real disk is read."""

from __future__ import annotations

import asyncio
import logging
import unittest
from pathlib import Path
from typing import Any, cast

from dmbot import alerts
from dmbot.alerts import Problem
from dmbot.config import ConfigError, Settings, load_settings
from dmbot.disk_check import (
    ALERT,
    OK,
    SAY_EVERY_S,
    WARN,
    DiskWatch,
    Result,
    level_of,
    look,
    places_for,
)
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
    def watch(self, used_pct: int) -> tuple[DiskWatch, list[float]]:
        clock = [0.0]
        self.used = used_pct

        def usage(_path: Path) -> tuple[int, int, int]:
            total = 100 * GB
            return total, self.used * GB, (100 - self.used) * GB

        watch = DiskWatch(
            {"data": Path("/data")},
            usage=usage,
            device=lambda _path: 1,
            inodes=lambda _path: (1000, 900),  # (10% of inodes used: healthy)
            clock=lambda: clock[0],
        )
        return watch, clock

    def test_a_healthy_disk_says_nothing(self) -> None:
        watch, _ = self.watch(40)
        with self.assertNoLogs("dmbot.disk_check", level="WARNING"):
            got = watch.check()
        self.assertEqual(
            [(r.path, r.used_pct, r.free_gb) for r in got.readings], [("data", 40, 60.0)]
        )
        self.assertEqual(got.alerts, [])

    def test_the_warning_level_logs_a_warning_with_counts_only_and_tells_no_one(self) -> None:
        watch, _ = self.watch(88)
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = watch.check()
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(logged.records[0].levelno, logging.WARNING)
        self.assertEqual(
            logged.records[0].getMessage(), "Disk check: the data disk is 88% full, 12.0 GB free"
        )
        self.assertEqual(got.alerts, [])

    def test_the_alert_level_logs_an_error_and_asks_to_tell_the_owner(self) -> None:
        watch, _ = self.watch(96)
        with self.assertLogs("dmbot.disk_check", level="ERROR") as logged:
            got = watch.check()
        self.assertEqual(logged.records[0].levelno, logging.ERROR)
        self.assertEqual(got.alerts, [Problem.DISK_FULL])

    def test_the_level_is_the_percent_rounded_down(self) -> None:
        """94.6% is 94% and a warning: it is never logged "95% full" at the warning level."""
        watch = DiskWatch(
            {"data": Path("/data")}, usage=lambda _p: (1000, 946, 54), device=lambda _p: 1
        )
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = watch.check()
        self.assertEqual(got.alerts, [])
        self.assertIn("94% full", logged.records[0].getMessage())

    def test_each_level_is_said_once_an_hour_and_again_after(self) -> None:
        watch, clock = self.watch(96)
        self.assertEqual(len(watch.check().alerts), 1)
        clock[0] += 600
        self.assertEqual(watch.check().alerts, [])  # (ten minutes later: still the same hour)
        clock[0] += SAY_EVERY_S
        self.assertEqual(len(watch.check().alerts), 1)

    def test_a_disk_that_got_worse_is_said_at_the_new_level_at_once(self) -> None:
        watch, clock = self.watch(88)
        with self.assertLogs("dmbot.disk_check", level="WARNING"):
            watch.check()
        self.used = 97
        clock[0] += 60
        with self.assertLogs("dmbot.disk_check", level="ERROR"):
            got = watch.check()
        self.assertEqual(got.alerts, [Problem.DISK_FULL])

    def test_places_on_one_disk_are_one_place(self) -> None:
        watch = DiskWatch(
            {"data": Path("/data"), "temp": Path("/tmp")},
            usage=lambda _p: (100 * GB, 97 * GB, 3 * GB),
            device=lambda _p: 7,  # (the same filesystem under both names)
        )
        with self.assertLogs("dmbot.disk_check", level="ERROR") as logged:
            got = watch.check()
        self.assertEqual([r.path for r in got.readings], ["data"])
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(got.alerts, [Problem.DISK_FULL])

    def test_two_different_disks_are_both_looked_at(self) -> None:
        devices = {Path("/data"): 1, Path("/tmp"): 2}
        watch = DiskWatch(
            {"data": Path("/data"), "temp": Path("/tmp")},
            usage=lambda _p: (100 * GB, 50 * GB, 50 * GB),
            device=lambda p: devices[p],
        )
        self.assertEqual([r.path for r in watch.check().readings], ["data", "temp"])

    def test_a_path_that_cannot_be_read_is_one_warning_and_the_others_go_on(self) -> None:
        def usage(path: Path) -> tuple[int, int, int]:
            if path == Path("/gone"):
                raise FileNotFoundError(str(path))
            return 100 * GB, 97 * GB, 3 * GB

        devices = {Path("/gone"): 1, Path("/tmp"): 2}
        watch = DiskWatch(
            {"gone": Path("/gone"), "temp": Path("/tmp")},
            usage=usage,
            device=lambda p: devices[p],
        )
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = watch.check()
        self.assertEqual([r.path for r in got.readings], ["temp"])  # (still looked at)
        self.assertEqual(got.alerts, [Problem.DISK_FULL])
        messages = [r.getMessage() for r in logged.records]
        self.assertTrue(
            any("couldn't read the gone disk (FileNotFoundError)" in m for m in messages)
        )
        self.assertFalse(any("/gone" in m for m in messages))  # (the name of the place, not a path)

    def inodes_watch(
        self, bytes_pct: int, inodes_pct: int, clock: list[float] | None = None
    ) -> DiskWatch:
        """A disk with `bytes_pct` of its bytes and `inodes_pct` of its inodes used (#4077)."""
        now = clock or [0.0]
        return DiskWatch(
            {"data": Path("/data")},
            usage=lambda _p: (100 * GB, bytes_pct * GB, (100 - bytes_pct) * GB),
            device=lambda _p: 1,
            inodes=lambda _p: (1000, 1000 - inodes_pct * 10),
            clock=lambda: now[0],
        )

    def test_inodes_at_the_warning_level_are_a_warning_and_tell_no_one(self) -> None:
        watch = self.inodes_watch(40, 88)
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = watch.check()
        self.assertEqual(got.alerts, [])
        self.assertEqual(
            [r.getMessage() for r in logged.records],
            ["Disk check: the data disk's inodes are 88% used"],
        )
        self.assertEqual(logged.records[0].levelname, "WARNING")

    def test_inodes_at_the_alert_level_are_an_error_and_ask_for_the_owner_alert(self) -> None:
        watch = self.inodes_watch(40, 96)
        with self.assertLogs("dmbot.disk_check", level="ERROR") as logged:
            got = watch.check()
        self.assertEqual(got.alerts, [Problem.DISK_FULL])
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(
            logged.records[0].getMessage(), "Disk check: the data disk's inodes are 96% used"
        )

    def test_a_filesystem_with_no_inodes_to_count_is_skipped(self) -> None:
        watch = DiskWatch(
            {"data": Path("/data")},
            usage=lambda _p: (100 * GB, 40 * GB, 60 * GB),
            device=lambda _p: 1,
            inodes=lambda _p: (0, 0),
        )
        with self.assertNoLogs("dmbot.disk_check", level="WARNING"):
            got = watch.check()
        self.assertEqual(got.alerts, [])

    def test_a_full_disk_and_full_inodes_are_each_said_once_an_hour(self) -> None:
        clock = [0.0]
        watch = self.inodes_watch(96, 96, clock)
        with self.assertLogs("dmbot.disk_check", level="ERROR"):
            first = watch.check()
        # one owner alert per look, even with both full
        self.assertEqual(first.alerts, [Problem.DISK_FULL])
        clock[0] = 600.0  # ten minutes later: both were said within the hour
        with self.assertNoLogs("dmbot.disk_check", level="ERROR"):
            later = watch.check()
        self.assertEqual(later.alerts, [])

    def test_inodes_that_cannot_be_read_are_one_warning(self) -> None:
        def unreadable(_p: Path) -> tuple[int, int]:
            raise OSError("no inodes here")

        watch = DiskWatch(
            {"data": Path("/data")},
            usage=lambda _p: (100 * GB, 40 * GB, 60 * GB),
            device=lambda _p: 1,
            inodes=unreadable,
        )
        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = watch.check()
        self.assertEqual(
            [r.getMessage() for r in logged.records],
            ["Disk check: couldn't read the data disk's inodes (OSError)"],
        )
        self.assertEqual([r.path for r in got.readings], ["data"])  # the bytes are still read

    def test_the_owner_message_is_fixed_plain_words_with_the_issue(self) -> None:
        text = Problem.DISK_FULL.value
        self.assertIn("Free some space", text)
        self.assertIn("before the database stops", text)
        self.assertIn("#4075", text)


class TheRealPath(unittest.IsolatedAsyncioTestCase):
    """The look is made in a worker thread, but the owner alert must start on the event loop:
    `raise_alert` starts a task, which a thread cannot (the bug a stand-in alert hid)."""

    async def test_the_alert_reaches_the_alerter_from_a_look_made_in_a_thread(self) -> None:
        told: list[Problem] = []

        class Stand:
            async def alert(self, problem: Problem) -> None:
                told.append(problem)

        watch = DiskWatch(
            {"data": Path("/data")},
            usage=lambda _p: (100 * GB, 97 * GB, 3 * GB),
            device=lambda _p: 1,
        )
        before = alerts._current
        alerts._current = cast(Any, Stand())
        try:
            got = await look(watch)
            for _ in range(3):
                await asyncio.sleep(0)  # (let the task `raise_alert` started run)
        finally:
            alerts._current = before
        self.assertEqual(got.alerts, [Problem.DISK_FULL])
        self.assertEqual(told, [Problem.DISK_FULL])

    async def test_a_look_that_fails_is_a_line_and_not_an_error(self) -> None:
        class Broken(DiskWatch):
            def check(self) -> Result:
                raise RuntimeError("boom")

        with self.assertLogs("dmbot.disk_check", level="WARNING") as logged:
            got = await look(Broken({}))
        self.assertEqual((got.readings, got.alerts), ([], []))
        self.assertIn("Disk check failed (RuntimeError)", logged.records[0].getMessage())


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
