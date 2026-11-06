"""Who may read the relationship reader's ties (#3244 slice 6, Supervisor's condition 1): nothing
but dev1's comparison. `rel_ties` is working data for judging the new reader against the old one;
a list, card, graph, recap, table answer or website door that read it would show unreviewed ties.
A file that mentions the table or calls `RelStore` and is not on this list fails here until
someone decides which kind it is and writes why. Storing real connections from these ties is
stage 2, a separate decision."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
MENTION = re.compile(r"\brel_ties\b|\bRelStore\b|\brel_store\b")

OWNS = "owns the table: its schema, its store, or the list of tables"
WRITES = "writes the ties (the run) or erases them with the words they came from"

# path (under src/dmbot) → (kind, why). Add a reader of the table only with Supervisor's yes.
FILES: dict[str, tuple[str, str]] = {
    "campaigns/tables.py": (OWNS, "the 'not in a backup' list"),
    "migrations/0345_rel_ties.py": (OWNS, "the table and the triggers that erase it"),
    "transcript/rel_run.py": (WRITES, "saves the ties through RelStore.replace, reads none"),
    "transcript/rel_store.py": (OWNS, "the store; `read` is the comparison's only door"),
    "transcript/store.py": (WRITES, "delete_person / remove_speaker / retention erase the ties"),
}


class NothingReadsTheTies(unittest.TestCase):
    def mentions(self) -> set[str]:
        found = set()
        for path in SRC.rglob("*.py"):
            if MENTION.search(path.read_text(encoding="utf-8")):
                found.add(path.relative_to(SRC).as_posix())
        return found

    def test_only_the_listed_files_mention_the_ties(self) -> None:
        extra = self.mentions() - set(FILES)
        self.assertFalse(extra, f"new file(s) touch rel_ties; review them: {sorted(extra)}")

    def test_the_list_only_names_files_that_still_mention_them(self) -> None:
        gone = set(FILES) - self.mentions()
        self.assertFalse(gone, f"remove from the list: {sorted(gone)}")

    def test_only_the_listed_files_name_the_store_class(self) -> None:
        # (the class is the only door: `RelStore(...).read` or `store.read` both need its name)
        named = [
            p.relative_to(SRC).as_posix()
            for p in SRC.rglob("*.py")
            if "RelStore" in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(sorted(named), ["transcript/rel_run.py", "transcript/rel_store.py"])
