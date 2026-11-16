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


class Grants(DatabaseTest):
    async def test_a_website_door_is_granted_to_nobody_but_its_owner_and_the_websites_role(
        self,
    ) -> None:
        """#4619: dev1 saw `dmbot` among a door's grants on the server. `dmbot` is the role that
        runs the migrations, so it OWNS every function, and an owner's own entry is how the catalog
        shows the owner's right once PUBLIC is revoked: it is not a grant to take back (the owner
        can always re-grant itself), and the bot calls a few `dmbot_web_*` helpers itself
        (`dmbot_web_lore_add`, `_scenes`, `_readings`, the move codes). What would be wrong is a
        grant to any other role, to PUBLIC, or a default-privileges grant: this test reads every
        `dmbot_web_*` function's ACL and fails on any grantee but its owner and the website's
        role."""
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT p.proname AS name, pg_get_userbyid(p.proowner) AS owner,"
                " CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END"
                " AS grantee, a.privilege_type AS privilege"
                " FROM pg_proc p CROSS JOIN LATERAL aclexplode("
                "   COALESCE(p.proacl, acldefault('f', p.proowner))) a"
                " WHERE p.pronamespace = current_schema()::regnamespace"
                " AND p.proname LIKE 'dmbot\\_web\\_%'"
            )
            rows = [dict(r) for r in await cur.fetchall()]
        self.assertTrue(rows, "no website door found: the query is wrong")
        stray = sorted(
            {
                (str(r["name"]), str(r["grantee"]))
                for r in rows
                if r["grantee"] not in (r["owner"], "dmbot_web")
            }
        )
        self.assertEqual(stray, [], "a website door is granted to a role it should not be")
