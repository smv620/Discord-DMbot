"""The helper that stretches a wall-clock limit on a busy machine (#2490)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests import speed


class Allowed(unittest.TestCase):
    def test_a_quiet_machine_gets_the_plain_limit(self) -> None:
        with patch.object(speed, "slowdown", return_value=1.0):
            self.assertEqual(speed.allowed(0.5), 0.5)

    def test_a_busy_machine_gets_more_room_by_the_worst_slowdown_seen(self) -> None:
        with patch.object(speed, "slowdown", return_value=2.0):
            self.assertEqual(speed.allowed(0.5, 3.0), 1.5)
            self.assertEqual(speed.allowed(0.5), 1.0)

    def test_the_slowdown_is_between_one_and_the_backstop(self) -> None:
        self.assertGreaterEqual(speed.slowdown(), 1.0)
        self.assertLessEqual(speed.slowdown(), speed.MOST)
        with patch.object(speed, "_work", return_value=100.0):
            self.assertEqual(speed.slowdown(), speed.MOST)  # a runaway still fails


if __name__ == "__main__":
    unittest.main()
