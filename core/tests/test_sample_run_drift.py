"""The sample run writes a lore upload and an imported session with its own INSERTs, copying what
the website's doors do (#3550). If a later migration changes a door, a column it writes that the
sample run does not (or the other way round) fails here, naming the door, so the sample run never
goes on testing something real uploads no longer do."""

from __future__ import annotations

import re
from pathlib import Path

from dmbot.devtools.sample_run import steps
from tests.pg import DatabaseTest

INSERT = re.compile(r"INSERT INTO\s+(\w+)\s*\(([^)]*)\)", re.IGNORECASE)

# What each door writes that the sample run deliberately does not, and why:
# - the lore door also takes a link and a speaker, both empty for a file upload;
# - the import door also records each speaker's consent line (`import_consents`); the sample run
#   does not fake one, it needs the test accounts to have agreed in Discord (see steps.py).
LEFT_OUT = {
    ("dmbot_web_lore_add", "lore_uploads"): {"link", "speaker"},
}
NOT_WRITTEN = {("dmbot_import_store", "import_consents")}
DOORS = {
    "lore_uploads": "dmbot_web_lore_add",
    "transcript_sessions": "dmbot_import_store",
    "transcript_lines": "dmbot_import_store",
    "session_imports": "dmbot_import_store",
}


STEPS_SOURCE = Path(steps.__file__).read_text(encoding="utf-8")


def columns(text: str) -> dict[str, set[str]]:
    """Table -> its INSERT's columns (the sample run's SQL is split over quoted pieces)."""
    return {
        table: {c.strip() for c in re.sub(r'["\s]+', " ", cols).split(",") if c.strip()}
        for table, cols in INSERT.findall(text)
    }


class TheSampleRunKeepsUpWithTheDoors(DatabaseTest):
    async def door(self, name: str) -> str:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace AND p.proname = %s",
                (name,),
            )
            row = await cur.fetchone()
        assert row is not None, name
        return str(row["d"])

    async def test_each_insert_writes_the_columns_its_door_writes(self) -> None:
        # (`web_sessions` is the throwaway website session the off-Discord import signs in with,
        # #3690: no door writes it for the import, so it is not one of the mimicked tables)
        mine = {t: c for t, c in columns(STEPS_SOURCE).items() if t != "web_sessions"}
        self.assertEqual(set(mine), set(DOORS))  # (a table added or dropped here is looked at)
        for table, door in DOORS.items():
            theirs = columns(await self.door(door))
            self.assertIn(table, theirs, f"{door} no longer writes {table}")
            expected = theirs[table] - LEFT_OUT.get((door, table), set())
            self.assertEqual(mine[table], expected, f"{table} differs from {door}")

    async def test_the_import_door_writes_nothing_new_the_sample_run_does_not_know(self) -> None:
        written = set(columns(await self.door("dmbot_import_store")))
        known = {t for t, d in DOORS.items() if d == "dmbot_import_store"}
        self.assertEqual(written - known, {t for d, t in NOT_WRITTEN if d == "dmbot_import_store"})
