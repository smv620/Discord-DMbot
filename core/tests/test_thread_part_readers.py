"""Who reads `memory_thread_parts` (#4471; quests design §10.2 and §13 rule 5): every reader goes
through one function, `thread_parts.parts_for`, which applies what players may see. This list is
reviewed by hand, as `test_map_fact_readers.py` does for a place's facts: a new file that names the
table fails here until someone decides what it is and writes why."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/thread_parts.py": "the one reader (`parts_for`, the players' rule) and the writes",
    "memory/_changes.py": "the change log's description of the table (Undo, merges)",
    "memory/backup.py": "the memory backup section: the DM's whole campaign, never a player's view",
    "campaigns/tables.py": "only names the table, on the backup section's list",
    "lineage.py": "only names the table, on the list that carries the build's stamp (#3739)",
    "migrations/0504_thread_parts.py": "creates the table",
    "migrations/0511_thread_card_door.py": "the DM-only read door for the website card",
    "migrations/0512_thread_sheet_door.py": "the same door, with the sheet (a count of waiting)",
    "migrations/0508_time_limit_told.py": "a foreign key to a limit's first row (#4448)",
    "migrations/0520_thread_progress.py": "a foreign key to the part a progress line moves (#4522)",
    "migrations/0528_thread_progress_web.py": "the website's Progress to check door: the words of "
    "the part a line moves (#4616)",
    "memory/thread_progress.py": "only whether a part a progress line moves still exists, before"
    " offering it (#4522): never a part's words or who it names",
}
NAMES = re.compile(r"memory_thread_parts|\bTHREAD_PARTS\b")


class OneReader(unittest.TestCase):
    def test_only_the_listed_files_touch_the_table(self) -> None:
        found = {
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if NAMES.search(p.read_text(encoding="utf-8"))
        }
        self.assertEqual(
            found - set(ALLOWED),
            set(),
            "a new reader of a thread's parts must go through thread_parts.parts_for (or be added "
            "here with why)",
        )
        self.assertEqual(set(ALLOWED) - found, set(), "a listed file no longer needs to be here")

    def test_the_one_reader_filters_for_players(self) -> None:
        text = (SRC / "memory" / "thread_parts.py").read_text(encoding="utf-8")
        # (writes read inside their own change with `w.select`; any other `.select(THREAD_PARTS`
        # is a reader, and there is one)
        readers = re.findall(
            r"async def (\w+)\((?:(?!\nasync def ).)*?(?<!\bw)\.select\(\s*THREAD_PARTS", text, re.S
        )
        # (and `dated_running`, the DM's own: the time limits the clock is checked against after
        # it moves, for the DM's private messages, never a players' view; quests slice 4b, #4448)
        self.assertEqual(readers, ["dated_running", "parts_for"], "the readers")
        body = text.split("async def parts_for", 1)[1].split("\nasync def ", 1)[0]
        # every part of the players' rule (§10.2): the behaviour is tested against a database in
        # test_thread_parts.py; this keeps a refactor from dropping one
        for rule in (
            "if viewer.dm:",  # only the DM sees everything
            "p.thread_id in threads",  # a thread they may see
            "p.status == CONFIRMED",  # kept by the DM
            "p.table_knows",  # and shown to the table
            "who=p.who_id in known",  # a name they may not see is left blank
            "about=p.about_id in known",
            "under=p.under_id in ids",  # and so is a part it stands under that they may not see
        ):
            self.assertIn(rule, body, rule)


if __name__ == "__main__":
    unittest.main()
