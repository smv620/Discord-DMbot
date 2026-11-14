"""Every reader of `memory_story_languages` skips the DM's no (#4362; Supervisor's ruling): a row
with `given_by = 'not'` stays so no later read gives the language again, and is never shown,
counted or offered. This pure test lists every file that names the table, with how it handles that
row, so a new reader has to say so. A migration whose SQL only reads DMbot's own reads
(`given_by = 'read'`, such as World notes' door) skips it by itself and needs no entry. No
database."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/store.py": "the store: `story_languages` and the languages join skip 'not'; a read "
    "finds the DM's no and writes nothing; the DM's own word turns it back; merges keep the no",
    "memory/language_rows.py": "writes the DM's no (`say_not`), reads only to check the row",
    "memory/world_notes.py": "lists only given_by 'read'; 🚫 writes the no through `say_not`",
    "memory/trait_offers.py": "looks only for the DM's own word ('dm')",
    "memory/backup.py": "the whole campaign, the no included, so a restore keeps it",
    "memory/_changes.py": "the change log's description of the table (Undo, merges)",
    "memory/states.py": "forgetting a character deletes all its rows, the no included",
    "campaigns/tables.py": "only names the table, on the backup section's list",
    "lineage.py": "only names the table, on the list that carries the build's stamp",
    "migrations/0448_story_languages.py": "creates the table",
    "migrations/0451_language_questions.py": "only names the table in a comment",
    "migrations/0484_language_not.py": "adds 'not' to the allowed values",
}
NAMES = re.compile(r"memory_story_languages|\bSTORY_LANGUAGES\b")
ONLY_READS = re.compile(r"given_by\s*=\s*''?read''?")


def _found() -> dict[str, str]:
    return {
        str(p.relative_to(SRC)): text
        for p in SRC.rglob("*.py")
        if NAMES.search(text := p.read_text(encoding="utf-8"))
    }


class EveryReaderSkipsTheDmsNo(unittest.TestCase):
    def test_every_file_naming_the_table_says_how_it_handles_the_no(self) -> None:
        unlisted = {
            path
            for path, text in _found().items()
            if path not in ALLOWED
            and not (path.startswith("migrations/") and ONLY_READS.search(text))
        }
        self.assertEqual(unlisted, set(), "say how this file treats given_by 'not' (#4362)")

    def test_a_listed_file_still_names_the_table(self) -> None:
        self.assertEqual(set(ALLOWED) - set(_found()), set())


if __name__ == "__main__":
    unittest.main()
