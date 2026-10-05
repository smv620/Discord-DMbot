"""Campaign memory in Postgres: isolation, undo of every operation, rule flags, backups."""

from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.memory.checks import CONTRADICTION, TOO_MANY, WRONG_OBJECT
from dmbot.memory.models import CONFIRMED, KEEP, MERGED, PROPOSED, MemoryRuleError
from dmbot.memory.ontology import PredicateTerm, TypeTerm
from dmbot.memory.store import MemorySection, MemoryStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM = 7

BORN_IN = PredicateTerm(
    "born_in",
    "was born in",
    "Where someone was born.",
    ("character",),
    ("place",),
    max_per_subject=1,
    core=False,
)


class MemoryTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db, clock=lambda: self.now)
        self.c = (await self.campaigns.create(GUILD_A, "Frozen Wastes", DM)).id

    async def npc(self, name: str, *, campaign: str | None = None, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        kw.setdefault("source", "dm")
        written = await self.memory.add_entity(GUILD_A, campaign or self.c, name=name, **kw)
        return written.value.id

    async def snapshot(self, campaign: str | None = None) -> dict[str, Any]:
        """Everything stored for a campaign, except the change log."""
        async with self.db.guild(GUILD_A) as conn:
            rows = await MemorySection().dump(conn, GUILD_A, campaign or self.c)
        return {"rows": sorted(rows, key=lambda r: (r["kind"], r.get("id", r.get("key"))))}


class Isolation(MemoryTest):
    async def test_another_campaign_sees_nothing(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        belleros = await self.npc("Belleros")
        self.assertEqual(await self.memory.entities(GUILD_A, other), [])
        self.assertIsNone(await self.memory.entity(GUILD_A, other, belleros))
        with self.assertRaises(MemoryRuleError):  # can't link to it from there
            await self.memory.add_alias(
                GUILD_A, other, belleros, "Bell", kind="nickname", source="dm"
            )

    async def test_another_server_sees_nothing(self) -> None:
        belleros = await self.npc("Belleros")
        with self.assertRaises(MemoryRuleError):
            await self.memory.entities(GUILD_B, self.c)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_entities")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
        self.assertIsNotNone(await self.memory.entity(GUILD_A, self.c, belleros))

    async def test_database_refuses_links_across_campaigns(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        belleros = await self.npc("Belleros")
        with self.assertRaises(pg_errors.ForeignKeyViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text,"
                    " key, kind, secret, status, sound_codes, source, created_at) VALUES"
                    " (%s, %s, %s, %s, 'Bell', 'bell', 'nickname', false, 'proposed', '{}',"
                    " 'dm', 0)",
                    (GUILD_A, other, "e" * 32, belleros),
                )

    async def test_deleting_the_campaign_deletes_its_memory(self) -> None:
        a, b = await self.npc("Belleros"), await self.npc("Cerric")
        await self.memory.add_relation(
            GUILD_A, self.c, a, "ally_of", b, source="dm", confidence=1.0
        )
        await self.campaigns.delete(GUILD_A, self.c)
        async with self.db.guild(GUILD_A) as conn:
            for table in ("memory_entities", "memory_relations", "memory_changes"):
                query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
                cur = await conn.execute(query)
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["n"], 0, table)


class Entities(MemoryTest):
    async def test_new_entity_has_its_name_as_an_alias(self) -> None:
        belleros = await self.npc("  Belleros ")
        entity = await self.memory.entity(GUILD_A, self.c, belleros)
        assert entity is not None
        self.assertEqual((entity.name, entity.status), ("Belleros", PROPOSED))
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=belleros)
        self.assertEqual([(a.text, a.kind) for a in aliases], [("Belleros", "full")])

    async def test_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.npc("Belleros", type="spaceship")

    async def test_alias_already_known_changes_nothing(self) -> None:
        belleros = await self.npc("Belleros")
        first = await self.memory.add_alias(
            GUILD_A, self.c, belleros, "Bell", kind="nickname", source="entitybot"
        )
        again = await self.memory.add_alias(
            GUILD_A, self.c, belleros, "bell", kind="nickname", source="entitybot"
        )
        self.assertEqual(again.value.id, first.value.id)
        self.assertIsNone(again.batch)

    async def test_secret_aliases_are_hidden_unless_asked(self) -> None:
        belleros = await self.npc("Belleros")
        await self.memory.add_alias(
            GUILD_A, self.c, belleros, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        shown = await self.memory.aliases(GUILD_A, self.c)
        self.assertEqual([a.text for a in shown], ["Belleros"])
        everything = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
        self.assertEqual(len(everything), 2)

    async def test_version_goes_up_with_each_change(self) -> None:
        start = await self.memory.version(GUILD_A, self.c)
        await self.npc("Belleros")
        self.assertEqual(await self.memory.version(GUILD_A, self.c), start + 2)  # entity, alias


class Merging(MemoryTest):
    async def test_two_proposed_entries_merge(self) -> None:
        belleros, bell = await self.npc("Belleros"), await self.npc("Bell")
        cerric = await self.npc("Cerric")
        await self.memory.add_relation(
            GUILD_A, self.c, bell, "ally_of", cerric, source="cleaner", confidence=0.8
        )
        await self.memory.merge(GUILD_A, self.c, belleros, bell, source="entitybot")
        gone = await self.memory.entity(GUILD_A, self.c, bell)
        assert gone is not None
        self.assertEqual((gone.status, gone.merged_into), (MERGED, belleros))
        names = [a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=belleros)]
        self.assertEqual(sorted(names), ["Bell", "Belleros"])
        facts = await self.memory.relations(GUILD_A, self.c, entity_id=belleros)
        self.assertEqual(len(facts), 1)
        resolved = await self.memory.resolve(GUILD_A, self.c, bell)
        assert resolved is not None
        self.assertEqual(resolved.id, belleros)

    async def test_confirmed_entries_need_the_dm(self) -> None:
        a = await self.npc("Belleros", status=CONFIRMED)
        b = await self.npc("Bellamy", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        c = await self.npc("Bell")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.merge(GUILD_A, self.c, a, c, source="entitybot")
        await self.memory.merge(GUILD_A, self.c, a, c, source="dm", dm_said_same=True)

    async def test_a_fact_between_the_two_is_dropped(self) -> None:
        a, b = await self.npc("Belleros"), await self.npc("Bell")
        await self.memory.add_relation(
            GUILD_A, self.c, a, "ally_of", b, source="cleaner", confidence=0.5
        )
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])


class Rules(MemoryTest):
    async def test_two_birthplaces_are_flagged_not_refused(self) -> None:
        await self.memory.add_predicate(
            GUILD_A,
            self.c,
            BORN_IN,
            examples=["Ysolde was born in Sorrowmere."],
            reason="The DM tracks birthplaces.",
            source="entitybot",
        )
        ysolde = await self.npc("Ysolde")
        sorrowmere = await self.npc("Sorrowmere", type="place")
        thornewick = await self.npc("Thornewick", type="place")
        await self.memory.add_relation(
            GUILD_A,
            self.c,
            ysolde,
            "born_in",
            sorrowmere,
            source="dm",
            confidence=1.0,
            status=CONFIRMED,
        )
        written = await self.memory.add_relation(
            GUILD_A, self.c, ysolde, "born_in", thornewick, source="cleaner", confidence=0.7
        )
        _, flags = written.value
        self.assertEqual([f.kind for f in flags], [TOO_MANY])
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)  # both kept
        self.assertEqual(len(await self.memory.flags(GUILD_A, self.c)), 1)

    async def test_ally_and_enemy_at_once_is_flagged(self) -> None:
        a, b = await self.npc("Gorrak"), await self.npc("Tamsin")
        await self.memory.add_relation(
            GUILD_A, self.c, a, "ally_of", b, source="dm", confidence=1.0
        )
        written = await self.memory.add_relation(
            GUILD_A, self.c, b, "enemy_of", a, source="cleaner", confidence=0.6
        )
        self.assertEqual([f.kind for f in written.value[1]], [CONTRADICTION])

    async def test_wrong_kind_is_flagged(self) -> None:
        a = await self.npc("Gorrak")
        fireball = await self.npc("Fireball", type="spell")
        written = await self.memory.add_relation(
            GUILD_A, self.c, a, "located_in", fireball, source="cleaner", confidence=0.4
        )
        self.assertEqual([f.kind for f in written.value[1]], [WRONG_OBJECT])

    async def test_two_way_fact_is_stored_once(self) -> None:
        a, b = await self.npc("Gorrak"), await self.npc("Tamsin")
        first = await self.memory.add_relation(
            GUILD_A, self.c, a, "ally_of", b, source="dm", confidence=1.0
        )
        again = await self.memory.add_relation(
            GUILD_A, self.c, b, "ally_of", a, source="dm", confidence=1.0
        )
        self.assertEqual(again.value[0].id, first.value[0].id)
        self.assertIsNone(again.batch)

    async def test_reuse_and_growth_limits(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "ally_of"):
            await self.memory.add_predicate(
                GUILD_A,
                self.c,
                PredicateTerm(
                    "friends_with", "friends with", "Friends.", ("npc",), ("npc",), core=False
                ),
                examples=["x"],
                reason="y",
                source="entitybot",
            )
        for i in range(5):
            await self.memory.add_type(
                GUILD_A,
                self.c,
                TypeTerm(f"thing{'abcde'[i]}x{i}q", "item", f"label {i}", "A thing.", core=False),
                examples=["x"],
                reason="y",
                source="entitybot",
                session_started_at=self.now,
            )
        with self.assertRaisesRegex(MemoryRuleError, "Enough"):
            await self.memory.add_type(
                GUILD_A,
                self.c,
                TypeTerm("vessel", "item", "vessel", "A ship.", core=False),
                examples=["x"],
                reason="y",
                source="entitybot",
                session_started_at=self.now,
            )


class Undo(MemoryTest):
    async def assert_undo_restores(self, before: dict[str, Any], batch: int | None) -> None:
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_every_operation_can_be_undone(self) -> None:
        empty = await self.snapshot()
        w = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="Belleros", source="dm")
        await self.assert_undo_restores(empty, w.batch)

        belleros = await self.npc("Belleros")
        cerric = await self.npc("Cerric")
        bell = await self.npc("Bell")
        steps = [
            lambda: self.memory.add_alias(
                GUILD_A, self.c, belleros, "Bel", kind="short", source="dm"
            ),
            lambda: self.memory.set_entity_status(
                GUILD_A, self.c, belleros, CONFIRMED, source="dm"
            ),
            lambda: self.memory.add_relation(
                GUILD_A, self.c, belleros, "located_in", cerric, source="cleaner", confidence=0.3
            ),  # flagged too
            lambda: self.memory.merge(GUILD_A, self.c, cerric, bell, source="entitybot"),
            lambda: self.memory.add_correction(
                GUILD_A, self.c, "Bell or us", action="fix", entity_id=belleros, source="dm"
            ),
            lambda: self.memory.add_correction(GUILD_A, self.c, "bell", action=KEEP, source="undo"),
            lambda: self.memory.add_mention(
                GUILD_A,
                self.c,
                belleros,
                line_ref="s1:12",
                span=(0, 8),
                confidence=0.9,
                method="exact",
                source="cleaner",
            ),
            lambda: self.memory.add_predicate(
                GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
            ),
        ]
        for step in steps:
            before = await self.snapshot()
            written = await step()
            self.assertNotEqual(await self.snapshot(), before)
            await self.assert_undo_restores(before, written.batch)

    async def test_undo_of_a_merge_splits_again_and_redo_works(self) -> None:
        a, b = await self.npc("Belleros"), await self.npc("Bell")
        before = await self.snapshot()
        merged = await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        after = await self.snapshot()
        assert merged.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, merged.batch)
        self.assertEqual(await self.snapshot(), before)
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_undo_twice_is_refused(self) -> None:
        w = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm")
        assert w.batch is not None
        await self.memory.undo(GUILD_A, self.c, w.batch)
        with self.assertRaisesRegex(MemoryRuleError, "already"):
            await self.memory.undo(GUILD_A, self.c, w.batch)

    async def test_undo_is_refused_when_changed_since(self) -> None:
        w = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="Belleros", source="dm")
        await self.memory.set_entity_status(GUILD_A, self.c, w.value.id, CONFIRMED, source="dm")
        assert w.batch is not None
        with self.assertRaisesRegex(MemoryRuleError, "changed again"):
            await self.memory.undo(GUILD_A, self.c, w.batch)

    async def test_undo_never_deletes_later_facts(self) -> None:
        w = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="Belleros", source="dm")
        cerric = await self.npc("Cerric")
        await self.memory.add_relation(
            GUILD_A, self.c, w.value.id, "ally_of", cerric, source="dm", confidence=1.0
        )
        assert w.batch is not None
        with self.assertRaisesRegex(MemoryRuleError, "changed again"):
            await self.memory.undo(GUILD_A, self.c, w.batch)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)


class Backups(MemoryTest):
    async def test_memory_survives_backup_and_restore(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
        )
        a, b = await self.npc("Belleros"), await self.npc("Bell")
        place = await self.npc("Sorrowmere", type="place")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        await self.memory.add_relation(
            GUILD_A, self.c, a, "born_in", place, source="dm", confidence=1.0
        )
        await self.memory.add_relation(
            GUILD_A, self.c, a, "located_in", b, source="cleaner", confidence=0.2
        )  # flagged
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        backup = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        original = await self.snapshot()
        async with self.db.guild(GUILD_B) as conn:
            rows = await MemorySection().dump(conn, GUILD_B, restored.id)
        copy = {"rows": sorted(rows, key=lambda r: (r["kind"], r.get("id", r.get("key"))))}
        self.assertEqual(copy, original)

    async def test_damaged_memory_is_refused(self) -> None:
        await self.npc("Belleros")
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = backup["sections"]["memory"]
        broken: list[list[Any]] = [
            [*rows, {"kind": "spaceship"}],
            [{**r, "type": "spaceship"} if r["kind"] == "entity" else r for r in rows],
            [{**r, "entity_id": "0" * 32} if r["kind"] == "alias" else r for r in rows],
            [{**r, "id": "not-an-id"} if r["kind"] == "entity" else r for r in rows],
            [*rows, {**rows[0]}],  # the same entry twice
        ]
        for bad in broken:
            data = {**backup, "sections": {**backup["sections"], "memory": bad}}
            with self.subTest(bad=bad[-1]), self.assertRaisesRegex(CampaignError, "damaged"):
                await self.campaigns.import_backup(GUILD_B, data, DM)
