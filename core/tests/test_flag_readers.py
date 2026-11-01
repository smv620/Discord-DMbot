"""Who still reads the old flags (#2995, slice 3): `hidden_from_players`, `audience_limited` and
`unseen_by_players`. Who knows what is kept per character (`memory_knowers`) and the one gate
(`memory.gate`) says what a viewer may know; the flags are only a copy that the writers keep in step
until the migration that removes them (STORY_MEMORY §9b). A new reader of a flag would be a second
rule for "who may see this" beside the gate, so a file that mentions one and is not on this list
fails here until someone decides which kind it is and writes why, the way `test_relation_readers`
does for connections.

The list is meant to shrink: converting a reader to the gate removes its line, and the test
fails for a line whose file no longer mentions a flag, so the list can only get shorter. Owners:
CloudDev-lead takes the Discord readers (`ui/`, `recaps/`, `bot.py`), GraphEngineer takes `memory/`
(agreed on #2995)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
FLAG = re.compile(r"\b(?:hidden_from_players|audience_limited|unseen_by_players)\b")

# What a mention is, so a reader cannot hide among the writers.
DEFINES = "defines or carries the flag: the model, the table, a backup, a row default"
WRITES = "writes the flag (and the list beside it, so they stay one rule); goes with the flags"
MIRRORS = "keeps the list and the flag in step (slice 2b); goes with the flags"
READS = "READS the flag to decide who may see something: to be converted to the gate"

# path (under src/dmbot) → (kind, owner, why). One line each, in alphabetical order.
FILES: dict[str, tuple[str, str, str]] = {
    "bot.py": (READS, "CloudDev-lead", "the clue reveal reads `unseen_by_players` from the lookup"),
    "memory/_changes.py": (WRITES, "GraphEngineer", "the change log's row writers and Undo"),
    "memory/backstory_keep.py": (READS, "GraphEngineer", "which entries a backstory may link"),
    "memory/backstory_links.py": (READS, "GraphEngineer", "hides a connection with a limited end"),
    "memory/backup.py": (DEFINES, "GraphEngineer", "a backup carries the flags as they are"),
    "memory/book_links.py": (READS, "GraphEngineer", "which entries the book may link"),
    "memory/continuity.py": (READS, "GraphEngineer", "which clues a heads-up may name"),
    "memory/gate.py": (READS, "GraphEngineer", "`_public_entry`: the entries a player may see"),
    "memory/knowers.py": (MIRRORS, "GraphEngineer", "follow_flags and the 'is it shown' checks"),
    "memory/lookup.py": (READS, "GraphEngineer", "the lookup's own secret-name rule"),
    "memory/meaning.py": (READS, "GraphEngineer", "meaning search leaves out unseen entries"),
    "memory/models.py": (DEFINES, "GraphEngineer", "the Entity fields and `unseen_by_players`"),
    "memory/party.py": (DEFINES, "GraphEngineer", "the party faction's row"),
    "memory/recall.py": (READS, "GraphEngineer", "recall leaves out unseen entries"),
    "memory/revealed_aloud.py": (READS, "GraphEngineer", "names said aloud that were hidden"),
    "memory/states.py": (WRITES, "GraphEngineer", "clears `audience_limited` on a change of kind"),
    "memory/store.py": (
        WRITES,
        "GraphEngineer",
        "the writers: add_entity, set_hidden_from_players",
    ),
    "schema.py": (DEFINES, "GraphEngineer", "the lookup's SQL view over the aliases"),
    "ui/clue_audience.py": (WRITES, "CloudDev-lead", "the DM's audience menu for a clue"),
    "ui/name_card.py": (READS, "CloudDev-lead", "the card's hidden line, audience line, toggle"),
    "ui/name_lists.py": (READS, "CloudDev-lead", "a list's secret-name rule"),
    "ui/name_unnamed.py": (DEFINES, "CloudDev-lead", "a docstring naming `unseen_by_players`"),
    "ui/names.py": (READS, "CloudDev-lead", "the saved-name text says 'hidden'"),
    "ui/threads.py": (WRITES, "CloudDev-lead", "makes a thread the DM typed start hidden"),
}


def mentions() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("migrations/"):
            continue  # a migration is history: it moves the flags into the lists
        if FLAG.search(path.read_text(encoding="utf-8")):
            found.add(relative)
    return found


class TheFlagReaders(unittest.TestCase):
    def test_every_file_that_mentions_an_old_flag_is_on_the_list(self) -> None:
        unlisted = sorted(mentions() - FILES.keys())
        self.assertEqual(
            unlisted,
            [],
            "These files mention hidden_from_players, audience_limited or unseen_by_players. "
            "Ask the gate (memory.gate, memory_knowers) who may see something instead of the "
            "flag, or add the file to FILES with its kind and why (#2995).",
        )

    def test_a_file_that_no_longer_mentions_a_flag_leaves_the_list(self) -> None:
        stale = sorted(FILES.keys() - mentions())
        self.assertEqual(stale, [], "Converted: remove these lines from FILES.")

    def test_the_list_is_in_order_and_each_line_says_why(self) -> None:
        self.assertEqual(list(FILES), sorted(FILES))
        kinds = {DEFINES, WRITES, MIRRORS, READS}
        for path, (kind, owner, why) in FILES.items():
            self.assertIn(kind, kinds, path)
            self.assertIn(owner, ("CloudDev-lead", "GraphEngineer"), path)
            self.assertGreater(len(why), 10, path)

    def test_the_readers_still_to_convert_are_counted_for_the_record(self) -> None:
        # (a number to watch go down, not a limit: the slice is done when it reaches zero)
        reading = sorted(path for path, (kind, _, _) in FILES.items() if kind == READS)
        self.assertEqual(len(reading), 13, reading)
