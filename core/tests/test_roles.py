"""The story roles (#3172, item b): the owner's seeded list as written, the limits on a role a
campaign's reading adds, the stored roles and their codes per campaign, the backup section and
migration 0388's reading of the old reason codes. Invented names; the store tests need Postgres."""

from __future__ import annotations

import unittest
from collections import Counter
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.db import Database, drop_schema
from dmbot.memory import roles
from dmbot.memory.models import CONFIRMED, DM
from dmbot.memory.role_backup import RolesSection
from dmbot.memory.role_store import NewRole, RoleStore
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest

GUILD, OTHER_GUILD, OWNER = 7801, 7802, 1
GOOD = ("smuggler", "smuggler", "Moves goods past the law. " * 5, ("a", "b", "c"), "minion")
GOOD_CODE = {
    "code": GOOD[0], "label": GOOD[1], "description": GOOD[2], "examples": list(GOOD[3]),
    "not_this": GOOD[4], "source": "reading", "created_at": 1,
}  # fmt: skip


class TheSeededList(unittest.TestCase):
    def test_thirty_roles_each_with_its_examples_and_enough_words_to_embed(self) -> None:
        self.assertEqual(len(roles.SEEDED_ROLES), 30)
        for r in roles.SEEDED_ROLES:
            self.assertGreaterEqual(len(r.examples), 3, r.code)
            self.assertGreaterEqual(len(r.embed_text.split()), 60, r.code)
            self.assertTrue(r.not_this, r.code)
            self.assertNotIn(r.not_this, r.embed_text, r.code)  # (never embedded)

    def test_commoner_is_the_default_and_never_stored(self) -> None:
        self.assertIn(roles.COMMONER, roles.SEEDED)
        self.assertNotIn(roles.COMMONER, roles.STORED_SEEDED)
        self.assertEqual(len(roles.STORED_SEEDED), 29)

    def test_the_owner_s_renames_are_in_and_the_old_codes_are_not(self) -> None:
        for code in ("minion", "ruler", "god", "captive", "worker", "tempter"):
            self.assertIn(code, roles.SEEDED)
        for code in ("henchman", "ruler_or_god", "misfit", "slave", "caretaker"):
            self.assertNotIn(code, roles.SEEDED)

    def test_the_migration_seeds_exactly_the_stored_list(self) -> None:
        import importlib

        m = importlib.import_module("dmbot.migrations.0388_importance_roles")
        self.assertEqual(m._SEED_CODES, roles.STORED_SEEDED)


class AnAddedRole(unittest.TestCase):
    def test_a_good_one_passes_and_each_limit_refuses(self) -> None:
        self.assertIsNone(roles.check_added(*GOOD))
        code, label, description, examples, not_this = GOOD
        for args, why in (
            (("Smuggler", label, description, examples, not_this), "bad_code"),
            (("a_b_c_d_e", label, description, examples, not_this), "bad_code"),
            (("mentor", label, description, examples, not_this), "seeded_code"),
            ((code, "", description, examples, not_this), "bad_label"),
            ((code, "12", description, examples, not_this), "bad_label"),
            ((code, label, "x" * 1201, examples, not_this), "bad_description"),
            ((code, label, description, ("a", "b"), not_this), "bad_examples"),
            ((code, label, description, ("a", "b", "x" * 201), not_this), "bad_examples"),
            ((code, label, description, examples, "x" * 241), "bad_not_this"),
        ):
            self.assertEqual(roles.check_added(*args), why, args[:2])


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.store = RoleStore(self.db, now=lambda: 1_000)
        self.c = (await CampaignStore(self.db).create(GUILD, "Rime", OWNER)).id
        self.mira = await self.entry(GUILD, self.c, "Mira Voss")

    async def entry(self, guild: int, campaign: str, name: str) -> str:
        made = await self.memory.add_entity(
            guild, campaign, type="character", name=name, source=DM, status=CONFIRMED
        )
        return str(made.value.id)

    async def held(self, guild: int = GUILD, campaign: str | None = None) -> list[str]:
        got = await self.store.held(guild, campaign or self.c)
        return [f"{r.role}:{r.reason}" for r in got]


class TheStore(Base):
    async def test_a_new_campaign_gets_the_seeded_codes_before_its_first_role(self) -> None:
        self.assertEqual(await self.store.codes(GUILD, self.c), {})
        lost: Counter[str] = Counter()
        await self.store.add(GUILD, self.c, [NewRole(self.mira, "god", "reading", 100, (3,))], lost)
        codes = await self.store.codes(GUILD, self.c)
        self.assertEqual(set(codes), set(roles.STORED_SEEDED))
        self.assertEqual(set(codes.values()), {"seeded"})
        self.assertEqual(await self.held(), ["god:reading"])

    async def test_a_role_held_already_adds_nothing_and_ending_it_keeps_the_history(self) -> None:
        lost: Counter[str] = Counter()
        one = NewRole(self.mira, "spy", "reading")
        self.assertEqual(await self.store.add(GUILD, self.c, [one, one], lost), 1)
        self.assertTrue(await self.store.end(GUILD, self.c, self.mira, "spy"))
        self.assertFalse(await self.store.end(GUILD, self.c, self.mira, "spy"))
        self.assertEqual(await self.held(), [])
        self.assertEqual(await self.store.add(GUILD, self.c, [one], lost), 1)  # (held again)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_roles")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 2)

    async def test_what_is_skipped_is_counted_by_reason(self) -> None:
        lost: Counter[str] = Counter()
        written = await self.store.add(
            GUILD, self.c,
            [NewRole(self.mira, "commoner", "reading"), NewRole(self.mira, "pirate", "reading"),
             NewRole(self.mira, "god", "demoted"), NewRole("f" * 32, "god", "reading")],
            lost,
        )  # fmt: skip
        self.assertEqual(written, 0)
        self.assertEqual(
            lost,
            Counter(role_commoner=1, role_unknown=1, role_bad_reason=1, role_no_entry=1),
        )

    async def test_a_campaign_s_own_role_stays_in_that_campaign(self) -> None:
        self.assertIsNone(await self.store.add_code(GUILD, self.c, *GOOD))
        self.assertIsNone(await self.store.add_code(GUILD, self.c, *GOOD))  # (again: nothing)
        other = (await CampaignStore(self.db).create(OTHER_GUILD, "Elsewhere", OWNER)).id
        lost: Counter[str] = Counter()
        await self.store.add(GUILD, self.c, [NewRole(self.mira, "smuggler", "reading")], lost)
        theirs = await self.entry(OTHER_GUILD, other, "Kesh")
        await self.store.add(OTHER_GUILD, other, [NewRole(theirs, "smuggler", "reading")], lost)
        self.assertEqual(await self.held(), ["smuggler:reading"])
        self.assertEqual(await self.held(OTHER_GUILD, other), [])
        self.assertEqual(lost["role_unknown"], 1)
        self.assertEqual((await self.store.codes(GUILD, self.c))["smuggler"], "reading")

    async def test_no_more_than_forty_added_roles(self) -> None:
        letters = "abcdefghijklmnopqrstuvwxyz"
        for n in range(roles.ADDED_MAX):
            code = f"role_{letters[n // 26]}{letters[n % 26]}"
            self.assertIsNone(await self.store.add_code(GUILD, self.c, code, *GOOD[1:]))
        self.assertEqual(
            await self.store.add_code(GUILD, self.c, "one_more", *GOOD[1:]), "too_many"
        )
        self.assertEqual(await self.store.add_code(GUILD, self.c, "Bad", *GOOD[1:]), "bad_code")

    async def test_two_readings_adding_at_once_never_pass_the_limit(self) -> None:
        import asyncio

        letters = "abcdefghijklmnopqrstuvwxyz"
        for n in range(roles.ADDED_MAX - 1):
            await self.store.add_code(GUILD, self.c, f"role_{letters[n // 26]}{letters[n % 26]}",
                                      *GOOD[1:])  # fmt: skip
        got = await asyncio.gather(
            self.store.add_code(GUILD, self.c, "late_one", *GOOD[1:]),
            self.store.add_code(GUILD, self.c, "late_two", *GOOD[1:]),
        )
        self.assertEqual(sorted(got, key=str), [None, "too_many"])
        codes = await self.store.codes(GUILD, self.c)
        self.assertEqual(sum(v == "reading" for v in codes.values()), roles.ADDED_MAX)


class TheWhy(Base):
    async def test_backed_and_stored_roles_together_and_a_merged_twin_counts(self) -> None:
        tavin = await self.memory.add_entity(
            GUILD, self.c, type="character", name="Tavin", source=DM, status=CONFIRMED,
            role="player_character", played_by=101,
        )  # fmt: skip
        made = await self.memory.add_relation(
            GUILD, self.c, self.mira, "mentor_of", str(tavin.value.id), source=DM,
            confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        twin = await self.entry(GUILD, self.c, "Mira the Old")
        lost: Counter[str] = Counter()
        await self.store.add(GUILD, self.c, [NewRole(twin, "god", "reading", 100)], lost)
        await self.memory.merge(GUILD, self.c, self.mira, twin, source=DM, dm_said_same=True)
        why = await self.store.why(GUILD, self.c, self.mira)
        self.assertEqual([w.role for w in why], ["god", "mentor"])
        god, mentor = why
        self.assertEqual((god.stored_reason, god.session_started_at), ("reading", 100))
        assert made.value is not None
        self.assertEqual(mentor.relation_ids, (made.value[0].id,))
        self.assertEqual(await self.store.why(GUILD, self.c, twin), [])  # (ask the one that stayed)


class TheBackup(Base):
    async def test_a_round_trip_keeps_added_codes_and_open_and_ended_roles(self) -> None:
        await self.store.add_code(GUILD, self.c, *GOOD)
        lost: Counter[str] = Counter()
        await self.store.add(
            GUILD, self.c,
            [NewRole(self.mira, "smuggler", "reading", 100, (4, 5)),
             NewRole(self.mira, "rival", "dm_corrected")],
            lost,
        )  # fmt: skip
        await self.store.end(GUILD, self.c, self.mira, "rival")
        section = RolesSection()
        async with self.db.guild(GUILD) as conn:
            rows = await section.dump(conn, GUILD, self.c)
        self.assertEqual(sum("code" in r for r in rows), len(roles.STORED_SEEDED) + 1)
        self.assertNotIn("lines", str(rows))  # (no line numbers: a restored campaign has none)
        before = await self.rows()
        checked = section.check(rows)
        async with self.db.guild(GUILD) as conn:
            await section.clear(conn, GUILD, self.c)
            await section.load(conn, GUILD, self.c, checked)
        self.assertEqual(await self.held(), ["smuggler:reading"])
        self.assertEqual((await self.store.codes(GUILD, self.c))["smuggler"], "reading")
        self.assertEqual(await self.rows(), before)  # (ids, reasons, sessions and ends kept)

    async def rows(self) -> list[tuple[Any, ...]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, role, reason, session_started_at, recorded_at, ended_at"
                " FROM memory_roles ORDER BY id"
            )
            return [tuple(r.values()) for r in await cur.fetchall()]

    async def test_a_restore_drops_roles_whose_entry_or_code_is_gone(self) -> None:
        role = {
            "id": "a" * 32, "entity_id": self.mira, "role": "god", "reason": "reading",
            "session_started_at": None, "recorded_at": 5, "ended_at": None,
        }  # fmt: skip
        file = [
            {"code": {"code": "god", "label": "god", "description": "", "examples": [],
                      "not_this": "", "source": "seeded", "created_at": 1}},
            {"role": role},
            {"role": {**role, "id": "b" * 32, "entity_id": "f" * 32}},  # (no such entry)
            {"role": {**role, "id": "c" * 32, "role": "pirate"}},  # (no such code)
        ]  # fmt: skip
        section = RolesSection()
        async with self.db.guild(GUILD) as conn:
            await section.load(conn, GUILD, self.c, section.check(file))
        self.assertEqual(await self.held(), ["god:reading"])

    async def test_a_damaged_section_is_refused_whole(self) -> None:
        good_role = {
            "id": "b" * 32, "entity_id": "a" * 32, "role": "god", "reason": "reading",
            "session_started_at": None, "recorded_at": 5, "ended_at": None,
        }  # fmt: skip
        RolesSection().check([{"role": good_role}])
        bad_rows: list[Any] = [
            "not a list",
            [{"role": {**good_role, "reason": "demoted"}}],
            [{"role": {**good_role, "ended_at": 4}}],
            [{"role": {**good_role, "role": "Not A Code"}}],
            [{"role": good_role, "code": {}}],
            [{"code": {"code": "mentor", "label": "x", "description": "d", "examples": ["a"] * 3,
                       "not_this": "", "source": "reading", "created_at": 1}}],  # (seeded code)
            [{"role": {k: v for k, v in good_role.items() if k != "id"}}],
            [{"code": {"code": "pirate", "label": "pirate", "description": "x", "examples": [],
                       "not_this": "", "source": "seeded", "created_at": 1}}],  # (has words)
            [{"code": {**GOOD_CODE, "examples": ["a", "b"]}}],  # (fewer than three examples)
        ]  # fmt: skip
        for rows in bad_rows:
            with self.assertRaises(CampaignError):
                RolesSection().check(rows)

    def test_an_old_seeded_code_is_left_out_and_extra_added_codes_are_trimmed(self) -> None:
        renamed = {"code": "henchman", "label": "henchman", "description": "", "examples": [],
                   "not_this": "", "source": "seeded", "created_at": 1}  # fmt: skip
        many = [{"code": {**GOOD_CODE, "code": f"extra_{chr(97 + n // 26)}{chr(97 + n % 26)}"}}
                for n in range(roles.ADDED_MAX + 1)]  # fmt: skip
        codes, _ = RolesSection().check([{"code": renamed}, *many])
        self.assertNotIn("henchman", [c[0] for c in codes])  # (renamed since: left out)
        self.assertEqual(len(codes), roles.ADDED_MAX)  # (the first 40 kept, not refused)


class TheMigration(DatabaseTest):
    async def test_old_reason_codes_are_read_as_ruled_and_old_campaigns_get_the_codes(
        self,
    ) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0388"]
        await self.db.close()  # a schema as it was before 0388, holding rows
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaign = (await CampaignStore(self.db).create(GUILD, "Rime", OWNER)).id
        made = await MemoryStore(self.db).add_entity(
            GUILD, campaign, type="character", name="Mira", source=DM, status=CONFIRMED
        )
        async with self.db.guild(GUILD) as conn:
            for rank, basis in ((3, "mentor"), (2, "corrected"), (1, "met_a_pc")):
                await conn.execute(
                    "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, basis,"
                    " source, recorded_at) VALUES (%s, %s, %s, %s, %s, 'plotbot', 1)",
                    (GUILD, campaign, made.value.id, rank, basis),
                )
        await self.db.migrate()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT rank, reason FROM memory_importance ORDER BY seq")
            got = [(r["rank"], r["reason"]) for r in await cur.fetchall()]
            cur = await conn.execute("SELECT count(*) AS n FROM memory_role_codes")
            row = await cur.fetchone()
        self.assertEqual(got, [(3, "reading"), (2, "dm_corrected"), (1, "reading")])
        assert row is not None
        self.assertEqual(row["n"], len(roles.STORED_SEEDED))


if __name__ == "__main__":
    unittest.main()
