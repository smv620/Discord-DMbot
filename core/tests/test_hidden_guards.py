"""Every function that keeps the DM's entries from players by `hidden_from_players` also keeps out
a person the story never named (#2790, migration 0264). Scans every function in the schema as the
database has it, so a later migration that builds a door again from its older text in schema.py
and loses the `unnamed` guard fails here. Positive uses (the function that lists the held names,
the trigger that clears the flag, the change-log readers) are no guard and are left alone."""

from __future__ import annotations

import re

from tests.pg import DatabaseTest

# (pattern of one way of writing "not hidden from players", the same with its unnamed partner)
GUARDS = (
    (
        r"NOT\s+\w+\.hidden_from_players",
        r"NOT\s+(\w+)\.hidden_from_players\s+AND\s+NOT\s+\1\.unnamed",
    ),
    (
        r"\(\s*\w+\.hidden_from_players\s+OR\s+",
        r"\(\s*(\w+)\.hidden_from_players\s+OR\s+\1\.unnamed\s+OR\s+",
    ),
    (
        r"OR\s+\w+\.hidden_from_players\s+OR\s+",
        r"OR\s+(\w+)\.hidden_from_players\s+OR\s+\1\.unnamed",
    ),
)
# `OR x.hidden_from_players)` closes a list: the partner comes before it, so none may remain.
# (Every space is `\s+`: a guard written across a line break or with two spaces is still seen.)
LIST_END = r"OR\s+\w+\.hidden_from_players\s*\)"


def unguarded(source: str) -> list[str]:
    """The ways a function's text says "not hidden from players" without the unnamed partner."""
    out = []
    for guard, paired in GUARDS:
        if len(re.findall(guard, source)) != len(re.findall(paired, source)):
            out.append(guard)
    if re.search(LIST_END, source):
        out.append(LIST_END)
    return out


class EveryPlayerGuard(DatabaseTest):
    async def test_no_function_in_the_schema_hides_entries_from_players_but_not_unnamed_ones(
        self,
    ) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT proname, prosrc FROM pg_proc WHERE prosrc LIKE '%hidden_from_players%'"
                " AND pronamespace = current_schema()::regnamespace ORDER BY proname"
            )
            found = await cur.fetchall()
        self.assertGreaterEqual(len(found), 8)  # the doors this is about are among them
        loose = {r["proname"]: unguarded(r["prosrc"]) for r in found if unguarded(r["prosrc"])}
        self.assertEqual(loose, {}, "a player-facing function lost its unnamed guard (see 0264)")

    def test_the_scan_itself_sees_a_missing_partner(self) -> None:
        self.assertEqual(unguarded("WHERE NOT e.hidden_from_players AND e.x"), [GUARDS[0][0]])
        self.assertEqual(unguarded("AND (a.secret OR e.hidden_from_players)"), [LIST_END])
        self.assertEqual(unguarded("(e.hidden_from_players OR e.audience_limited)"), [GUARDS[1][0]])
        self.assertEqual(
            unguarded("NOT e.hidden_from_players AND NOT e.unnamed AND e.hidden_from_players"), []
        )
        self.assertEqual(unguarded("WHERE e.hidden_from_players AND NOT e.audience_limited"), [])

    def test_a_guard_across_a_line_break_or_with_two_spaces_is_seen_too(self) -> None:
        self.assertEqual(unguarded("WHERE NOT\n   e.hidden_from_players\n AND e.x"), [GUARDS[0][0]])
        self.assertEqual(unguarded("WHERE NOT   e.hidden_from_players AND e.x"), [GUARDS[0][0]])
        self.assertEqual(unguarded("AND (a.secret OR\n  e.hidden_from_players\n)"), [LIST_END])
        self.assertEqual(
            unguarded("(e.hidden_from_players\n   OR e.audience_limited)"), [GUARDS[1][0]]
        )
        self.assertEqual(unguarded("NOT e.hidden_from_players\n  AND NOT\n e.unnamed AND e.y"), [])
