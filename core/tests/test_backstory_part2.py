"""Backstory connections, part 2 (#2650): the upload a waiting connection came from, Keep all
"from this backstory", the DM-screen notice that connections became ready (said once), the
no-AI queue check, and the trailing description. Invented names and text throughout."""

from __future__ import annotations

import unittest

from dmbot.devtools import backstory_links as devtool
from dmbot.dm_screen import links_ready
from dmbot.ui import name_background as ui_bg


class Labels(unittest.TestCase):
    def test_the_upload_is_its_name_and_the_day(self) -> None:
        # 24 Oct 2026, noon UTC
        self.assertEqual(
            ui_bg.upload_label("Tavin's backstory", 1_792_843_200), "Tavin's backstory, 24 Oct"
        )

    def test_a_long_name_is_cut_to_what_the_queue_keeps(self) -> None:
        self.assertLessEqual(len(ui_bg.upload_label("x" * 500, 0)), 120)


class Notice(unittest.TestCase):
    def test_one_upload_one_clause(self) -> None:
        self.assertEqual(
            links_ready.notice({"Tavin's backstory, 24 Oct": 3}),
            "📜 3 connections from Tavin's backstory, 24 Oct are ready to check.",
        )

    def test_one_connection_is_singular(self) -> None:
        self.assertEqual(
            links_ready.notice({"Lore, 1 Jan": 1}),
            "📜 1 connection from Lore, 1 Jan is ready to check.",
        )

    def test_two_uploads_and_old_ones_are_joined_and_counted(self) -> None:
        text = links_ready.notice({"A, 1 Jan": 2, "B, 2 Jan": 1, "": 4})
        self.assertEqual(
            text,
            "📜 2 connections from A, 1 Jan, 1 connection from B, 2 Jan and 4 more connections "
            "are ready to check.",
        )

    def test_nothing_ready_says_nothing(self) -> None:
        self.assertEqual(links_ready.notice({}), "")

    def test_an_upload_name_cannot_ping_or_format(self) -> None:
        text = links_ready.notice({"@everyone **x**, 1 Jan": 1})
        self.assertNotIn("@everyone", text.replace("@​everyone", ""))
        self.assertNotIn("**x**", text)


class Report(unittest.TestCase):
    def test_counts_only_by_kind_and_by_upload_number(self) -> None:
        lines = devtool.report(
            [
                ("kin_of", "Secret file name, 1 Jan", True, 2),
                ("kin_of", "Secret file name, 1 Jan", False, 1),
                ("member_of", "", False, 3),
            ]
        )
        self.assertEqual(lines[:3], ["waiting: 6", "ready to check: 2", "waiting for names: 4"])
        self.assertIn("kind kin_of: 3", lines)
        self.assertIn("kind member_of: 3", lines)
        self.assertIn("upload 2: 2 ready, 1 waiting", lines)
        self.assertIn("no upload label (queued before #2650): 0 ready, 3 waiting", lines)
        self.assertNotIn("Secret", "\n".join(lines))
