"""Every person the story never named is called "unnamed - <description>" (#3736): the name is built
in one place, a chain reads once, two that would read the same are told apart by when they were
first seen, and migration 0378 rewrites the stored copies of the old "(name unknown)" labels. The
migration runs on tables that hold rows, held back until they are written (CLAUDE.md, #704).
Invented names."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import unnamed
from dmbot.memory.models import CONFIRMED, name_key
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4346, 99
NAME = "0378_unnamed_names"
SEVEN_NOV = int(datetime(2026, 11, 7, 19, 5, tzinfo=UTC).timestamp())


class TheName(unittest.TestCase):
    def test_the_form(self) -> None:
        self.assertEqual(unnamed.label("sister", "Ken"), "unnamed - Ken's sister")
        self.assertEqual(
            unnamed.label("barmaid at Three Flags Sailing with red hair"),
            "unnamed - barmaid at Three Flags Sailing with red hair",
        )

    def test_a_chain_reads_once(self) -> None:
        sister = unnamed.label("sister", "Ken")
        self.assertEqual(unnamed.label("son", sister), "unnamed - Ken's sister's son")

    def test_telling_one_apart(self) -> None:
        self.assertTrue(unnamed.is_label("unnamed - Ken's sister"))
        self.assertFalse(unnamed.is_label("Ken's sister"))
        self.assertEqual(unnamed.description("unnamed - Ken's sister"), "Ken's sister")
        self.assertEqual(unnamed.description("Ken"), "Ken")
        self.assertEqual(unnamed.SOMEONE, "unnamed - someone")

    def test_the_stored_copy_keeps_its_prefix_when_cut(self) -> None:
        long = unnamed.stored("x" * 120, "Ken")
        self.assertTrue(long.startswith("unnamed - Ken's "))
        self.assertLessEqual(len(long), unnamed.STORED_MAX)


class Unique(unittest.TestCase):
    def test_a_free_name_is_kept_as_it_is(self) -> None:
        taken = {name_key("unnamed - a hooded figure")}
        self.assertEqual(unnamed.unique_what("sister", "Ken", taken, SEVEN_NOV), "sister")

    def test_a_taken_one_gets_the_session_s_date(self) -> None:
        taken = {name_key("unnamed - a hooded figure")}
        self.assertEqual(
            unnamed.unique_what("a hooded figure", None, taken, SEVEN_NOV),
            "a hooded figure (first seen 7 Nov 2026)",
        )

    def test_then_the_date_and_time(self) -> None:
        taken = {
            name_key("unnamed - a hooded figure"),
            name_key("unnamed - a hooded figure (first seen 7 Nov 2026)"),
        }
        self.assertEqual(
            unnamed.unique_what("a hooded figure", None, taken, SEVEN_NOV),
            "a hooded figure (first seen 7 Nov 2026 19:05)",
        )

    def test_the_tag_survives_the_stored_cut(self) -> None:
        word = "w" * unnamed.MAX_WHAT
        for anchor in (None, "Ken"):
            taken = {name_key(unnamed.stored(word, anchor))}
            got = unnamed.unique_what(word, anchor, taken, SEVEN_NOV)
            with self.subTest(anchor=anchor):
                self.assertTrue(unnamed.stored(got, anchor).endswith("(first seen 7 Nov 2026)"))
                self.assertNotIn(name_key(unnamed.stored(got, anchor)), taken)

    def test_the_tag_never_decides_who_is_who(self) -> None:
        self.assertEqual(
            unnamed.same_person_key("a hooded figure (first seen 7 Nov 2026)", None),
            unnamed.same_person_key("a hooded figure", None),
        )

    def test_an_old_label_is_brought_forward(self) -> None:
        self.assertEqual(
            unnamed.from_old_label("Ken's sister (name unknown)'s son (name unknown)"),
            "unnamed - Ken's sister's son",
        )
        self.assertTrue(unnamed.is_old_label("a hooded figure (name unknown)"))

    def test_the_words_stay_inside_the_column(self) -> None:
        word = "w" * unnamed.MAX_WHAT
        got = unnamed.unique_what(word, None, {name_key(unnamed.stored(word))}, SEVEN_NOV)
        self.assertLessEqual(len(got), unnamed.MAX_WHAT)
        self.assertTrue(got.endswith("(first seen 7 Nov 2026)"))


class WithOldRows(unittest.IsolatedAsyncioTestCase):
    """A campaign with Ken, migrated up to just before 0378."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL,
            schema=self.schema,
            max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != NAME),
        )
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)).id
        made = await self.memory.add_entity(
            GUILD, self.c, type="npc", name="Ken", source="dm", status=CONFIRMED
        )
        self.ken = str(made.value.id)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def old(self, what: str, old_name: str, *, anchor: str | None = None, at: int) -> str:
        """An unnamed person as the old code stored them: "<…> (name unknown)"."""
        made = await self.memory.add_unnamed(GUILD, self.c, what=what, anchor_id=anchor, read={})
        eid = str(made.entity.id)
        await self.sql(
            "UPDATE memory_entities SET name = %s, created_at = %s WHERE id = %s",
            old_name,
            at,
            eid,
        )
        return eid

    async def sql(self, query: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(query, args)
            return list(await cur.fetchall()) if cur.description else []

    async def name_of(self, eid: str) -> tuple[str, str]:
        (row,) = await self.sql("SELECT name, unnamed_what FROM memory_entities WHERE id = %s", eid)
        return str(row["name"]), str(row["unnamed_what"])


class NameMigration(WithOldRows):
    async def alias(self, entity: str, text: str, alias_id: str) -> None:
        """An other name on `entity`, as a join left it, with the key the old code gave it."""
        await self.sql(
            "INSERT INTO memory_aliases (id, guild_id, campaign_id, entity_id, text, key, kind,"
            " used_by, secret, status, sound_codes, source, created_at)"
            " SELECT %s, guild_id, campaign_id, %s, %s, %s, kind, used_by, secret, status,"
            " sound_codes, source, created_at FROM memory_aliases WHERE entity_id = %s LIMIT 1",
            alias_id, entity, text, name_key(text), self.ken,
        )  # fmt: skip

    async def merged_into(self, eid: str, into: str) -> None:
        await self.sql(
            "UPDATE memory_entities SET status = 'merged', merged_into = %s WHERE id = %s",
            into,
            eid,
        )

    async def alias_row(self, alias_id: str) -> dict[str, Any]:
        (row,) = await self.sql(
            "SELECT text, key, sound_codes FROM memory_aliases WHERE id = %s", alias_id
        )
        return row

    async def test_the_stored_names_and_a_joined_person_s_other_names(self) -> None:
        sister = await self.old("sister", "Ken's sister (name unknown)", anchor=self.ken, at=1)
        son = await self.old(
            "son", "Ken's sister (name unknown)'s son (name unknown)", anchor=sister, at=2
        )
        cut = "barmaid at the Three Flags Sailing (name unk's son (name unknown)"
        long_son = await self.old("son", cut, at=3)
        # Ken took in the sister and the son (two joins): each left its label on him
        await self.merged_into(sister, self.ken)
        await self.merged_into(son, self.ken)
        await self.alias(self.ken, "Ken's sister (name unknown)", "a" * 32)
        await self.alias(self.ken, "Ken's sister (name unknown)'s son (name unknown)", "b" * 32)
        # the same text on an entry nobody was joined into stays as it is
        other = await self.memory.add_entity(
            GUILD, self.c, type="npc", name="Mira", source="dm", status=CONFIRMED
        )
        await self.alias(str(other.value.id), "Ken's sister (name unknown)", "c" * 32)
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(await self.name_of(sister), ("unnamed - Ken's sister", "sister"))
        self.assertEqual((await self.name_of(son))[0], "unnamed - Ken's sister's son")
        self.assertEqual(
            (await self.name_of(long_son))[0], "unnamed - barmaid at the Three Flags Sailing's son"
        )
        for alias_id, text in (
            ("a" * 32, "unnamed - Ken's sister"),
            ("b" * 32, "unnamed - Ken's sister's son"),
        ):
            row = await self.alias_row(alias_id)
            self.assertEqual(row["text"], text)
            self.assertEqual(row["key"], name_key(text))  # (as Python keys it)
            self.assertEqual(row["sound_codes"], [])
        self.assertEqual((await self.alias_row("c" * 32))["text"], "Ken's sister (name unknown)")
        # the Python reading of an old label agrees with the migration's
        self.assertEqual(unnamed.from_old_label(cut), (await self.name_of(long_son))[0])

    async def test_two_that_read_the_same_are_told_apart(self) -> None:
        first = await self.old("a hooded figure", "a hooded figure (name unknown)", at=SEVEN_NOV)
        second = await self.old(
            "a hooded figure", "a hooded figure (name unknown)", at=SEVEN_NOV + 86400
        )
        third = await self.old(
            "a hooded figure", "a hooded figure (name unknown)", at=SEVEN_NOV + 86400 + 60
        )
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(
            await self.name_of(first), ("unnamed - a hooded figure", "a hooded figure")
        )
        self.assertEqual(
            await self.name_of(second),
            (
                "unnamed - a hooded figure (first seen 8 Nov 2026)",
                "a hooded figure (first seen 8 Nov 2026)",
            ),
        )
        self.assertEqual(
            (await self.name_of(third))[0],
            "unnamed - a hooded figure (first seen 8 Nov 2026 19:06)",
        )
        # the name built from the parts agrees with the stored copy
        for eid in (first, second, third):
            name, what = await self.name_of(eid)
            self.assertEqual(unnamed.label(what), name)

    async def test_a_second_run_changes_nothing(self) -> None:
        sister = await self.old("sister", "Ken's sister (name unknown)", anchor=self.ken, at=1)
        await self.db.migrate(MIGRATIONS)
        await self.sql("DELETE FROM schema_migrations WHERE name = %s", NAME)
        await self.db.migrate(MIGRATIONS)
        self.assertEqual((await self.name_of(sister))[0], "unnamed - Ken's sister")


class NewOnes(WithOldRows):
    """After the migration: a new person gets a unique name in the same write (no session's date
    given: the write's own time, here a fixed clock)."""

    async def test_a_second_hooded_figure_gets_the_date(self) -> None:
        await self.db.migrate(MIGRATIONS)
        memory = MemoryStore(self.db, clock=lambda: SEVEN_NOV)
        one = await memory.add_unnamed(GUILD, self.c, what="a hooded figure", read={})
        two = await memory.add_unnamed(GUILD, self.c, what="a hooded figure", read={})
        self.assertEqual(one.entity.name, "unnamed - a hooded figure")
        self.assertEqual(two.entity.name, "unnamed - a hooded figure (first seen 7 Nov 2026)")
        self.assertEqual(two.entity.unnamed_what, "a hooded figure (first seen 7 Nov 2026)")
        again = await memory.add_unnamed(GUILD, self.c, what="sister", anchor_id=self.ken)
        same = await memory.add_unnamed(GUILD, self.c, what="sister", anchor_id=self.ken)
        self.assertEqual((same.entity.id, same.created), (again.entity.id, False))  # (found again)

    async def test_naming_one_keeps_what_it_said_as_the_description(self) -> None:
        await self.db.migrate(MIGRATIONS)
        made = await self.memory.add_unnamed(
            GUILD, self.c, what="barmaid at Three Flags Sailing with red hair", read={}
        )
        named = await self.memory.rename_entity(GUILD, self.c, made.entity.id, "Wenna", source="dm")
        self.assertEqual(
            (named.value.name, named.value.unnamed, named.value.description),
            ("Wenna", False, "barmaid at Three Flags Sailing with red hair"),
        )

    async def test_one_read_finds_its_own_person_again_beside_an_older_one(self) -> None:
        await self.db.migrate(MIGRATIONS)
        memory = MemoryStore(self.db, clock=lambda: SEVEN_NOV)
        await memory.add_unnamed(GUILD, self.c, what="a hooded figure", read={})  # (an older read)
        read: dict[str, str] = {}
        first = await memory.add_unnamed(GUILD, self.c, what="a hooded figure", read=read)
        again = await memory.add_unnamed(GUILD, self.c, what="a hooded figure", read=read)
        self.assertEqual(first.entity.name, "unnamed - a hooded figure (first seen 7 Nov 2026)")
        self.assertEqual((again.entity.id, again.created), (first.entity.id, False))

    async def test_naming_keeps_the_dm_s_own_description_whole(self) -> None:
        await self.db.migrate(MIGRATIONS)
        sister = await self.memory.add_unnamed(GUILD, self.c, what="sister", anchor_id=self.ken)
        await self.sql(
            "UPDATE memory_entities SET description = %s WHERE id = %s",
            "Sharp-tongued, runs the ferry.",
            sister.entity.id,
        )
        named = await self.memory.rename_entity(
            GUILD, self.c, sister.entity.id, "Hesta", source="dm"
        )
        self.assertEqual(named.value.description, "Ken's sister. Sharp-tongued, runs the ferry.")
        said = await self.memory.add_unnamed(GUILD, self.c, what="cousin", anchor_id=self.ken)
        await self.sql(
            "UPDATE memory_entities SET description = %s WHERE id = %s",
            "Ken's cousin, a smith.",
            said.entity.id,
        )
        kept = await self.memory.rename_entity(GUILD, self.c, said.entity.id, "Bors", source="dm")
        self.assertEqual(kept.value.description, "Ken's cousin, a smith.")  # (says so already)


class TheBackup(unittest.TestCase):
    """A backup from before #3736 reads as the migration left the database."""

    def test_old_labels_come_forward(self) -> None:
        from dmbot.memory import backup

        rows: list[dict[str, Any]] = [
            {"table": "entity", "id": "k", "name": "Ken", "unnamed": False, "merged_into": None},
            {
                "table": "entity", "id": "s", "name": "Ken's sister (name unknown)",
                "unnamed": True, "unnamed_what": "sister", "merged_into": "k", "status": "merged",
                "created_at": 1,
            },
            {
                "table": "entity", "id": "h1", "name": "a hooded figure (name unknown)",
                "unnamed": True, "unnamed_what": "a hooded figure", "merged_into": None,
                "status": "proposed", "created_at": SEVEN_NOV,
            },
            {
                "table": "entity", "id": "h2", "name": "a hooded figure (name unknown)",
                "unnamed": True, "unnamed_what": "a hooded figure", "merged_into": None,
                "status": "proposed", "created_at": SEVEN_NOV + 86400,
            },
            {"table": "alias", "id": "a", "entity_id": "k", "text": "Ken's sister (name unknown)",
             "key": "kens sister name unknown"},
        ]  # fmt: skip
        out = {r["id"]: r for r in backup._unnamed_names(rows)}
        self.assertEqual(out["s"]["name"], "unnamed - Ken's sister")
        self.assertEqual(out["h1"]["name"], "unnamed - a hooded figure")
        self.assertEqual(out["h2"]["name"], "unnamed - a hooded figure (first seen 8 Nov 2026)")
        self.assertEqual(out["h2"]["unnamed_what"], "a hooded figure (first seen 8 Nov 2026)")
        self.assertEqual(
            (out["a"]["text"], out["a"]["key"]),
            ("unnamed - Ken's sister", name_key("unnamed - Ken's sister")),
        )

    def test_a_current_backup_passes_through(self) -> None:
        from dmbot.memory import backup

        rows = [{"table": "entity", "id": "s", "name": "unnamed - Ken's sister", "unnamed": True}]
        self.assertIs(backup._unnamed_names(rows), rows)
