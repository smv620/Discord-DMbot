"""Who may read the story-thread suggestions (#4453; Supervisor's ruling): only the store module
and the migration that makes the table. A suggestion holds the DM's lore quotes and is never shown
to players, never counted as a name and never read by a names list; any other reader has to be
added here, with its reason, so it is decided on purpose."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/thread_suggestions.py": "the one reader and writer (the DM's card and the upload)",
    "migrations/0502_thread_suggestions.py": "creates the table",
    "campaigns/tables.py": "only names the table: a wait, left out of backups",
    "lineage.py": "only names the table, on the list of what is never stamped",
}
# The table's name inside any string literal (SQL split over lines included); a module path
# ("dmbot.memory.thread_suggestions") or a file name is no read of the table
NAMES = re.compile(r"""["'][^"'\n]*(?<![.\w])thread_suggestions(?![\w.])[^"'\n]*["']""")


class OneReader(unittest.TestCase):
    def test_only_the_listed_files_touch_the_table(self) -> None:
        found = {
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if NAMES.search(p.read_text(encoding="utf-8"))
        }
        self.assertEqual(found - set(ALLOWED), set(), "a new reader of thread_suggestions")
        self.assertEqual(set(ALLOWED) - found, set(), "a listed file no longer names the table")
