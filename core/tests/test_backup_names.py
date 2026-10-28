"""The backup-name helpers live outside ui (#2827) and stay importable from ui.logic."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot import discord_limits
from dmbot.campaigns import backup_names
from dmbot.ui import logic


class TheBackupNames(unittest.TestCase):
    def test_discords_import_path_gives_the_same_functions(self) -> None:
        # (Discord's callers use logic.backup_*; the website's restore uses the new module.)
        self.assertIs(logic.backup_filename, backup_names.backup_filename)
        self.assertIs(logic.backup_parts, backup_names.backup_parts)
        self.assertIs(logic.backup_campaign_name, backup_names.backup_campaign_name)
        self.assertIs(logic.shorten, discord_limits.shorten)

    def test_the_file_name_is_a_slug_with_the_day(self) -> None:
        self.assertEqual(
            backup_names.backup_filename("The Brynwater Crossing!", 1_792_000_000),
            "the-brynwater-crossing-2026-10-14.dmbot.json.gz",
        )
        self.assertEqual(
            backup_names.backup_filename("???", 0), "campaign-1970-01-01.dmbot.json.gz"
        )

    def test_the_parts_count_is_zero_unless_it_is_a_sensible_whole_number(self) -> None:
        self.assertEqual(backup_names.backup_parts({"parts": {"count": 3}}), 3)
        odds: list[Any] = [
            None,
            {},
            {"parts": {}},
            {"parts": {"count": 0}},
            {"parts": {"count": 1001}},
            {"parts": {"count": True}},
            {"parts": {"count": "3"}},
            "x",
        ]
        for odd in odds:
            self.assertEqual(backup_names.backup_parts(odd), 0, odd)

    def test_the_campaign_name_is_shortened_and_missing_names_are_none(self) -> None:
        self.assertEqual(backup_names.backup_campaign_name({"campaign": {"name": "Mira"}}), "Mira")
        long = backup_names.backup_campaign_name({"campaign": {"name": "x" * 300}})
        self.assertEqual(len(long or ""), discord_limits.OPTION_LABEL_MAX)
        odds: list[Any] = [
            None,
            {},
            {"campaign": {}},
            {"campaign": {"name": "  "}},
            {"campaign": 1},
        ]
        for odd in odds:
            self.assertIsNone(backup_names.backup_campaign_name(odd), odd)


if __name__ == "__main__":
    unittest.main()
