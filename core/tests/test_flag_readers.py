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
(agreed on #2995).

`SWITCH` (Supervisor's ruling K on #4537, slice 5c) is the flag read as what it is now: the DM's
"shown to the table now" switch (`docs/design/party_knowledge.md` §9 rule 1), on a path whose
output only the DM gets. It is not a way out of the shrinking list: if a `SWITCH` file later feeds
a player, a table answer, a recap or a page, its line becomes READS again and moves to the gate."""

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
COUNTS = "counts it for a report to dev1: decides nothing anyone sees, prints numbers only"
SWITCH = "the DM's 'shown to the table now' switch, on a path whose output only the DM gets"

# path (under src/dmbot) → (kind, owner, why). One line each, in alphabetical order.
FILES: dict[str, tuple[str, str, str]] = {
    "devtools/sample_run/counts.py": (
        COUNTS,
        "GraphEngineer",
        "the sample run's split-party counts (#3545)",
    ),
    "memory/_changes.py": (WRITES, "GraphEngineer", "the change log's row writers and Undo"),
    "memory/backstory_links.py": (
        SWITCH,
        "GraphEngineer",
        "`waiting`: a limited clue's end marks the DM's card secret (`Waiting.clue_end`: no "
        "👥 Some know, no hand-over, a secret name) and keeps a kept row unrevealed (failing "
        "closed); the row's secret bit is its own (`own_secret`), its view the gate's (#4823)",
    ),
    "memory/backup.py": (DEFINES, "GraphEngineer", "a backup carries the flags as they are"),
    "memory/book_links.py": (
        SWITCH,
        "GraphEngineer",
        "`suggestions`: which entries the book may link, offered to the DM only",
    ),
    "memory/continuity.py": (
        SWITCH,
        "GraphEngineer",
        "`_world`: which clues the DM's private heads-up may name",
    ),
    "memory/gate.py": (
        DEFINES,
        "GraphEngineer",
        "rule 1's switch inside the gate: the common rows while shown, the group mask, and "
        "`_public_entry` (the main name `name_entries` gives a known entry when the viewer "
        "holds no name row); no caller outside the gate reads it (ruling L, 5c)",
    ),
    "memory/knowers.py": (MIRRORS, "GraphEngineer", "follow_flags and the 'is it shown' checks"),
    "memory/lookup.py": (
        DEFINES,
        "GraphEngineer",
        "the name rows carry copies, read by nobody here to decide who sees what (#4802): "
        "`NameEntry.secret` is the Cleaner's SWITCH (ruling Q), and `entry_hidden` the recap's "
        "fallback when no party could be read, fail-stricter (ruling Q3: it only ever hides "
        "more); `for_viewer` takes the gate's `seen` and the name pass the gate's table view",
    ),
    "memory/models.py": (DEFINES, "GraphEngineer", "the Entity fields and `unseen_by_players`"),
    "memory/party.py": (DEFINES, "GraphEngineer", "the party faction's row"),
    "memory/public_guard.py": (
        WRITES,
        "GraphEngineer",
        "which flags take an item from the players, held unless the DM chose it (#4524)",
    ),
    "memory/revealed_aloud.py": (
        SWITCH,
        "GraphEngineer",
        "`unrevealed_names`: which hidden names were said aloud, so `reveal_said` turns the switch "
        "on for them (the DM is told, with Undo); it reads and flips the switch itself",
    ),
    "memory/states.py": (WRITES, "GraphEngineer", "clears `audience_limited` on a change of kind"),
    "memory/store.py": (
        WRITES,
        "GraphEngineer",
        "the writers: add_entity, set_hidden_from_players; and `_shown_when_said`, which "
        "reads them for what `reveal_said` shows and `ask_world_reveals` asks (#4590)",
    ),
    "schema.py": (DEFINES, "GraphEngineer", "the lookup's SQL view over the aliases"),
    "ui/clue_audience.py": (WRITES, "CloudDev-lead", "the DM's audience menu for a clue"),
    "ui/name_card.py": (
        WRITES,
        "CloudDev-lead",
        "the DM's switch: the card's 'hidden from the players' line and toggle, DM only; what a "
        "player or the table holds is the gate's (#4804)",
    ),
    "ui/name_unnamed.py": (DEFINES, "CloudDev-lead", "a docstring naming `unseen_by_players`"),
    "ui/names.py": (
        WRITES,
        "CloudDev-lead",
        "save_name passes the DM's hidden choice to add_entity",
    ),
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
        kinds = {DEFINES, WRITES, MIRRORS, READS, COUNTS, SWITCH}
        for path, (kind, owner, why) in FILES.items():
            self.assertIn(kind, kinds, path)
            self.assertIn(owner, ("CloudDev-lead", "GraphEngineer"), path)
            self.assertGreater(len(why), 10, path)

    def test_the_readers_still_to_convert_are_counted_for_the_record(self) -> None:
        # (the slice is done: nothing reads a flag to decide who may see something, #4802; a new
        # line of that kind fails here, as a second rule beside the gate)
        reading = sorted(path for path, (kind, _, _) in FILES.items() if kind == READS)
        self.assertEqual(reading, [])
