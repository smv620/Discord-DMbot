"""Who writes a kept connection's source (#3742): every writer is a read of the DM's own lore or
backstory and says so, so the graph-improvement poster can trust `own_writing`. A new caller (a
shared adventure's reader) must be added here on purpose, with the kind it really is. Pure: reads
the source files only."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
# Each file that may record a link source, and the kind it must say (a new reader of other
# material is added here with `UPLOAD_UNKNOWN`, on purpose).
ALLOWED = {
    "ui/name_background.py": "UPLOAD_OWN_WRITING",  # the 📜 read, confirmed as own writing
    "memory/backstory_keep.py": "UPLOAD_OWN_WRITING",  # keeps a link the own-writing reads queued
}
STORE = "memory/store.py"


class TheWriters(unittest.TestCase):
    def test_only_the_own_writing_reads_record_a_link_source_and_they_say_so(self) -> None:
        callers: dict[str, list[str]] = {}
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            calls = re.findall(r"record_link_source\((.*?)\)\s*(?:#|\n)", text, re.S)
            name = path.relative_to(SRC).as_posix()
            if calls and name != STORE:
                callers[name] = calls
        self.assertEqual(set(callers), set(ALLOWED))
        for name, calls in callers.items():
            for call in calls:
                self.assertIn(f"kind={ALLOWED[name]}", call.replace(" ", ""), name)

    def test_only_the_store_inserts_link_sources(self) -> None:
        writers = {
            path.relative_to(SRC).as_posix()
            for path in SRC.rglob("*.py")
            if "INSERT INTO memory_link_sources" in path.read_text(encoding="utf-8")
            and "migrations" not in path.parts
        }
        self.assertEqual(writers, {STORE})


if __name__ == "__main__":
    unittest.main()
