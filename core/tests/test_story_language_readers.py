"""Every reader of `memory_story_languages` skips the DM's no (#4362; Supervisor's ruling): a row
with `given_by = 'not'` stays so no later read gives the language again, and is never shown,
counted or offered. This pure test lists every file that names the table, with how it handles that
row, so a new reader has to say so; a new migration door gets its entry by name too. It can't see a
caller of `languages_known`: one that uses "known" to decide whether to ask the DM must also read
`languages_said_no`, as the lore upload's `ask_languages` does. No database."""

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
    "memory/trait_offers.py": "a language question closes on the DM's yes or no ('dm' or 'not')",
    "memory/backup.py": "the whole campaign, the no included, so a restore keeps it",
    "memory/_changes.py": "the change log's description of the table (Undo, merges)",
    "memory/states.py": "forgetting a character deletes all its rows, the no included",
    "campaigns/tables.py": "only names the table, on the backup section's list",
    "lineage.py": "only names the table, on the list that carries the build's stamp",
    "migrations/0448_story_languages.py": "creates the table",
    "migrations/0451_language_questions.py": "only names the table in a comment",
    "migrations/0484_language_not.py": "adds 'not' to the allowed values",
    # (on #4378's branch until it merges: its SQL lists only given_by = 'read')
    "migrations/0481_world_notes_door.py": "World notes' website door: only given_by 'read'",
}
NAMES = re.compile(r"memory_story_languages|\bSTORY_LANGUAGES\b")


def _found() -> set[str]:
    return {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if NAMES.search(p.read_text(encoding="utf-8"))
    }


class EveryReaderSkipsTheDmsNo(unittest.TestCase):
    def test_every_file_naming_the_table_says_how_it_handles_the_no(self) -> None:
        unlisted = set(_found()) - set(ALLOWED)
        self.assertEqual(unlisted, set(), "say how this file treats given_by 'not' (#4362)")

    def test_a_listed_file_still_names_the_table(self) -> None:
        # (a listed file not there yet is another PR's, as 0481 is until #4378 merges)
        here = {path for path in ALLOWED if (SRC / path).exists()}
        self.assertEqual(here - _found(), set())

    def test_the_no_is_the_word_the_sql_spells(self) -> None:
        from dmbot.memory.models import GIVEN_BY, LANGUAGE_NOT

        self.assertEqual(LANGUAGE_NOT, "not")  # (the SQL says 'not' as a literal)
        self.assertIn(LANGUAGE_NOT, GIVEN_BY)


if __name__ == "__main__":
    unittest.main()
