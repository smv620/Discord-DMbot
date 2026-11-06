"""What the database lets a role run (#3107): no SECURITY DEFINER function is open to every role,
every one pins its search path, and nothing that reads who knows a statement forgets the rows a
hide put to sleep. Read from the catalog of a migrated schema, so a future helper that forgets is
caught the day it is written."""

from __future__ import annotations

from typing import Any

from tests.pg import DatabaseTest

# SECURITY DEFINER functions PUBLIC may run, each with its reason. Empty: every one of ours is a
# door granted by name to the website's role (db.WEB_ROLE_FUNCTIONS) or called from inside one.
OPEN_TO_PUBLIC: dict[str, str] = {}


class Catalog(DatabaseTest):
    async def definers(self) -> list[dict[str, Any]]:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT p.proname AS name, pg_get_function_identity_arguments(p.oid) AS args,"
                " p.proconfig AS config, has_function_privilege('public', p.oid, 'EXECUTE')"
                " AS public_exec FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace AND p.prosecdef"
                " ORDER BY p.proname"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_no_security_definer_function_is_open_to_every_role(self) -> None:
        open_ones = [d["name"] for d in await self.definers() if d["public_exec"]]
        self.assertEqual(sorted(set(open_ones) - set(OPEN_TO_PUBLIC)), [])

    async def test_every_security_definer_function_pins_its_search_path(self) -> None:
        loose = [
            d["name"]
            for d in await self.definers()
            if not any(str(c).startswith("search_path=") for c in d["config"] or [])
        ]
        self.assertEqual(loose, [])

    async def test_there_are_definers_to_check(self) -> None:
        names = {d["name"] for d in await self.definers()}
        for expected in (
            "dmbot_web_graph_source",
            "dmbot_web_knowers_data",
            "dmbot_web_backup_add",
            "dmbot_web_users_id",
        ):
            self.assertIn(expected, names)

    async def test_every_reader_of_who_knows_leaves_out_the_rows_asleep(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT p.proname AS name, p.prosrc AS src FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace"
                " AND strpos(p.prosrc, 'memory_knowers') > 0 AND p.prokind = 'f'"
            )
            found = [dict(r) for r in await cur.fetchall()]
        self.assertTrue(found)
        forgot = [f["name"] for f in found if "hidden_by" not in f["src"]]
        self.assertEqual(forgot, [])

    async def test_the_websites_role_cannot_call_a_helper_but_the_doors_still_work(self) -> None:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT has_function_privilege('dmbot_web',"
                " 'dmbot_web_peek_note(bigint, text, text)', 'EXECUTE') AS helper,"
                " has_function_privilege('dmbot_web',"
                " 'dmbot_web_relationships(bigint, text, text, int, int, boolean)',"
                " 'EXECUTE') AS door"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["helper"])
        self.assertTrue(row["door"])
