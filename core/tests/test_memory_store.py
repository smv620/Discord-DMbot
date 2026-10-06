"""Campaign memory in Postgres: isolation, undo of every operation, rule flags, backups."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory._changes import ALL, scoped_select
from dmbot.memory.backup import MemorySection
from dmbot.memory.checks import CONTRADICTION, TOO_MANY, WRONG_OBJECT, WRONG_SUBJECT
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import (
    CONFIRMED,
    KEEP,
    MERGED,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    Written,
)
from dmbot.memory.ontology import PredicateTerm, TypeTerm
from dmbot.memory.store import MemoryStore
from dmbot.schema import ISOLATED_TABLES
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
MEMORY_TABLES = [t for t in ISOLATED_TABLES if t.startswith("memory_")]


class MemoryTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db, clock=lambda: self.now)
        self.c = (await self.campaigns.create(GUILD_A, "Frozen Wastes", DM)).id

    async def add(self, name: str, *, campaign: str | None = None, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        kw.setdefault("source", "dm")
        written = await self.memory.add_entity(GUILD_A, campaign or self.c, name=name, **kw)
        return written.value.id

    async def relate(self, a: str, pred: str, b: str, **kw: Any) -> Written[Any]:
        kw.setdefault("source", "dm")
        kw.setdefault("confidence", 1.0)
        return await self.memory.add_relation(GUILD_A, self.c, a, pred, b, **kw)

    async def snapshot(self, guild: int = GUILD_A, campaign: str | None = None) -> dict[str, Any]:
        """Every memory row of a campaign, except the change log."""
        out: dict[str, Any] = {}
        async with self.db.guild(guild) as conn:
            for table in ALL:
                order = sql.SQL(" ORDER BY {}").format(sql.Identifier(table.key))
                cur = await conn.execute(scoped_select(table, order), (guild, campaign or self.c))
                out[table.name] = [dict(r) for r in await cur.fetchall()]
        return out

    async def count(self, table: str, guild: int = GUILD_A) -> int:
        async with self.db.guild(guild) as conn:
            query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
            cur = await conn.execute(query)
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])


class Isolation(MemoryTest):
    async def fill(self) -> tuple[str, str]:
        """At least one row in every memory table."""
        await self.memory.add_predicate(
            GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
        )
        await self.memory.add_type(
            GUILD_A,
            self.c,
            TypeTerm("ship", "item", "ship", "A ship.", core=False),
            examples=["x"],
            reason="y",
            source="entitybot",
        )
        a, b = await self.add("Belleros"), await self.add("Cerric")
        await self.memory.add_alias(GUILD_A, self.c, a, "Bell", kind="nickname", source="dm")
        await self.relate(a, "located_in", b)  # flagged: Cerric isn't a place
        await self.memory.add_mention(
            GUILD_A,
            self.c,
            a,
            line_ref="s1:1",
            span=(0, 4),
            confidence=0.9,
            method="exact",
            source="cleaner",
        )
        await self.memory.add_correction(
            GUILD_A, self.c, "bell or us", action="fix", entity_id=a, source="dm"
        )
        return a, b

    async def test_another_server_sees_nothing(self) -> None:
        await self.fill()
        for table in MEMORY_TABLES:
            self.assertGreater(await self.count(table), 0, table)
            self.assertEqual(await self.count(table, GUILD_B), 0, table)
        with self.assertRaises(MemoryRuleError):
            await self.memory.entities(GUILD_B, self.c)

    async def test_another_campaign_sees_and_reaches_nothing(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        a, _ = await self.fill()
        self.assertEqual(await self.memory.entities(GUILD_A, other), [])
        self.assertEqual(await self.memory.aliases(GUILD_A, other, include_secret=True), [])
        self.assertEqual(await self.memory.relations(GUILD_A, other, include_secret=True), [])
        self.assertIsNone(await self.memory.entity(GUILD_A, other, a))
        x = await self.add("Xan", campaign=other)
        attempts: list[Callable[[], Awaitable[Any]]] = [
            lambda: self.memory.add_alias(GUILD_A, other, a, "Bel", kind="short", source="dm"),
            lambda: self.memory.add_relation(
                GUILD_A, other, x, "ally_of", a, source="dm", confidence=1.0
            ),
            lambda: self.memory.merge(GUILD_A, other, x, a, source="dm", dm_said_same=True),
            lambda: self.memory.add_mention(
                GUILD_A,
                other,
                a,
                line_ref="s",
                span=(0, 1),
                confidence=1.0,
                method="dm",
                source="dm",
            ),
            lambda: self.memory.add_correction(
                GUILD_A, other, "bel", action="fix", entity_id=a, source="dm"
            ),
        ]
        for attempt in attempts:
            with self.assertRaises(MemoryRuleError):
                await attempt()

    async def test_undo_only_touches_its_own_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        w = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="A", source="dm")
        await self.add("B", campaign=other)  # batch 1 there too
        before_other = await self.snapshot(campaign=other)
        assert w.batch is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD_B, self.c, w.batch)
        await self.memory.undo(GUILD_A, self.c, w.batch)
        self.assertEqual(await self.snapshot(campaign=other), before_other)

    async def test_database_refuses_links_across_campaigns(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        belleros = await self.add("Belleros")
        with self.assertRaises(pg_errors.ForeignKeyViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text,"
                    " key, kind, secret, status, sound_codes, source, created_at) VALUES"
                    " (%s, %s, %s, %s, 'Bell', 'bell', 'nickname', false, 'proposed', '{}',"
                    " 'dm', 0)",
                    (GUILD_A, other, "e" * 32, belleros),
                )

    async def test_notification_carries_only_campaign_and_version(self) -> None:
        listener = await self.db._pool.getconn()
        try:
            await listener.execute("LISTEN dmbot_memory")
            a = await self.add("Belleros")
            named = await self.memory.version(GUILD_A, self.c)
            await self.memory.add_mention(
                GUILD_A,
                self.c,
                a,
                line_ref="s1:1",
                span=(0, 4),
                confidence=0.9,
                method="exact",
                source="cleaner",
            )
            mentioned = await self.memory.version(GUILD_A, self.c)
            gen = listener.notifies(timeout=1)  # both are already queued
            payloads = [n.payload async for n in gen]
        finally:
            await listener.execute("UNLISTEN *")
            await self.db._pool.putconn(listener)
        # Names changed, then only a mention (copies of the names needn't reload).
        mine = [p for p in payloads if p.startswith(self.c)]  # the channel is database-wide
        self.assertEqual(mine, [f"{self.c}:{named}:1", f"{self.c}:{mentioned}:0"])

    async def test_deleting_the_campaign_deletes_its_memory(self) -> None:
        await self.fill()
        await self.campaigns.delete(GUILD_A, self.c)
        for table in (*MEMORY_TABLES, "memory_changes"):
            self.assertEqual(await self.count(table), 0, table)


class Entities(MemoryTest):
    async def test_new_entity_has_its_name_as_an_alias(self) -> None:
        belleros = await self.add("  Belleros ")
        entity = await self.memory.entity(GUILD_A, self.c, belleros)
        assert entity is not None
        self.assertEqual((entity.name, entity.status), ("Belleros", PROPOSED))
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=belleros)
        self.assertEqual([(a.text, a.kind) for a in aliases], [("Belleros", "full")])

    async def test_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.add("Belleros", type="spaceship")

    async def test_only_the_dm_confirms(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.add("Belleros", source="cleaner", status=CONFIRMED)
        a = await self.add("Belleros", source="scan")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.set_entity_status(GUILD_A, self.c, a, CONFIRMED, source="entitybot")
        await self.memory.set_entity_status(GUILD_A, self.c, a, CONFIRMED, source="dm")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.set_entity_status(GUILD_A, self.c, a, REJECTED, source="entitybot")

    async def test_saying_an_alias_again_only_strengthens_it(self) -> None:
        a = await self.add("Belleros")
        first = await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", source="scan"
        )
        again = await self.memory.add_alias(
            GUILD_A, self.c, a, "The Hooded Stranger", kind="title", source="scan"
        )
        self.assertEqual((again.value.id, again.batch), (first.value.id, None))
        secret = await self.memory.add_alias(
            GUILD_A,
            self.c,
            a,
            "the hooded stranger",
            kind="title",
            secret=True,
            status=CONFIRMED,
            source="dm",
        )
        self.assertEqual((secret.value.secret, secret.value.status), (True, CONFIRMED))
        quieter = await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", source="scan"
        )
        self.assertEqual((quieter.value.secret, quieter.value.status), (True, CONFIRMED))
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.update_alias(
                GUILD_A, self.c, first.value.id, secret=False, source="entitybot"
            )

    async def test_rejected_alias_stays_rejected_unless_the_dm_says_it(self) -> None:
        a = await self.add("Belleros")
        bell = await self.memory.add_alias(
            GUILD_A, self.c, a, "Bell", kind="nickname", source="cleaner"
        )
        await self.memory.update_alias(GUILD_A, self.c, bell.value.id, status=REJECTED, source="dm")
        again = await self.memory.add_alias(
            GUILD_A, self.c, a, "Bell", kind="nickname", source="cleaner"
        )
        self.assertEqual(again.value.status, REJECTED)
        by_dm = await self.memory.add_alias(
            GUILD_A, self.c, a, "Bell", kind="nickname", source="dm"
        )
        self.assertEqual(by_dm.value.status, PROPOSED)

    async def test_secret_aliases_are_hidden_unless_asked(self) -> None:
        a = await self.add("Belleros")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        self.assertEqual([x.text for x in await self.memory.aliases(GUILD_A, self.c)], ["Belleros"])
        everything = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
        self.assertEqual(len(everything), 2)

    async def test_rejected_entries_drop_out_of_lookups(self) -> None:
        a, b = await self.add("Belleros"), await self.add("Cerric")
        await self.relate(a, "ally_of", b)
        await self.memory.set_entity_status(GUILD_A, self.c, a, REJECTED, source="dm")
        self.assertEqual([x.text for x in await self.memory.aliases(GUILD_A, self.c)], ["Cerric"])
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])

    async def test_version_goes_up_with_each_change(self) -> None:
        start = await self.memory.version(GUILD_A, self.c)
        await self.add("Belleros")
        self.assertEqual(await self.memory.version(GUILD_A, self.c), start + 2)  # entity, alias


class Merging(MemoryTest):
    async def test_two_proposed_entries_merge(self) -> None:
        belleros, bell = await self.add("Belleros"), await self.add("Bell")
        cerric = await self.add("Cerric")
        await self.relate(bell, "ally_of", cerric, source="cleaner", confidence=0.8)
        await self.memory.merge(GUILD_A, self.c, belleros, bell, source="entitybot")
        gone = await self.memory.entity(GUILD_A, self.c, bell)
        assert gone is not None
        self.assertEqual((gone.status, gone.merged_into), (MERGED, belleros))
        names = [a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=belleros)]
        self.assertEqual(sorted(names), ["Bell", "Belleros"])
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, entity_id=belleros)), 1)
        resolved = await self.memory.resolve(GUILD_A, self.c, bell)
        assert resolved is not None
        self.assertEqual(resolved.id, belleros)

    async def test_confirmed_or_different_kinds_need_the_dm(self) -> None:
        a = await self.add("Belleros", status=CONFIRMED)
        b = await self.add("Bellamy", status=CONFIRMED)
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        c = await self.add("Bell")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.merge(GUILD_A, self.c, a, c, source="entitybot")
        place = await self.add("Bellhaven", type="place")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.merge(GUILD_A, self.c, c, place, source="entitybot")
        with self.assertRaises(ValueError):  # only a DM source may claim the DM said so
            await self.memory.merge(GUILD_A, self.c, a, c, source="cleaner", dm_said_same=True)
        await self.memory.merge(GUILD_A, self.c, a, c, source="dm", dm_said_same=True)

    async def test_a_fact_between_the_two_is_dropped(self) -> None:
        a, b = await self.add("Belleros"), await self.add("Bell")
        await self.relate(a, "ally_of", b, source="cleaner", confidence=0.5)
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])

    async def test_moved_facts_are_folded_and_checked_again(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
        )
        a, b, cerric = await self.add("Ysolde"), await self.add("Isolde"), await self.add("Cerric")
        p1, p2 = (
            await self.add("Sorrowmere", type="place"),
            await self.add("Brynwater", type="place"),
        )
        await self.relate(a, "ally_of", cerric, source="cleaner", confidence=0.6)
        await self.relate(b, "ally_of", cerric, status=CONFIRMED)  # the DM's word
        await self.relate(a, "born_in", p1, source="cleaner", confidence=0.6)
        await self.relate(b, "born_in", p2, source="cleaner", confidence=0.6)
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        facts = await self.memory.relations(GUILD_A, self.c, entity_id=a)
        allies = [f for f in facts if f.predicate == "ally_of"]
        self.assertEqual(len(allies), 1)  # the same fact twice is kept once
        self.assertEqual(len([f for f in facts if f.predicate == "born_in"]), 2)
        self.assertEqual([f.kind for f in await self.memory.flags(GUILD_A, self.c)], [TOO_MANY])


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
        ysolde = await self.add("Ysolde")
        p1, p2 = (
            await self.add("Sorrowmere", type="place"),
            await self.add("Thornewick", type="place"),
        )
        await self.relate(ysolde, "born_in", p1, status=CONFIRMED)
        written = await self.relate(ysolde, "born_in", p2, source="cleaner", confidence=0.7)
        self.assertEqual([f.kind for f in written.value[1]], [TOO_MANY])
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)  # both kept
        self.assertEqual(len(await self.memory.flags(GUILD_A, self.c)), 1)

    async def test_an_ended_fact_is_history_not_a_clash(self) -> None:
        cerric = await self.add("Cerric")
        p1, p2 = (
            await self.add("Brynwater", type="place"),
            await self.add("Thornewick", type="place"),
        )
        first = await self.relate(cerric, "located_in", p1, from_session_at=100)
        await self.memory.update_relation(
            GUILD_A, self.c, first.value[0].id, to_session_at=200, source="dm"
        )
        written = await self.relate(cerric, "located_in", p2, from_session_at=200)
        self.assertEqual(written.value[1], [])

    async def test_ally_and_enemy_at_once_is_flagged(self) -> None:
        a, b = await self.add("Gorrak"), await self.add("Tamsin")
        await self.relate(a, "ally_of", b)
        written = await self.relate(b, "enemy_of", a, source="cleaner", confidence=0.6)
        self.assertEqual([f.kind for f in written.value[1]], [CONTRADICTION])

    async def test_wrong_kinds_are_flagged(self) -> None:
        a = await self.add("Gorrak")
        fireball = await self.add("Fireball", type="spell")
        written = await self.relate(a, "located_in", fireball, source="cleaner", confidence=0.4)
        self.assertEqual([f.kind for f in written.value[1]], [WRONG_OBJECT])
        written = await self.relate(fireball, "member_of", a, source="cleaner", confidence=0.4)
        self.assertEqual([f.kind for f in written.value[1]], [WRONG_SUBJECT, WRONG_OBJECT])

    async def test_saying_a_fact_again_only_strengthens_it(self) -> None:
        a, b = await self.add("Gorrak"), await self.add("Tamsin")
        first = await self.relate(a, "ally_of", b, source="cleaner", confidence=0.5)
        again = await self.relate(b, "ally_of", a, source="cleaner", confidence=0.5)
        self.assertEqual((again.value[0].id, again.batch), (first.value[0].id, None))
        by_dm = await self.relate(a, "ally_of", b, status=CONFIRMED, secret=True)
        self.assertEqual((by_dm.value[0].status, by_dm.value[0].secret), (CONFIRMED, True))
        await self.memory.update_relation(
            GUILD_A, self.c, first.value[0].id, status=REJECTED, source="dm"
        )
        back = await self.relate(a, "ally_of", b, source="cleaner", confidence=0.9)
        self.assertEqual((back.value[0].status, back.batch), (REJECTED, None))
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, include_secret=True)), 0)

    async def test_only_the_dm_confirms_facts(self) -> None:
        a, b = await self.add("Gorrak"), await self.add("Tamsin")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.relate(a, "ally_of", b, source="cleaner", status=CONFIRMED)

    async def test_mentions_must_be_from_this_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        x = await self.add("Xan", campaign=other)
        theirs = await self.memory.add_mention(
            GUILD_A, other, x, line_ref="s", span=(0, 1), confidence=1.0, method="dm", source="dm"
        )
        a, b = await self.add("Gorrak"), await self.add("Tamsin")
        with self.assertRaises(MemoryRuleError):
            await self.relate(a, "ally_of", b, mention_ids=[theirs.value])

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


Step = Callable[[], Awaitable[Written[Any]]]


class Undo(MemoryTest):
    async def check_undo_and_redo(self, step: Step) -> None:
        before = await self.snapshot()
        written = await step()
        after = await self.snapshot()
        self.assertNotEqual(after, before)
        assert written.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.snapshot(), before)
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_every_operation_can_be_undone_and_redone(self) -> None:
        a, b, c = await self.add("Belleros"), await self.add("Cerric"), await self.add("Bell")
        bel = (
            await self.memory.add_alias(GUILD_A, self.c, a, "Bel", kind="short", source="dm")
        ).value
        fact = (await self.relate(a, "ally_of", b, source="cleaner", confidence=0.5)).value[0]
        flagged = await self.relate(a, "located_in", b, source="cleaner", confidence=0.3)
        flag = flagged.value[1][0]
        steps: list[Step] = [
            lambda: self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm"),
            lambda: self.memory.add_alias(GUILD_A, self.c, a, "Belle", kind="short", source="dm"),
            lambda: self.memory.update_alias(GUILD_A, self.c, bel.id, secret=True, source="dm"),
            lambda: self.memory.set_entity_status(GUILD_A, self.c, a, CONFIRMED, source="dm"),
            lambda: self.relate(a, "enemy_of", b, source="cleaner", confidence=0.3),
            lambda: self.memory.update_relation(
                GUILD_A,
                self.c,
                fact.id,
                status=CONFIRMED,
                to_session_at=500,
                secret=True,
                source="dm",
            ),
            lambda: self.memory.resolve_flag(GUILD_A, self.c, flag.id, source="dm"),
            lambda: self.memory.add_correction(
                GUILD_A, self.c, "Bell or us", action="fix", entity_id=a, source="dm"
            ),
            lambda: self.memory.add_correction(GUILD_A, self.c, "bell", action=KEEP, source="undo"),
            lambda: self.memory.add_mention(
                GUILD_A,
                self.c,
                a,
                line_ref="s1:12",
                span=(0, 8),
                confidence=0.9,
                method="exact",
                source="cleaner",
            ),
            lambda: self.memory.add_predicate(
                GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
            ),
            lambda: self.memory.add_type(
                GUILD_A,
                self.c,
                TypeTerm("ship", "item", "ship", "A ship.", core=False),
                examples=["x"],
                reason="y",
                source="entitybot",
            ),
            lambda: self.memory.merge(GUILD_A, self.c, b, c, source="entitybot"),
        ]
        for i, step in enumerate(steps):
            with self.subTest(step=i):
                await self.check_undo_and_redo(step)

    async def test_retiring_a_term_can_be_undone(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
        )
        await self.check_undo_and_redo(
            lambda: self.memory.deprecate_term(GUILD_A, self.c, "born_in", source="entitybot")
        )

    async def test_undoing_a_busy_merge_splits_everything_again(self) -> None:
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        cerric, tamsin = await self.add("Cerric"), await self.add("Tamsin")
        await self.memory.add_alias(GUILD_A, self.c, keep, "Bel", kind="short", source="dm")
        await self.memory.add_alias(
            GUILD_A, self.c, gone, "bel", kind="short", secret=True, source="dm"
        )  # same key on both: one is folded away
        await self.memory.add_alias(
            GUILD_A, self.c, cerric, "Cer", kind="short", used_by=gone, source="dm"
        )
        await self.relate(gone, "ally_of", tamsin)  # moved, maybe re-ordered
        await self.relate(gone, "ally_of", keep)  # becomes a self-fact: dropped
        await self.relate(cerric, "enemy_of", gone)
        await self.relate(gone, "ally_of", cerric, source="cleaner", confidence=0.5)  # flagged
        await self.memory.add_mention(
            GUILD_A,
            self.c,
            gone,
            line_ref="s1:3",
            span=(0, 4),
            confidence=0.9,
            method="exact",
            source="cleaner",
        )
        await self.memory.add_correction(
            GUILD_A, self.c, "bell or us", action="fix", entity_id=gone, source="dm"
        )
        await self.check_undo_and_redo(
            lambda: self.memory.merge(GUILD_A, self.c, keep, gone, source="entitybot")
        )

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
        cerric = await self.add("Cerric")
        await self.relate(w.value.id, "ally_of", cerric)
        assert w.batch is not None
        with self.assertRaisesRegex(MemoryRuleError, "changed again"):
            await self.memory.undo(GUILD_A, self.c, w.batch)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)


class Backups(MemoryTest):
    async def test_memory_survives_backup_and_restore(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, BORN_IN, examples=["x"], reason="y", source="entitybot"
        )
        a, b = await self.add("Belleros"), await self.add("Bell")
        place = await self.add("Sorrowmere", type="place")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        await self.relate(a, "born_in", place)
        await self.relate(a, "located_in", b, source="cleaner", confidence=0.25)  # flagged
        await self.relate(b, "ally_of", place, source="cleaner", confidence=0.25)  # flagged
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        await self.memory.add_correction(GUILD_A, self.c, "bel", action=KEEP, source="dm")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copy = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):  # not backed up
            original.pop(table), copy.pop(table)
        self.assertEqual(copy, original)
        self.assertGreater(await self.memory.version(GUILD_B, restored.id), 0)

    async def test_replacing_from_a_backup_bumps_the_version(self) -> None:
        await self.add("Belleros")
        backup = await self.campaigns.export(GUILD_A, self.c)
        before = await self.memory.version(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_A, backup, DM, replace_campaign_id=self.c)
        self.assertGreater(await self.memory.version(GUILD_A, self.c), before)

    async def test_damaged_memory_is_refused(self) -> None:
        a, b = await self.add("Belleros"), await self.add("Bell")
        await self.memory.merge(GUILD_A, self.c, a, b, source="entitybot")
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = backup["sections"]["memory"]

        def changed(tag: str, **fields: Any) -> list[Any]:
            return [{**r, **fields} if r["table"] == tag else r for r in rows]

        broken: list[list[Any]] = [
            [*rows, {"table": "spaceship"}],
            changed("entity", type="spaceship"),
            changed("alias", entity_id="0" * 32),
            changed("entity", id="not-an-id"),
            [{**r, "merged_into": "0" * 32} if r.get("merged_into") else r for r in rows],
            [*rows, {**rows[0]}],  # the same entry twice
            [{"kind": "entity", **{k: v for k, v in rows[0].items() if k != "table"}}],
        ]
        for i, bad in enumerate(broken):
            data = {**backup, "sections": {**backup["sections"], "memory": bad}}
            with self.subTest(case=i), self.assertRaisesRegex(CampaignError, "damaged"):
                await self.campaigns.import_backup(GUILD_B, data, DM)


class Indexes(MemoryTest):
    async def test_every_memory_link_has_an_index(self) -> None:
        """Deleting a campaign checks every link; without an index each check reads the
        whole table (measured at 40 s for a large campaign)."""
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                """
                SELECT c.conrelid::regclass::text AS tbl,
                       array(SELECT a.attname FROM unnest(c.conkey) k
                             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k)
                           AS cols,
                       EXISTS (
                           SELECT 1 FROM pg_index i
                           WHERE i.indrelid = c.conrelid
                             AND (SELECT array_agg(x) FROM unnest(
                                    (i.indkey::int2[])[0:cardinality(c.conkey) - 1]) x)::int2[]
                                 @> c.conkey
                             AND cardinality(c.conkey) <= i.indnatts
                       ) AS indexed
                FROM pg_constraint c
                WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE '%%memory_%%'
                """
            )
            rows = await cur.fetchall()
        self.assertGreater(len(rows), 5)
        missing = [(r["tbl"], r["cols"]) for r in rows if not r["indexed"]]
        self.assertEqual(missing, [])


class HeardNames(MemoryTest):
    """Names said in a session, kept at its end for ranking hints (#126, #127)."""

    async def test_kept_counted_and_never_a_new_version(self) -> None:
        from dmbot.memory.models import Heard

        a, b = await self.add("Belleros", status=CONFIRMED), await self.add("Cerric")
        gone = await self.add("Bellamy")  # rejected: still kept, the lookup skips it
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        version = await self.memory.version(GUILD_A, self.c)
        heard = [
            Heard(a, "t:x:1000:8", (0, 8), "exact"),
            Heard(a, "t:x:2000:8", (4, 12), "spelling"),
            Heard(b, "t:x:3000:9", (0, 6), "exact"),
            Heard(gone, "t:x:4000:9", (0, 7), "exact"),
            Heard(b, "", (0, 6), "exact"),  # bad: left out
        ]
        kept = await self.memory.add_session_heard(GUILD_A, self.c, 1_000, heard)
        self.assertEqual(kept, 4)  # the bad one left out
        self.assertEqual(await self.memory.version(GUILD_A, self.c), version)  # no reload
        await self.memory.add_session_heard(GUILD_A, self.c, 2_000, heard[:1])
        data = await self.memory.lookup_data(GUILD_A, self.c)
        counts = {h.entity_id: (h.times, h.last_session_at) for h in data.heard}
        self.assertEqual(counts[a], (3, 2_000))
        self.assertEqual(counts[b], (1, 1_000))
        from dmbot.memory.lookup import CampaignLookup

        self.assertNotIn(gone, CampaignLookup.build(data).heard)  # only live names count
        self.assertEqual(data.recent_sessions, (2_000, 1_000))

    async def test_another_campaign_or_server_never_sees_them(self) -> None:
        from dmbot.memory.models import Heard

        a = await self.add("Belleros", status=CONFIRMED)
        other = (await self.campaigns.create(GUILD_A, "Strahd", DM)).id
        await self.memory.add_session_heard(
            GUILD_A, self.c, 1_000, [Heard(a, "t:x:1:8", (0, 8), "exact")]
        )
        self.assertEqual((await self.memory.lookup_data(GUILD_A, other)).heard, ())
        # An entity ID from this campaign written under another one is skipped.
        kept = await self.memory.add_session_heard(
            GUILD_A, other, 1_000, [Heard(a, "t:x:1:8", (0, 8), "exact")]
        )
        self.assertEqual(kept, 0)


class LookupInPostgres(MemoryTest):
    async def test_lookup_data_holds_what_matching_needs(self) -> None:
        a, b = await self.add("Belleros", status=CONFIRMED), await self.add("Cerric")
        gone = await self.add("Bellamy")
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        await self.relate(a, "ally_of", b)
        await self.relate(a, "enemy_of", b, secret=True)
        await self.memory.add_correction(GUILD_A, self.c, "kale", action=KEEP, source="dm")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual(data.version, await self.memory.version(GUILD_A, self.c))
        self.assertEqual({e.name for e in data.entities}, {"Belleros", "Cerric"})
        self.assertEqual(
            {(x.text, x.secret) for x in data.aliases},
            {("Belleros", False), ("Cerric", False), ("the hooded stranger", True)},
        )
        self.assertEqual([r.predicate for r in data.relations], ["ally_of"])  # no secrets
        self.assertEqual([c.heard for c in data.corrections], ["kale"])
        stored = {x.text: x.sound_codes for x in data.aliases}
        self.assertEqual(stored["Belleros"], ("PLRS",))

    async def test_cache_follows_changes_live(self) -> None:
        cache = LookupCache(self.memory)
        listening = asyncio.Event()

        async def listen(
            channel: str, on_listening: Callable[[], None]
        ) -> AsyncGenerator[str, None]:
            def ready() -> None:
                on_listening()
                listening.set()

            async with contextlib.aclosing(self.db.listen(channel, ready)) as stream:
                async for raw in stream:
                    yield raw

        follower = asyncio.create_task(cache.follow(listen))
        try:
            await asyncio.wait_for(listening.wait(), 10)
            a = await self.add("Belleros")
            first = await cache.get(GUILD_A, self.c)
            self.assertEqual([e.text for e in first.sounds_like("bell or us")], ["Belleros"])
            await self.memory.add_alias(GUILD_A, self.c, a, "Bell", kind="nickname", source="dm")
            for _ in range(100):
                current = await cache.get(GUILD_A, self.c)
                if current.exact("bell"):
                    break
                await asyncio.sleep(0.05)
            self.assertEqual([e.entity_id for e in current.exact("bell")], [a])
        finally:
            follower.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await follower

    async def test_restored_sound_codes_are_worked_out_again(self) -> None:
        await self.add("Belleros")
        backup = await self.campaigns.export(GUILD_A, self.c)
        for row in backup["sections"]["memory"]:
            if row["table"] == "alias":
                row["sound_codes"] = ["FORGED"]
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        aliases = await self.memory.aliases(GUILD_B, restored.id)
        self.assertEqual([x.sound_codes for x in aliases], [("PLRS",)])
