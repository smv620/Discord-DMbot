"""Rules are house rules, not homebrew (#1628): "rule" is no kind, a file's `(rule)` heading is
refused with a plain pointer while the rest of the file loads, old packs and backups still read,
and migration 0111 moves the existing rule entries into the campaign's house rules."""

from __future__ import annotations

import unittest
from typing import Any

from psycopg import errors

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.rules import homebrew_file as hf
from dmbot.rules import pack_content
from dmbot.rules.homebrew import (
    KINDS,
    NOT_HOMEBREW,
    HomebrewError,
    HomebrewSection,
    HomebrewStore,
    clean_kind,
)
from dmbot.rules.house import HouseRuleStore
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest

GUILD, DM = 111, 7


class Kinds(unittest.TestCase):
    def test_every_kind_has_a_label(self) -> None:
        from dmbot.ui.homebrew import KIND_LABELS

        self.assertEqual(set(KIND_LABELS), set(KINDS))

    def test_rule_is_not_a_kind(self) -> None:
        self.assertNotIn("rule", KINDS)
        self.assertEqual(
            KINDS,
            (
                "spell", "monster", "item", "feat", "condition",
                "species", "subclass", "background", "other",
            ),
        )  # fmt: skip

    def test_asking_for_it_points_to_house_rules(self) -> None:
        for raw in ("rule", " Rule "):
            with self.assertRaises(HomebrewError) as raised:
                clean_kind(raw)
            self.assertEqual(str(raised.exception), NOT_HOMEBREW)
        self.assertEqual(
            NOT_HOMEBREW, "Rules go in House rules, not Homebrew: ⚙️ Manage DMbot → House rules."
        )

    def test_the_files_help_no_longer_lists_it(self) -> None:
        self.assertNotIn("rule", hf.HELP.split("Kinds:")[1].split("\n")[0])

    def test_a_file_with_a_rule_heading_loads_the_rest_and_reports_that_one(self) -> None:
        text = (
            "Homebrew: Test\n"
            "## Hollow King (monster)\n\nIt rules the hollow.\n"
            "## Fast Travel (rule)\n\nEveryone moves at double speed.\n"
            "## Lamp (item)\n\nIt glows.\n"
        )
        parsed = hf.parse(text)
        self.assertEqual([e.name for e in parsed.entries], ["Hollow King", "Lamp"])
        [problem] = parsed.problems
        self.assertEqual(problem.why, NOT_HOMEBREW)
        self.assertIn("Fast Travel", problem.text)

    def test_a_pack_made_before_keeps_working_without_its_rules(self) -> None:
        content = {
            "entries": [
                {"kind": "rule", "name": "Fast", "summary": None, "text": "x", "known": True},
                {"kind": "spell", "name": "Zap", "summary": None, "text": "y", "known": False},
            ]
        }
        valid = pack_content.valid_content(content)
        self.assertEqual([e["name"] for e in valid["entries"]], ["Zap"])

    def test_an_old_backup_loads_a_rule_as_other(self) -> None:
        row: dict[str, Any] = {
            "kind": "rule",
            "name": "Fast",
            "text": "Double speed.",
            "summary": None,
            "players_may_know": False,
            "by": "7",
            "created_at": 1,
            "updated_at": 1,
        }
        [checked] = HomebrewSection().check([row])
        self.assertEqual((checked.kind, checked.name), ("other", "Fast"))
        with self.assertRaises(CampaignError):  # (anything else unknown is still refused)
            HomebrewSection().check([{**row, "kind": "dragon"}])


class Migration(DatabaseTest):
    async def test_rule_entries_move_into_house_rules_and_the_rest_is_kept_safe(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0111"]
        await self.db.close()  # start again from a schema as it was before 0111
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db)
        one = await campaigns.create(GUILD, "One", DM)
        two = await campaigns.create(GUILD, "Two", DM)
        long_text = "x" * 495  # with "Name: " in front it is over a house rule's 500
        async with self.db.guild(GUILD) as conn:
            # One already has two house rules (the counter is at 2).
            for n in (1, 2):
                await conn.execute(
                    "INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by,"
                    " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 1, 1)",
                    (GUILD, one.id, n, f"Old rule {n}", DM),
                )
            await conn.execute("UPDATE campaigns SET house_rules_made = 2 WHERE id = %s", (one.id,))
            rows = [  # (campaign, kind, name, text, from a book, players may know)
                (one.id, "rule", "Fast Travel", "Everyone moves at double speed.", False, True),
                (
                    one.id,
                    "rule",
                    "Flanking",
                    "flanking gives advantage when two allies...",
                    False,
                    True,
                ),
                (one.id, "rule", "Booked", "From a shared book.", True, True),
                (one.id, "rule", "Long One", long_text, False, True),
                (one.id, "rule", "Secret Law", "Nobody may know this.", False, False),
                (one.id, "rule", "Two Lines", "First line.\n\n   Second   line.", False, True),
                (one.id, "monster", "Hollow King", "A crowned skeleton.", False, False),
                (two.id, "rule", "Quiet Rest", "A rest is quiet.", False, True),
            ]
            for campaign_id, kind, name, text, book, known in rows:
                await conn.execute(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, created_by, created_at, updated_at, from_book, players_may_know)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s)",
                    (GUILD, campaign_id, kind, name, name.casefold(), text, DM, book, known),
                )
        await self.db.migrate()
        houses = HouseRuleStore(self.db)
        mine = {(r.number, r.rule) for r in await houses.list(GUILD, one.id)}
        self.assertEqual(
            mine,
            {
                (1, "Old rule 1"),
                (2, "Old rule 2"),
                (3, "Fast Travel: Everyone moves at double speed."),
                (5, "Secret Law: Nobody may know this."),  # (hidden no more: part 2)
                (4, "flanking gives advantage when two allies..."),  # (it starts with the name)
                (6, "Two Lines: First line. Second line."),  # (one line, like every house rule)
            },
        )
        theirs = [(r.number, r.rule) for r in await houses.list(GUILD, two.id)]
        self.assertEqual(theirs, [(1, "Quiet Rest: A rest is quiet.")])
        store = HomebrewStore(self.db)
        kept = {(e.name, e.kind) for e in await store.list(GUILD, one.id)}
        self.assertEqual(
            kept,
            {
                ("Booked", "other"),  # from a book
                ("Long One", "other"),  # too long for a house rule
                ("Hollow King", "monster"),
            },
        )
        self.assertEqual(await store.list(GUILD, two.id), [])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, house_rules_made FROM campaigns ORDER BY house_rules_made"
            )
            made = {r["id"]: r["house_rules_made"] for r in await cur.fetchall()}
        self.assertEqual((made[one.id], made[two.id]), (6, 1))
        async with self.db.unscoped() as conn:  # the openings are gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_campaign_near_its_house_rule_cap_moves_only_what_fits(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0111"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        one = await CampaignStore(self.db).create(GUILD, "One", DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by,"
                " created_at, updated_at) SELECT %s, %s, n, 'Rule ' || n, %s, 1, 1"
                " FROM generate_series(1, 199) AS n",
                (GUILD, one.id, DM),
            )
            await conn.execute(
                "UPDATE campaigns SET house_rules_made = 199 WHERE id = %s", (one.id,)
            )
            for n in (1, 2, 3):
                await conn.execute(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, created_by, created_at, updated_at, players_may_know)"
                    " VALUES (%s, %s, 'rule', %s, %s, 'x', %s, 1, 1, true)",
                    (GUILD, one.id, f"R{n}", f"r{n}", DM),
                )
        await self.db.migrate()
        self.assertEqual(len(await HouseRuleStore(self.db).list(GUILD, one.id)), 200)
        left = await HomebrewStore(self.db).list(GUILD, one.id)
        self.assertEqual(sorted((e.name, e.kind) for e in left), [("R2", "other"), ("R3", "other")])

    async def test_the_database_no_longer_takes_the_kind(self) -> None:
        campaigns = CampaignStore(self.db)
        one = await campaigns.create(GUILD, "One", DM)
        async with self.db.guild(GUILD) as conn:
            with self.assertRaises(errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, created_by, added_by, created_at, updated_at)"
                    " VALUES (%s, %s, 'rule', 'X', 'x', 'y', %s, %s, 1, 1)",
                    (GUILD, one.id, DM, DM),
                )
