"""Who reads `memory_relations` (#2412, slice 2b). A reading is placed on a *proposed* secret
connection when nobody has kept it (`placing.py`), so every reader of connections must either
see only confirmed ones or be a DM-side reader on purpose. This list is reviewed by hand: a new
file that touches the table fails here until someone decides which kind it is and writes why, the
way `test_campaign_move_cascade` makes a new table choose."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# file → how it treats a proposed connection
CONFIRMED_ONLY = "reads confirmed connections only (and, for players, only those the party holds)"
DM_ONLY = "DM-side by design"
READERS = {
    "memory/recall.py": CONFIRMED_ONLY + ": the walk behind NPC pages, table answers and recaps",
    "memory/importance.py": CONFIRMED_ONLY,
    "memory/continuity.py": CONFIRMED_ONLY + ": the counts and the story's world",
    "memory/backstory_links.py": CONFIRMED_ONLY + ": pairs already known",
    "memory/exposures.py": DM_ONLY + ": secret connections said aloud (confirmed ones)",
    "memory/states.py": "live located_in facts, confirmed only since #2412",
    "memory/beliefs.py": "beliefs hang on any connection; every players' reader filters on the "
    "party's belief at 1.0, which a guess never is",
    "memory/placing.py": "writes the proposed connections and their beliefs for the DM",
    "memory/notify.py": DM_ONLY + ": what the DM is told",
    "memory/health.py": DM_ONLY + ": counts for the DM, not shown to players",
    "memory/set_definitions.py": "is a relationship word in use: any status, on purpose",
    "memory/backup.py": "a backup is complete: DM secrets and proposals travel as they are",
    "memory/_changes.py": "the change log and the row writers",
    "memory/store.py": "the store: players' readers filter confirmed (and the party's belief); "
    "the checks and merge are DM-side and judge facts against facts since #2412",
    "campaigns/tables.py": "the list of tables a campaign owns",
    "schema.py": "the doors: the website's connection readers filter confirmed and not secret "
    "for anyone but the DM",
}


class WhoReadsConnections(unittest.TestCase):
    def test_every_file_that_touches_the_table_is_on_the_reviewed_list(self) -> None:
        found = {
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if re.search(r"memory_relations|\bRELATIONS\b", path.read_text())
        }
        self.assertEqual(
            sorted(found),
            sorted(READERS),
            "A file reads or writes connections. If it can show one to players or count one in "
            "something they see, it must filter status = 'confirmed' (a reading's proposed "
            "connection is secret and unproved); otherwise add it here with the reason.",
        )

    def test_the_players_views_count_only_what_the_party_holds(self) -> None:
        schema = (SRC / "schema.py").read_text()
        # the website's connection door: players see confirmed, not secret ones
        door = schema[schema.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships(") :]
        door = door[: door.index("$$ LANGUAGE")] if "$$ LANGUAGE" in door else door
        self.assertIn("status = 'confirmed'", door)


if __name__ == "__main__":
    unittest.main()
