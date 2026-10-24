"""Every character is an NPC or a PC (#2562): a god is an NPC and keeps "god" in its words."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

from dmbot import schema
from dmbot.db import Database, drop_schema
from dmbot.memory import models
from dmbot.memory.ontology import GOD_NOTE, god_is_an_npc, resolve_kind
from tests.pg import TEST_URL, DatabaseTest

GUILD = 7


class Words(unittest.TestCase):
    def test_a_character_is_an_npc_or_a_pc_and_nothing_else(self) -> None:
        self.assertEqual(models.ROLES, ("player_character", "npc"))
        self.assertEqual(models.ROLE_LABELS, {"player_character": "PC", "npc": "NPC"})

    def test_the_older_kinds_are_npcs(self) -> None:
        for older in ("deity", "creature", "npc"):
            self.assertEqual(resolve_kind(older), ("character", "npc"))
        self.assertEqual(resolve_kind("player_character"), ("character", "player_character"))

    def test_a_god_keeps_the_word_in_its_description(self) -> None:
        self.assertEqual(god_is_an_npc("Queen of the frost."), f"Queen of the frost. {GOD_NOTE}")
        self.assertEqual(god_is_an_npc(""), GOD_NOTE)
        self.assertEqual(god_is_an_npc("The goddess of frost."), "The goddess of frost.")
        long = "x" * 300
        self.assertLessEqual(len(god_is_an_npc(long)), models.DESCRIPTION_MAX)
        self.assertTrue(god_is_an_npc(long).endswith(GOD_NOTE))


class TheMigration(DatabaseTest):
    async def test_gods_become_npcs_with_their_snapshots_and_the_role_check_is_two_values(
        self,
    ) -> None:
        before = [m for m in schema.MIGRATIONS if m[0] < "0222"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        from dmbot.campaigns import CampaignStore

        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", 11)
        async with self.db.guild(GUILD) as conn:
            for n, (role, text) in enumerate(
                (("god", "Queen of frost."), ("god", "The goddess of frost."), ("npc", "A cook."))
            ):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, role)"
                    " VALUES (%s, %s, %s, 'character', %s, %s, 'confirmed', 'dm', 1, %s)",
                    (GUILD, campaign.id, f"{n:032x}", f"Name{n}", text, role),
                )
            snapshot = {"id": f"{0:032x}", "type": "character", "role": "god", "description": "x"}
            await conn.execute(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, before, after, source, made_at)"
                " VALUES (%s, %s, 1, 1, 'memory_entities', %s, 'update', %s, %s, 'dm', 1)",
                (GUILD, campaign.id, f"{0:032x}", json.dumps(snapshot), json.dumps(snapshot)),
            )
        await self.db.migrate()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT name, role, description FROM memory_entities WHERE guild_id = %s"
                " ORDER BY name",
                (GUILD,),
            )
            rows = {r["name"]: (r["role"], r["description"]) for r in await cur.fetchall()}
            cur = await conn.execute("SELECT before, after FROM memory_changes")
            log = await cur.fetchone()
        self.assertEqual(rows["Name0"], ("npc", "Queen of frost. A god."))
        self.assertEqual(rows["Name1"], ("npc", "The goddess of frost."))  # already says so
        self.assertEqual(rows["Name2"], ("npc", "A cook."))
        assert log is not None
        for side in (log["before"], log["after"]):
            self.assertEqual((side["role"], side["description"]), ("npc", "x A god."))
        async with self.db.guild(GUILD) as conn:
            with self.assertRaises(Exception):  # noqa: B017 (the CHECK refuses any other role)
                await conn.execute(
                    "UPDATE memory_entities SET role = 'god' WHERE guild_id = %s", (GUILD,)
                )
        async with self.db.unscoped() as conn:  # the openings are gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND tablename IN ('memory_entities', 'memory_changes')"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)


class NoPersonWordForACharacter(unittest.TestCase):
    """Words a DM reads say "character", never "person", for someone in the story (#2562): "person"
    is a table kind (a player or the DM), so it is not the word for an NPC."""

    FILES = (
        "memory/ontology.py",
        "memory/name_list.py",
        "memory/book_links.py",
        "ui/book_link.py",
    )
    # Exact strings that are not words for the reader: the kind key, the plain-English rows that
    # speak of a real person at the table, a Discord profile.
    ALLOWED = ("A real person at the table", "the person running the game", "person's profile")

    def test_the_words_a_dm_reads_do_not_call_a_character_a_person(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1] / "src" / "dmbot"
        found: list[str] = []
        for name in self.FILES:
            tree = ast.parse((root / name).read_text())
            docs = {
                id(n.body[0].value)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                text = node.value
                if id(node) in docs or text == "person" or "SELECT" in text or "type " in text:
                    continue
                if re.search(r"\bperson\b", text) and not any(a in text for a in self.ALLOWED):
                    found.append(f"{name}:{node.lineno}: {text[:60]}")
        self.assertEqual(found, [])
