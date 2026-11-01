"""The website's name-action door keeps every action it ever took (#2447, found in the review of
the pin migration): a migration that rebuilds the door from older text silently drops what a
migration in between added, so the check is on the migrated database, not on one migration."""

from __future__ import annotations

import re

from tests.test_memory_store import MemoryTest

EVERY_ACTION = (
    "spell", "kind", "add-other", "remove-other", "secret", "hide", "merge", "merge-many",
    "remove", "link-add", "link-words", "link-secret", "link-remove", "link-pin",
    "claim-confirm", "claim-reject", "backstory-keep", "backstory-skip", "backstory-keep-all",
    "backstory-which-pick", "backstory-which-neither",
    "reading-place", "reading-no", "reading-undo", "names-fixed-undo",
    "name", "name-join", "name-undo",  # "Not named yet" on the Names page (#2803)
    "link-who", "alias-who",  # "Who knows this?" (#2999)
)  # fmt: skip


class TheDoorKeepsEveryAction(MemoryTest):
    async def test_the_table_and_the_door_know_every_action(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conname = 'name_actions_action_check'"
                " AND conrelid = 'name_actions'::regclass"
            )
            row = await cur.fetchone()
            assert row is not None
            allowed = set(re.findall(r"'([a-z-]+)'", row["d"]))
            cur = await conn.execute(
                "SELECT string_agg(pg_get_functiondef(p.oid), E'\\n') AS d FROM pg_proc p"
                " WHERE p.oid = ANY (ARRAY(SELECT oid FROM pg_proc q WHERE q.proname ="
                " 'dmbot_web_name_action_add' AND q.pronamespace = current_schema()::regnamespace))"
            )
            door = (await cur.fetchone() or {"d": ""})["d"] or ""
        self.assertEqual(allowed, set(EVERY_ACTION))
        for action in EVERY_ACTION:
            self.assertIn(f"'{action}'", door, action)

    async def test_the_door_still_has_its_pinned_search_path(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT proconfig FROM pg_proc WHERE proname = 'dmbot_web_name_action_add'"
                " AND pronamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertTrue(any(c.startswith("search_path=") for c in row["proconfig"] or []))
