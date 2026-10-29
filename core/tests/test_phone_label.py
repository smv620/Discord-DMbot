"""The phone's label limit is one number, defined beside the other Discord limits (#2827)."""

from __future__ import annotations

import unittest

from dmbot import discord_limits
from dmbot.ui import logic


class ThePhoneLabelLimit(unittest.TestCase):
    def test_the_phone_label_limit_is_one_number_under_both_names(self) -> None:
        self.assertEqual(logic.PHONE_LABEL_MAX, discord_limits.PHONE_LABEL_MAX)
        self.assertEqual(discord_limits.PHONE_LABEL_MAX, 25)
