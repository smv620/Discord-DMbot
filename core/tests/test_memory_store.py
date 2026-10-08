"""Campaign memory in Postgres: isolation, undo of every operation, rule flags, backups."""

import asyncio
import contextlib
import dataclasses
import itertools
import json
import random
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Any
from unittest.mock import AsyncMock, patch

from psycopg import AsyncConnection, sql
from psycopg import errors as pg_errors

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory._changes import ALIASES, ALL, Changes, scoped_select
from dmbot.memory._changes import ENTITIES as ENTITIES_TABLE
from dmbot.memory._changes import FLAGS as FLAGS_TABLE
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
    NewName,
    TooLateToUndo,
    Written,
)
from dmbot.memory.ontology import PredicateTerm, TypeTerm
from dmbot.memory.sounds import sound_codes
from dmbot.memory.store import MemoryStore
from dmbot.schema import ISOLATED_TABLES
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM = 7

SERVES = PredicateTerm(
    "sworn_to",
    "is sworn to",
    "Who someone works for.",
    ("character",),
    ("character",),
    max_per_subject=2,
    core=False,
)
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
        from dmbot.memory.models import Heard

        await self.memory.add_session_heard(GUILD_A, self.c, 1_000, [Heard(a, DM, 1)])
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

    async def test_flags_whose_clash_is_gone_are_closed_after_a_session(self) -> None:
        """#164: rejecting one side of a clash (or an undo, or an edit) ends it, but left
        its flag open. The after-session cleanup closes it; real problems stay open."""
        cerric = await self.add("Cerric")
        p1, p2 = (
            await self.add("Brynwater", type="place"),
            await self.add("Thornewick", type="place"),
        )
        first = await self.relate(cerric, "located_in", p1)
        clash = await self.relate(cerric, "located_in", p2, source="cleaner", confidence=0.5)
        fireball = await self.add("Fireball", type="spell")
        wrong = await self.relate(cerric, "member_of", fireball, source="cleaner", confidence=0.4)
        self.assertEqual([f.kind for f in clash.value[1]], [TOO_MANY])
        nothing = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual((nothing.value, nothing.batch), ([], None))  # every clash still real
        await self.memory.update_relation(
            GUILD_A, self.c, first.value[0].id, status=REJECTED, source="dm"
        )
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual([f.id for f in closed.value], [clash.value[1][0].id])
        still = await self.memory.flags(GUILD_A, self.c)
        self.assertEqual({f.id for f in still}, {f.id for f in wrong.value[1]})
        assert closed.batch is not None
        await self.memory.undo(GUILD_A, self.c, closed.batch, source="dm")  # can be undone
        self.assertEqual(len(await self.memory.flags(GUILD_A, self.c)), len(still) + 1)

    async def test_a_merge_that_ends_a_clash_leaves_nothing_to_close(self) -> None:
        """Two places that turn out to be one: the two facts fold into one and the flag
        goes with the folded fact, so the cleanup finds nothing. His member_of flag
        (wrong kind) is a different problem and stays."""
        cerric = await self.add("Cerric")
        town = await self.add("Bryn Shander", type="place")
        same_town = await self.add("Bryn", type="place")
        await self.relate(cerric, "located_in", town)
        await self.relate(cerric, "located_in", same_town, source="cleaner", confidence=0.5)
        fireball = await self.add("Fireball", type="spell")
        await self.relate(cerric, "member_of", fireball, source="cleaner", confidence=0.4)
        await self.memory.merge(GUILD_A, self.c, town, same_town, source="dm")
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(closed.value, [])
        kinds = {f.kind for f in await self.memory.flags(GUILD_A, self.c)}
        self.assertEqual(kinds, {WRONG_OBJECT})

    async def test_a_merge_can_still_be_undone_after_its_flag_is_closed(self) -> None:
        """#348: the cleanup closing a flag the merge made must not block the merge's
        Undo (it used to fail with "changed again since")."""
        cerric, twin = await self.add("Cerric"), await self.add("Ceric")
        p1, p2 = (
            await self.add("Brynwater", type="place"),
            await self.add("Thornewick", type="place"),
        )
        own = await self.relate(cerric, "located_in", p1)
        await self.relate(twin, "located_in", p2, source="cleaner", confidence=0.5)
        merged = await self.memory.merge(GUILD_A, self.c, cerric, twin, source="dm")
        self.assertEqual([f.kind for f in await self.memory.flags(GUILD_A, self.c)], [TOO_MANY])
        await self.memory.update_relation(  # the other fact, not one the merge moved
            GUILD_A, self.c, own.value[0].id, status=REJECTED, source="dm"
        )
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(len(closed.value), 1)
        assert merged.batch is not None
        await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")
        entities = {e.id for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertIn(twin, entities)  # split again
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])  # the merge's flag

    async def test_undoing_a_batch_of_several_closed_flags_works(self) -> None:
        """#348 review, a guard for #342's set-based undo: a batch that inserted two
        flags, both later closed by the cleanup, can still be undone."""
        cerric = await self.add("Cerric")
        fireball = await self.add("Fireball", type="spell")
        pair = await self.relate(fireball, "member_of", cerric, source="cleaner", confidence=0.4)
        self.assertEqual([f.kind for f in pair.value[1]], [WRONG_SUBJECT, WRONG_OBJECT])
        await self.memory.set_entity_type(GUILD_A, self.c, fireball, "npc", source="dm")
        await self.memory.set_entity_type(GUILD_A, self.c, cerric, "faction", source="dm")
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(len(closed.value), 2)
        assert pair.batch is not None
        await self.memory.undo(GUILD_A, self.c, pair.batch, source="dm")
        self.assertEqual(await self.memory.relations(GUILD_A, self.c, include_secret=True), [])
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])

    async def test_naming_the_right_kind_closes_a_wrong_kind_flag(self) -> None:
        cerric = await self.add("Cerric")
        tower = await self.add("The Tower", type="spell")  # wrongly a spell
        wrong = await self.relate(cerric, "located_in", tower, source="cleaner", confidence=0.4)
        self.assertEqual([f.kind for f in wrong.value[1]], [WRONG_OBJECT])
        await self.memory.set_entity_type(GUILD_A, self.c, tower, "place", source="dm")
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual([f.id for f in closed.value], [wrong.value[1][0].id])

    async def test_still_too_many_stays_flagged(self) -> None:
        """Room for two, four given. Each TOO_MANY flag names one partner: rejecting a
        partner closes only the flags naming it, while the clash with the others (still
        three for two places) stays flagged. Dropping to two closes them all."""
        await self.memory.add_predicate(
            GUILD_A, self.c, SERVES, examples=["x"], reason="y", source="entitybot"
        )
        cerric = await self.add("Cerric")
        lords = [await self.add(f"Lord {n}") for n in range(4)]
        facts = [(await self.relate(cerric, "sworn_to", lord)).value for lord in lords[:2]]
        third = await self.relate(cerric, "sworn_to", lords[2], source="cleaner", confidence=0.5)
        fourth = await self.relate(cerric, "sworn_to", lords[3], source="cleaner", confidence=0.5)
        self.assertTrue(third.value[1] and fourth.value[1])  # both over the limit
        await self.memory.update_relation(
            GUILD_A, self.c, facts[0][0].id, status=REJECTED, source="dm"
        )
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        rejected = facts[0][0].id
        self.assertEqual({f.other_id for f in closed.value}, {rejected})
        still = await self.memory.flags(GUILD_A, self.c)
        self.assertTrue(still)  # three left for two places: still a clash
        self.assertNotIn(rejected, {f.other_id for f in still})
        await self.memory.update_relation(
            GUILD_A, self.c, facts[1][0].id, status=REJECTED, source="dm"
        )
        await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])

    async def test_another_campaigns_flags_are_left_alone(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        for campaign in (self.c, other):
            cerric = await self.add("Cerric", campaign=campaign)
            p1 = await self.add("Brynwater", type="place", campaign=campaign)
            p2 = await self.add("Thornewick", type="place", campaign=campaign)
            first = await self.memory.add_relation(
                GUILD_A, campaign, cerric, "located_in", p1, source="dm", confidence=1.0
            )
            await self.memory.add_relation(
                GUILD_A, campaign, cerric, "located_in", p2, source="cleaner", confidence=0.5
            )
            await self.memory.update_relation(
                GUILD_A, campaign, first.value[0].id, status=REJECTED, source="dm"
            )
        await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])
        self.assertEqual(len(await self.memory.flags(GUILD_A, other)), 1)  # its own turn

    async def closing_statements(self, flags: int) -> int:
        """Statements for the cleanup to close `flags` stale flags."""
        town = await self.add(f"Bryn Shander {flags}", type="place")
        inn = await self.add(f"Inn {flags}", type="place")
        firsts = []
        for n in range(flags):
            npc = await self.add(f"Guard {flags}-{n}")
            firsts.append((await self.relate(npc, "located_in", town)).value[0].id)
            await self.relate(npc, "located_in", inn, source="cleaner", confidence=0.5)
        for fact_id in firsts:
            await self.memory.update_relation(
                GUILD_A, self.c, fact_id, status=REJECTED, source="dm"
            )
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(len(closed.value), flags)
        return statements

    async def test_checking_many_flags_takes_a_few_statements(self) -> None:
        one = await self.closing_statements(1)
        thirty = await self.closing_statements(30)
        # A fixed few statements however many close: one bulk update and its log (#347;
        # it was 6 per flag, then 3).
        self.assertEqual(thirty, one)

    async def test_the_same_flag_twice_keeps_only_the_oldest(self) -> None:
        town, inn = (
            await self.add("Bryn Shander", type="place"),
            await self.add("Inn", type="place"),
        )
        npc = await self.add("Guard")
        await self.relate(npc, "located_in", town)
        await self.relate(npc, "located_in", inn, source="cleaner", confidence=0.5)
        (flag,) = await self.memory.flags(GUILD_A, self.c)
        self.now += 1
        async with self.memory._write(GUILD_A, self.c, "entitybot") as w:  # as a merge might
            row = (await w.select(FLAGS_TABLE, " AND id = %s", [flag.id]))[0]
            await w.insert(FLAGS_TABLE, {**row, "id": "f" * 32, "created_at": self.now})
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual([f.id for f in closed.value], ["f" * 32])
        self.assertEqual([f.id for f in await self.memory.flags(GUILD_A, self.c)], [flag.id])

    async def test_a_term_dmbot_cant_check_still_loses_its_doubles(self) -> None:
        town, inn = (
            await self.add("Bryn Shander", type="place"),
            await self.add("Inn", type="place"),
        )
        npc = await self.add("Guard")
        await self.relate(npc, "located_in", town)
        await self.relate(npc, "located_in", inn, source="cleaner", confidence=0.5)
        (flag,) = await self.memory.flags(GUILD_A, self.c)
        self.now += 1
        async with self.memory._write(GUILD_A, self.c, "entitybot") as w:
            row = (await w.select(FLAGS_TABLE, " AND id = %s", [flag.id]))[0]
            await w.insert(FLAGS_TABLE, {**row, "id": "f" * 32, "created_at": self.now})
        from dmbot.memory import store as store_module

        real = store_module._load_ontology

        async def without_located_in(w: Any) -> Any:
            onto = await real(w)
            return dataclasses.replace(
                onto, predicates={k: v for k, v in onto.predicates.items() if k != "located_in"}
            )

        with patch.object(store_module, "_load_ontology", without_located_in):
            closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual([f.id for f in closed.value], ["f" * 32])  # the original stays

    async def test_a_busy_place_checks_only_the_facts_that_matter(self) -> None:
        """#347 perf-qa: a flag on a fact about a busy place is checked against the
        same pair and the same "how many" term, not every fact on that place."""
        from dmbot.memory import store as store_module

        hub = await self.add("Bryn Shander", type="place")
        inn = await self.add("Inn", type="place")
        for n in range(40):  # others in town, nothing to do with the guard's flag
            await self.relate(await self.add(f"Villager {n}"), "located_in", hub)
        guard = await self.add("Guard")
        first = (await self.relate(guard, "located_in", hub)).value[0]
        await self.relate(guard, "located_in", inn, source="cleaner", confidence=0.5)
        await self.memory.update_relation(GUILD_A, self.c, first.id, status=REJECTED, source="dm")
        from dmbot.memory.checks import check_relation as real

        sizes: list[int] = []

        def counted(onto: Any, new: Any, st: str, ot: str, existing: Any) -> Any:
            sizes.append(len(existing))
            return real(onto, new, st, ot, existing)

        with patch.object(store_module, "check_relation", counted):
            closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(len(closed.value), 1)
        self.assertLessEqual(max(sizes), 2)  # the guard's own two facts, not 40 villagers

    async def test_flags_against_different_facts_are_not_doubles(self) -> None:
        npc = await self.add("Guard")
        places = [await self.add(n, type="place") for n in ("Town", "Inn", "Keep")]
        for place in places:  # three places: too many for one person
            await self.relate(npc, "located_in", place, source="cleaner", confidence=0.5)
        before = await self.memory.flags(GUILD_A, self.c)
        self.assertGreater(len({f.other_id for f in before}), 1)
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(closed.value, [])  # all still real, none a double
        self.assertEqual(len(await self.memory.flags(GUILD_A, self.c)), len(before))

    async def test_one_campaigns_check_never_skips_anothers(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        await self.memory.resolve_stale_flags(GUILD_A, self.c)  # nothing open: recorded
        with patch.object(self.memory, "_write", side_effect=AssertionError("no lock")):
            await self.memory.resolve_stale_flags(GUILD_A, self.c)  # skipped
        a = await self.add("Guard", campaign=other)
        town = (
            await self.memory.add_entity(GUILD_A, other, type="place", name="Town", source="dm")
        ).value.id
        inn = (
            await self.memory.add_entity(GUILD_A, other, type="place", name="Inn", source="dm")
        ).value.id
        first = await self.memory.add_relation(
            GUILD_A, other, a, "located_in", town, source="dm", confidence=1.0
        )
        await self.memory.add_relation(
            GUILD_A, other, a, "located_in", inn, source="cleaner", confidence=0.5
        )
        await self.memory.update_relation(
            GUILD_A, other, first.value[0].id, status=REJECTED, source="dm"
        )
        closed = await self.memory.resolve_stale_flags(GUILD_A, other)
        self.assertEqual(len(closed.value), 1)

    async def test_nothing_changed_since_the_last_check_skips_it(self) -> None:
        a = await self.add("Guard")
        town, inn = (
            await self.add("Bryn Shander", type="place"),
            await self.add("Inn", type="place"),
        )
        first = (await self.relate(a, "located_in", town)).value[0]
        await self.relate(a, "located_in", inn, source="cleaner", confidence=0.5)
        await self.memory.resolve_stale_flags(GUILD_A, self.c)  # the clash is real: kept
        with patch.object(self.memory, "_write", side_effect=AssertionError("no lock")):
            await self.memory.resolve_stale_flags(GUILD_A, self.c)
        await self.memory.update_relation(GUILD_A, self.c, first.id, status=REJECTED, source="dm")
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)  # changed: checked
        self.assertEqual(len(closed.value), 1)

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

    async def test_a_typed_name_and_its_rule_are_one_change(self) -> None:
        """#503: "Type it…" with a name DMbot didn't know: one Undo takes back both."""
        written = await self.memory.add_typed_name(GUILD_A, self.c, " Marin ", "Maerin")
        entity = written.value
        self.assertEqual((entity.name, entity.type, entity.status), ("Maerin", "concept", PROPOSED))
        data = await self.memory.lookup_data(GUILD_A, self.c)
        (fix,) = data.corrections
        self.assertEqual((fix.heard, fix.action, fix.entity_id), ("Marin", "fix", entity.id))
        self.assertIn(entity.id, [e.id for e in data.entities])  # waiting in Check new names
        await self.check_undo_and_redo(
            lambda: self.memory.add_typed_name(GUILD_A, self.c, "Velka", "Velkka")
        )

    async def test_a_typed_name_already_known_or_secret_is_refused(self) -> None:
        a = await self.add("Belleros")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the Veiled One", kind="title", source="dm", secret=True
        )
        before = await self.snapshot()
        for typed in ["Belleros", "the veiled one"]:
            with self.subTest(typed=typed), self.assertRaises(MemoryRuleError):
                await self.memory.add_typed_name(GUILD_A, self.c, "Marin", typed)
        self.assertEqual(await self.snapshot(), before)  # nothing written

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

    async def test_a_big_merge_and_its_undo_take_few_statements(self) -> None:
        """#164: rows move in one statement per table, not one round trip per row."""
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [NewName(f"Friend {n}", "npc", CONFIRMED, (), ()) for n in range(200)],
            source="dm",
        )
        friends = [i for i in written.value if i is not None]
        for n in range(200):
            await self.memory.add_alias(
                GUILD_A, self.c, gone, f"Bell {n}", kind="short", source="dm"
            )
        for friend in friends:
            await self.relate(gone, "ally_of", friend)
        before = await self.snapshot()
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            merged = await self.memory.merge(
                GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True
            )
            merge_statements, statements = statements, 0
            assert merged.batch is not None
            after = await self.snapshot()
            statements = 0
            undone = await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")
            undo_statements = statements
        # Row by row, this was well over a thousand round trips each. Counted with
        # execute, so the savepoints around bulk undo runs (their own round trips) aren't.
        self.assertLess(merge_statements, 60)
        self.assertLess(undo_statements, 60)
        self.assertEqual(await self.snapshot(), before)  # undo split them again exactly
        # Still one log row per changed row, so undo and redo work as before.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT table_name, count(*) AS n FROM memory_changes WHERE batch = %s"
                " GROUP BY table_name",
                (merged.batch,),
            )
            logged = {r["table_name"]: r["n"] for r in await cur.fetchall()}
        self.assertEqual(
            (logged["memory_aliases"], logged["memory_relations"]), (201, 200)
        )  # + "Bell"
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_a_merge_onto_a_fact_about_a_rejected_entry_is_refused(self) -> None:
        """As before #164: a fact to check whose other end was rejected stops the merge."""
        keep, gone, cerric = (
            await self.add("Belleros"),
            await self.add("Bell"),
            await self.add("Cerric"),
        )
        await self.relate(gone, "ally_of", cerric)
        await self.memory.set_entity_status(GUILD_A, self.c, cerric, REJECTED, source="dm")
        before = await self.snapshot()
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
        self.assertEqual(await self.snapshot(), before)

    async def test_a_flag_on_a_fact_the_merge_folds_away_goes_with_it(self) -> None:
        """#164 review: the same, for a fact folded into its twin later in the walk."""
        frida = await self.add("Frida")
        keep, gone = (
            await self.add("Neverwinter", type="place"),
            await self.add("Nevers", type="place"),
        )
        await self.relate(frida, "located_in", gone, detail="docks")  # flagged against the next
        self.now += 1
        await self.relate(frida, "located_in", gone)  # becomes the same as the last: folded
        self.now += 1
        await self.relate(frida, "located_in", keep)
        await self.check_undo_and_redo(
            lambda: self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
        )

    async def test_a_merge_with_shared_names_and_facts_undoes_in_bulk(self) -> None:
        """Rows the merge deleted (twins) come back in bulk, values and all."""
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        for n in range(50):
            friend = await self.add(f"Friend {n}")
            await self.relate(keep, "ally_of", friend, source="cleaner", confidence=0.5)
            await self.relate(gone, "ally_of", friend, secret=True)  # folded, upgrades keep's
            await self.memory.add_alias(
                GUILD_A, self.c, gone, f"Nick {n}", kind="short", secret=True, source="dm"
            )
            await self.memory.add_alias(
                GUILD_A, self.c, keep, f"Nick {n}", kind="short", source="dm"
            )
        await self.check_undo_and_redo(
            lambda: self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
        )

    async def test_a_bulk_undo_after_one_fact_changed_again_is_refused(self) -> None:
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        facts = []
        for n in range(20):
            friend = await self.add(f"Friend {n}")
            facts.append((await self.relate(gone, "ally_of", friend)).value[0])
        merged = await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
        await self.memory.update_relation(
            GUILD_A, self.c, facts[7].id, status=CONFIRMED, source="dm"
        )
        changed = await self.snapshot()
        assert merged.batch is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")
        self.assertEqual(await self.snapshot(), changed)  # nothing half undone

    async def test_a_flag_on_a_fact_the_merge_drops_goes_with_it(self) -> None:
        """#164 review: a moved fact can be flagged against one dropped later in the
        same merge (it became "X is in X"); that flag must go, not break the merge."""
        keep, gone = (
            await self.add("Neverwinter", type="place"),
            await self.add("Nevers", type="place"),
        )
        sword_coast = await self.add("Sword Coast", type="place")
        await self.relate(gone, "located_in", sword_coast)  # becomes keep's: is in
        self.now += 1  # the walk goes oldest first
        await self.relate(keep, "located_in", gone)  # becomes "is in itself": dropped
        await self.check_undo_and_redo(
            lambda: self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
        )

    async def test_a_merge_of_thousands_of_rows_and_its_undo(self) -> None:
        """#164: 6,000 moved rows used to need more values than one statement takes;
        now each table's rows and their log are one statement whatever the count."""
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        async with self.db.guild(GUILD_A) as conn:  # quicker than 6,000 add_mention calls
            await conn.execute(
                "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id, line_ref,"
                " span_start, span_end, confidence, method, created_at)"
                " SELECT %s, %s, md5(i::text), %s, 's1:' || i, 0, 4, 0.9, 'exact', %s"
                " FROM generate_series(1, 6000) i",
                (GUILD_A, self.c, gone, self.now),
            )
        before = await self.snapshot()
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            merged = await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm")
            merge_statements, statements = statements, 0
            assert merged.batch is not None
            await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")
        self.assertLess(merge_statements, 60)
        self.assertLess(statements, 60)
        self.assertEqual(await self.snapshot(), before)

    async def test_a_new_campaign_merges_fast_with_stale_statistics(self) -> None:
        """#342 review: with statistics from before this campaign existed, a filtered
        UPDATE … FROM picked a nested-loop plan: seconds for 50 names among 3,000."""
        await self.fill_names(self.c, 2000)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("ANALYZE memory_aliases")
            await conn.execute("ANALYZE memory_entities")
        fresh = (await self.campaigns.create(GUILD_A, "Fresh", DM)).id
        await self.fill_names(fresh, 3000)
        keep = await self.memory.add_entity(GUILD_A, fresh, type="npc", name="Keep", source="dm")
        gone = await self.memory.add_entity(GUILD_A, fresh, type="npc", name="Gone", source="dm")
        for n in range(50):
            await self.memory.add_alias(
                GUILD_A, fresh, gone.value.id, f"Gone {n}", kind="short", source="dm"
            )
        started = time.perf_counter()
        await self.memory.merge(GUILD_A, fresh, keep.value.id, gone.value.id, source="dm")
        self.assertLess(time.perf_counter() - started, 1.0)  # the bad plan took over 3 s

    async def fill_names(self, campaign: str, count: int) -> None:
        """`count` entries with one name each, written directly (quicker than the API)."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at)"
                " SELECT %s, %s, md5(%s || i), 'npc', 'Name ' || i, '', 'proposed', 'dm', %s"
                " FROM generate_series(1, %s) i",
                (GUILD_A, campaign, campaign, self.now, count),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at)"
                " SELECT %s, %s, md5('a' || %s || i), md5(%s || i), 'Name ' || i,"
                " 'name ' || i, 'full', false, 'proposed', '{}', 'dm', %s"
                " FROM generate_series(1, %s) i",
                (GUILD_A, campaign, campaign, campaign, self.now, count),
            )

    async def test_a_bulk_undo_that_trips_a_unique_name_goes_row_by_row(self) -> None:
        """#342 review: in one batch a name leaves E for F, then another name with the
        same words moves from G to E. Undone in one statement, the second can reach E
        before the first has left: the savepoint catches it and the rows go one by
        one, with the same result."""
        e, f, g = await self.add("Edda"), await self.add("Finn"), await self.add("Gwen")
        a1 = await self.memory.add_alias(GUILD_A, self.c, e, "Red", kind="short", source="dm")
        a2 = await self.memory.add_alias(GUILD_A, self.c, g, "Red", kind="short", source="dm")
        before = await self.snapshot()
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await w.update_rows(ALIASES, {a1.value.id: {"entity_id": f}})
            await w.update_rows(ALIASES, {a2.value.id: {"entity_id": e}})
            batch = w.batch
        assert batch is not None
        after = await self.snapshot()
        from dmbot.memory import _changes

        async def tripped(changes: Any, run: Any) -> bool:
            # The bulk undo in the order that collides: Red back to E while G's Red is
            # still there. Postgres refuses; the savepoint must roll it back (#347).
            await changes.conn.execute(
                "UPDATE memory_aliases SET entity_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (e, *changes.ids, a1.value.id),
            )
            return True

        with patch.object(_changes, "_undo_run", AsyncMock(side_effect=tripped)) as bulk:
            undone = await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        bulk.assert_awaited()  # it failed for real, and the rows went one by one
        self.assertEqual(await self.snapshot(), before)
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_a_chain_of_three_undoes_in_whatever_order_it_needs(self) -> None:
        """#347: rows that must wait for each other, given in the worst order (oldest
        first): each round frees the next, until all are back."""
        from dmbot.memory import _changes

        e, g, h, f = [await self.add(n) for n in ("Edda", "Gwen", "Hild", "Finn")]
        reds = [
            (
                await self.memory.add_alias(
                    GUILD_A, self.c, owner, "Red", kind="short", source="dm"
                )
            ).value.id
            for owner in (e, g, h)
        ]
        before = await self.snapshot()
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await w.update_rows(ALIASES, {reds[0]: {"entity_id": f}})  # E's Red to F
            await w.update_rows(ALIASES, {reds[1]: {"entity_id": e}})  # G's Red to E
            await w.update_rows(ALIASES, {reds[2]: {"entity_id": g}})  # H's Red to G
            batch = w.batch
        tries = 0
        real_row = _changes._undo_row

        async def counted(*args: Any) -> None:
            nonlocal tries
            tries += 1
            await real_row(*args)

        async with self.memory._write(GUILD_A, self.c, "undo", undoes=batch) as w:
            cur = await w.conn.execute(
                "SELECT table_name, row_id, op, before, after FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND batch = %s ORDER BY version",
                (*w.ids, batch),
            )
            with patch.object(_changes, "_undo_row", counted):
                await _changes._undo_rows(w, await cur.fetchall())
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(tries, 3 + 2 + 1)  # three rounds, each freeing the next

    async def test_update_rows_refuses_bad_columns(self) -> None:
        a = await self.add("Edda")
        (own,) = await self.memory.aliases(GUILD_A, self.c, entity_id=a)
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            for rows in (
                {own.id: {"nope": 1}},  # not a column
                {own.id: {"id": "x"}},  # the key never changes
            ):
                with self.subTest(rows), self.assertRaises(ValueError):
                    await w.update_rows(ALIASES, rows)
            other = (await w.select(ENTITIES_TABLE, " LIMIT 1"))[0]["id"]
            with self.assertRaises(ValueError):  # each row must set the same columns
                await w.update_rows(
                    ALIASES, {own.id: {"secret": True}, other: {"status": CONFIRMED}}
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


class Lists(MemoryTest):
    async def test_a_list_is_one_change_and_one_undo(self) -> None:
        from dmbot.memory.models import NewName

        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("Belleros", "npc", CONFIRMED, ("Bell",), ("the hooded stranger",)),
                NewName("Odd Thing", "concept", PROPOSED),
            ],
            source="dm",
        )
        a, b = written.value
        assert a is not None and b is not None
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=a, include_secret=True)
        self.assertEqual(
            {(x.text, x.secret) for x in aliases},
            {("Belleros", False), ("Bell", False), ("the hooded stranger", True)},
        )
        thing = await self.memory.entity(GUILD_A, self.c, b)
        assert thing is not None and written.batch is not None
        self.assertEqual(thing.status, PROPOSED)
        await self.memory.undo_names(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.memory.entities(GUILD_A, self.c), [])

    async def test_known_names_get_other_names_in_the_same_change(self) -> None:
        from dmbot.memory.models import MoreNames, NewName

        bel = await self.add("Belleros", status=CONFIRMED)
        ulf = await self.add("Ulfgar", status=CONFIRMED)
        gone = await self.add("Gone")
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        before = await self.snapshot()
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [NewName("Bryn Shander", "place", CONFIRMED)],
            source="dm",
            more=[
                MoreNames(bel, ("the old knight", "Ulfgar"), ("the hooded stranger",)),
                MoreNames(gone, ("Ghost",)),  # forgotten since: left out
            ],
        )
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=bel, include_secret=True)
        self.assertEqual(
            {(x.text, x.secret, x.status) for x in aliases},
            {
                ("Belleros", False, CONFIRMED),
                ("the old knight", False, CONFIRMED),
                ("the hooded stranger", True, CONFIRMED),
            },  # Ulfgar is another name's: left out
        )
        self.assertEqual(len(await self.memory.aliases(GUILD_A, self.c, entity_id=ulf)), 1)
        assert written.batch is not None
        await self.memory.undo_names(GUILD_A, self.c, written.batch)  # one Undo for all
        self.assertEqual(await self.snapshot(), before)

    async def test_a_name_the_entry_has_secretly_or_turned_down_is_skipped_quietly(self) -> None:
        from dmbot.memory.models import MoreNames

        bel = await self.add("Belleros", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, bel, "the hooded stranger", kind="title", source="dm",
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        bel_alias = (
            await self.memory.add_alias(GUILD_A, self.c, bel, "Bel", kind="short", source="dm")
        ).value
        await self.memory.update_alias(GUILD_A, self.c, bel_alias.id, status=REJECTED, source="dm")
        written = await self.memory.add_names(
            GUILD_A, self.c, [], source="dm", secret_clashes=False,
            more=[MoreNames(bel, ("the hooded stranger", "Bel"))],
        )  # fmt: skip
        self.assertEqual(written.value, [None])  # nothing added, and no error to give it away
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=bel, include_secret=True)
        self.assertEqual(
            sorted((x.text, x.secret) for x in aliases if x.status != REJECTED),
            [("Belleros", False), ("the hooded stranger", True)],
        )

    async def test_names_already_used_are_skipped_inside_the_save(self) -> None:
        from dmbot.memory.models import NewName

        await self.add("Belleros", status=CONFIRMED)
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("belleros", "npc", CONFIRMED),
                NewName("Kesh", "npc", CONFIRMED, ("Belleros",)),
            ],
            source="dm",
        )
        skipped, kesh = written.value
        self.assertIsNone(skipped)
        assert kesh is not None
        keys = {x.key for x in await self.memory.aliases(GUILD_A, self.c, entity_id=kesh)}
        self.assertEqual(keys, {"kesh"})  # its other name was already Belleros's

    async def test_undoing_a_list_is_refused_after_a_connection_or_for_other_changes(self) -> None:
        from dmbot.memory.models import NewName

        written = await self.memory.add_names(
            GUILD_A, self.c, [NewName("Kesh", "npc", CONFIRMED)], source="dm"
        )
        (kesh,) = written.value
        assert kesh is not None and written.batch is not None
        other = await self.add("Ulfgar")
        await self.relate(kesh, "knows", other)
        with self.assertRaises(MemoryRuleError):  # never deletes the connection silently
            await self.memory.undo_names(GUILD_A, self.c, written.batch)
        renamed = await self.memory.rename_entity(GUILD_A, self.c, other, "Ulf", source="dm")
        assert renamed.batch is not None
        with self.assertRaises(MemoryRuleError):  # not a list of names
            await self.memory.undo_names(GUILD_A, self.c, renamed.batch)

    async def test_a_whole_group_gets_its_kind_in_one_change(self) -> None:
        from dmbot.memory.models import NewName

        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [NewName(f"Mage {n}", "concept", PROPOSED, (f"M{n}",)) for n in range(3)],
            source="dm",
        )
        ids = [i for i in written.value if i is not None]
        await self.memory.set_entity_status(GUILD_A, self.c, ids[0], REJECTED, source="dm")
        done = await self.memory.confirm_kinds(GUILD_A, self.c, ids, "npc", source="dm")
        self.assertEqual(done.value, 2)  # one was removed in between
        confirmed = await self.memory.entities(GUILD_A, self.c, statuses=[CONFIRMED])
        self.assertEqual(
            {(e.name, e.type) for e in confirmed}, {("Mage 1", "npc"), ("Mage 2", "npc")}
        )
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=ids[1])
        self.assertTrue(all(a.status == CONFIRMED for a in aliases))

    async def test_the_name_a_key_belongs_to_is_one_look_up(self) -> None:
        """known_as asks for one key, never every name (#580): only a confirmed entry,
        by a name everyone may know."""
        from dmbot.memory.models import NewName, name_key

        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("Belleros", "npc", CONFIRMED, ("Bell",), ("the Stranger",)),
                NewName("Kesh", "npc", PROPOSED),
                NewName("Ulfgar", "npc", CONFIRMED),
                NewName("Bel", "npc", CONFIRMED),
            ],
            source="dm",
        )
        belleros, _, ulfgar, bel = written.value
        assert belleros is not None and ulfgar is not None and bel is not None
        await self.memory.set_entity_status(GUILD_A, self.c, ulfgar, REJECTED, source="dm")
        old = await self.memory.add_alias(
            GUILD_A, self.c, belleros, "Old Bell", kind="nickname", status=CONFIRMED, source="dm"
        )
        await self.memory.update_alias(GUILD_A, self.c, old.value.id, status=REJECTED, source="dm")
        await self.memory.merge(GUILD_A, self.c, belleros, bel, source="dm", dm_said_same=True)

        async def named(text: str) -> str | None:
            return await self.memory.confirmed_name_for(GUILD_A, self.c, name_key(text))

        self.assertEqual(await named("bell"), "Belleros")
        self.assertEqual(await named("BELLEROS"), "Belleros")
        self.assertIsNone(await named("the stranger"))  # a secret name: no clash shown
        self.assertIsNone(await named("Kesh"))  # only suggested
        self.assertIsNone(await named("Ulfgar"))  # removed
        self.assertIsNone(await named("Old Bell"))  # a name the DM took back
        self.assertEqual(await named("Bel"), "Belleros")  # merged: the one it became
        self.assertIsNone(await named("Auril"))

        # On a campaign with no table statistics yet, each look-up still goes by the key
        # index and the full entry ID, never a read of the whole campaign (#580 perf).
        plans: list[str] = []
        real = AsyncConnection.execute

        async def explained(conn: Any, query: Any, params: Any = None, **kw: Any) -> Any:
            if str(query).startswith("SELECT") and "memory_" in str(query):
                cur = await real(conn, "EXPLAIN " + str(query), params)
                plans.append("\n".join(str(next(iter(r.values()))) for r in await cur.fetchall()))
            return await real(conn, query, params, **kw)

        with patch.object(AsyncConnection, "execute", explained):
            self.assertEqual(await named("bell"), "Belleros")
        by_key = [p for p in plans if "memory_aliases" in p]
        self.assertTrue(by_key and all("memory_aliases_by_key" in p for p in by_key), plans)
        by_id = [p for p in plans if "memory_entities" in p]
        self.assertTrue(by_id and all("Index Cond" in p and "id =" in p for p in by_id), plans)

    async def test_a_big_group_gets_its_kind_in_a_few_statements(self) -> None:
        """ "Every wizard is an NPC" for 150 names, each with names of its own, takes a few
        statements, not one chain per name and per name of it (#580)."""
        from dmbot.memory.models import NewName

        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName(f"Mage {n}", "concept", PROPOSED, (f"M{n}", f"Magus {n}"), (f"veil {n}",))
                for n in range(150)
            ],
            source="dm",
        )
        ids = [i for i in written.value if i is not None]
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            done = await self.memory.confirm_kinds(
                GUILD_A, self.c, [*ids, ids[0]], "npc", source="dm"
            )
        self.assertEqual(done.value, 150)  # a name given twice counts once
        self.assertLess(statements, 40)  # was 2,557 (13 now)
        names = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
        self.assertEqual(len(names), 600)
        self.assertTrue(all(a.status == CONFIRMED for a in names))
        confirmed = await self.memory.entities(GUILD_A, self.c, statuses=[CONFIRMED])
        self.assertEqual({e.type for e in confirmed}, {"npc"})
        assert done.batch is not None  # one change, and Undo puts it all back
        await self.memory.undo(GUILD_A, self.c, done.batch)
        names = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
        self.assertTrue(all(a.status == PROPOSED for a in names))
        back = await self.memory.entities(GUILD_A, self.c, statuses=[PROPOSED])
        self.assertEqual(len(back), 150)
        self.assertEqual({e.type for e in back}, {"concept"})

    async def save_list(
        self, size: int, first: int = 0, more: Sequence[Any] = ()
    ) -> tuple[Written[Any], int, float]:
        """Add a list of `size` names, each with another name and a secret one: what it
        returned, the statements it took, and the seconds."""
        from dmbot.memory.models import NewName

        names = [
            NewName(f"Guard {n}", "npc", CONFIRMED, (f"G{n}",), (f"the spy {n}",))
            for n in range(first, first + size)
        ]
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            started = time.perf_counter()
            written = await self.memory.add_names(GUILD_A, self.c, names, source="dm", more=more)
            seconds = time.perf_counter() - started
        return written, statements, seconds

    async def test_a_list_stores_each_name_as_typed_with_its_sounds(self) -> None:
        """Names are worked out before the write (#598): the same as one at a time."""
        from dmbot.memory.models import NewName
        from dmbot.memory.sounds import sound_codes

        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [
                NewName("  Belleros ", "npc", CONFIRMED, ("Bell", "Bell"), ("the  Stranger",)),
                NewName("Kesh", "npc", CONFIRMED, ("Bell",)),  # Belleros's already
            ],
            source="dm",
        )
        bel, kesh = written.value
        assert bel is not None and kesh is not None
        got = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
        self.assertEqual(
            sorted((x.text, x.entity_id == bel, x.secret) for x in got),
            [("Bell", True, False), ("Belleros", True, False), ("Kesh", False, False),
             ("the Stranger", True, True)],
        )  # fmt: skip
        for alias in got:
            self.assertEqual(list(alias.sound_codes), list(sound_codes(alias.text)))

    async def test_a_bad_name_for_a_forgotten_entry_doesnt_stop_the_list(self) -> None:
        from dmbot.memory.models import MoreNames, NewName

        gone = await self.add("Gone")
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        written = await self.memory.add_names(
            GUILD_A, self.c, [NewName("Kesh", "npc", CONFIRMED)], source="dm",
            more=[MoreNames(gone, ("x" * 500,))],  # never looked at: its entry has gone
        )  # fmt: skip
        kesh, left_out = written.value
        self.assertIsNotNone(kesh)
        self.assertIsNone(left_out)

    async def test_a_long_list_saves_in_a_few_statements(self) -> None:
        """#253: the write lock is held for a fixed few statements, however long the list:
        one for the entries, one for their names, and one log statement each."""
        from dmbot.memory.models import MoreNames

        bel = await self.add("Belleros", status=CONFIRMED)
        _, one, _ = await self.save_list(1, more=[MoreNames(bel, ("Bell",))])
        before = await self.snapshot()
        more = [MoreNames(bel, ("the old knight",), ("the hooded stranger",))]
        written, many, _ = await self.save_list(300, first=1, more=more)
        self.assertEqual(many, one)
        self.assertLess(many, 20)
        self.assertEqual(len([i for i in written.value if i is not None]), 301)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT table_name, count(*) AS n FROM memory_changes WHERE guild_id = %s"
                " AND campaign_id = %s AND batch = %s GROUP BY table_name",
                (GUILD_A, self.c, written.batch),
            )
            logged = {r["table_name"]: r["n"] for r in await cur.fetchall()}
        # One log row each, Belleros's two new names among the aliases.
        self.assertEqual(logged, {"memory_entities": 300, "memory_aliases": 902})
        assert written.batch is not None
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            await self.memory.undo_names(GUILD_A, self.c, written.batch)  # still one Undo
        self.assertLess(statements, 25)  # names, then entries: a few runs, not row by row
        self.assertEqual(await self.snapshot(), before)

    async def test_a_2000_line_list_saves_faster_than_row_by_row(self) -> None:
        """#253: timed against the old way, one INSERT and one log row per name."""

        async def row_by_row(w: Changes, table: Any, rows: Any) -> list[dict[str, Any]]:
            return [await w.insert(table, row) for row in rows]

        with patch.object(Changes, "insert_many", row_by_row):
            _, old_statements, old = await self.save_list(2000)
        _, new_statements, new = await self.save_list(2000, first=2000)
        print(
            f"\n2,000 names (6,000 rows): row by row {old_statements} statements"
            f" {old * 1000:.0f} ms; now {new_statements} statements {new * 1000:.0f} ms"
        )
        self.assertGreater(old_statements, 12_000)
        self.assertLess(new_statements, 20)
        self.assertLess(new, old)

    async def test_only_the_dm_and_never_a_players_character(self) -> None:
        from dmbot.memory.models import NewName

        with self.assertRaises(MemoryRuleError):
            await self.memory.add_names(
                GUILD_A, self.c, [NewName("X", "npc", CONFIRMED)], source="entitybot"
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_names(
                GUILD_A, self.c, [NewName("X", "player_character", CONFIRMED)], source="dm"
            )


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

    async def test_an_older_plain_backup_keeps_its_secret_names(self) -> None:
        a = await self.add("Belleros")
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", secret=True, source="dm"
        )
        backup = await self.campaigns.export(GUILD_A, self.c)
        backup["version"] = 1  # made before #164: plain JSON, no compression
        plain = json.dumps(backup, indent=1).encode()
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(plain), DM)
        original, copy = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):  # not backed up
            original.pop(table), copy.pop(table)
        self.assertEqual(copy, original)
        self.assertIn(
            ("the hooded stranger", True),
            {(r["text"], r["secret"]) for r in copy["memory_aliases"]},
        )

    async def test_backups_check_and_build_rows_off_the_event_loop(self) -> None:
        """#164: a big campaign's rows take a good fraction of a second to check or
        build; that CPU runs in a worker thread while the event loop (voice, other
        servers) carries on. Each step asks the loop to run something: on the loop
        itself that would wait forever (5 s here), so no timing threshold to flake."""
        from dmbot.campaigns import store as campaign_store
        from dmbot.memory import backup

        await self.add("Belleros")
        loop = asyncio.get_running_loop()
        ran: list[str] = []

        def watched(name: str, real: Any) -> Any:
            def run(*args: Any) -> Any:
                asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=5)
                ran.append(name)
                return real(*args)

            return run

        with (
            patch.object(backup, "_dump_rows", watched("dump", backup._dump_rows)),
            patch.object(backup, "_checked_rows", watched("check", backup._checked_rows)),
            patch.object(
                campaign_store,
                "_validate_backup",
                watched("validate", campaign_store._validate_backup),
            ),
        ):
            data = await self.campaigns.export(GUILD_A, self.c)
            await self.campaigns.import_backup(GUILD_B, data, DM)
        tables = len(backup._TAGS)  # one hop per table: one table's raw rows at a time (#393)
        self.assertEqual(ran, ["dump"] * tables + ["validate", "check"])

    async def test_a_damaged_file_is_refused_before_anything_is_touched(self) -> None:
        """The memory rows are checked before the restore's transaction opens: a bad
        file never locks or clears the campaign it would replace."""
        await self.add("Belleros")
        before = await self.snapshot()
        data = await self.campaigns.export(GUILD_A, self.c)
        data["sections"]["memory"].append({"table": "entity", "id": "not an id"})
        opened = 0
        real = self.db.guild

        def counting(*args: Any, **kw: Any) -> Any:
            nonlocal opened
            opened += 1
            return real(*args, **kw)

        with patch.object(self.db, "guild", counting), self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_A, data, DM, replace_campaign_id=self.c)
        self.assertEqual(opened, 0)
        self.assertEqual(await self.snapshot(), before)

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
    """How often names were said, kept at the end of a session for hints (#126, #127)."""

    async def test_counted_per_session_and_never_a_new_version(self) -> None:
        from dmbot.memory.lookup import CampaignLookup
        from dmbot.memory.models import Heard

        a, b = await self.add("Belleros", status=CONFIRMED), await self.add("Cerric")
        gone = await self.add("Bellamy")
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        version = await self.memory.version(GUILD_A, self.c)
        kept = await self.memory.add_session_heard(
            GUILD_A,
            self.c,
            1_000,
            [Heard(a, 8, 2), Heard(a, 9, 1), Heard(b, 8, 1), Heard(gone, 8, 5), Heard(b, 8, 0)],
        )
        self.assertEqual(kept, 3)  # a rejected name and a zero count left out
        self.assertEqual(await self.memory.version(GUILD_A, self.c), version)  # no reload
        await self.memory.add_session_heard(GUILD_A, self.c, 2_000, [Heard(a, 8, 4)])
        data = await self.memory.lookup_data(GUILD_A, self.c)
        counts = {h.entity_id: (h.times, h.last_session_at) for h in data.heard}
        self.assertEqual(counts, {a: (7, 2_000), b: (1, 1_000)})
        self.assertEqual(data.recent_sessions, (2_000, 1_000))
        self.assertIn(a, CampaignLookup.build(data).heard)

    async def test_merged_names_count_for_the_one_kept(self) -> None:
        from dmbot.memory.models import Heard

        keep, gone = await self.add("Belleros", status=CONFIRMED), await self.add("Bellaros")
        await self.memory.add_session_heard(GUILD_A, self.c, 1_000, [Heard(gone, 8, 3)])
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        await self.memory.add_session_heard(GUILD_A, self.c, 2_000, [Heard(gone, 8, 1)])
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual([(h.entity_id, h.times) for h in data.heard], [(keep, 4)])

    async def test_undo_still_works_after_a_name_was_said(self) -> None:
        from dmbot.memory.models import Heard

        written = await self.memory.add_entity(
            GUILD_A, self.c, type="npc", name="Zephyr", source="dm", status=CONFIRMED
        )
        await self.memory.add_session_heard(GUILD_A, self.c, 1_000, [Heard(written.value.id, 8, 2)])
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)  # never refused
        self.assertEqual((await self.memory.lookup_data(GUILD_A, self.c)).heard, ())
        self.assertEqual(await self.count("memory_heard"), 0)

    async def test_another_campaign_or_server_never_sees_them(self) -> None:
        from dmbot.memory.models import Heard

        a = await self.add("Belleros", status=CONFIRMED)
        other = (await self.campaigns.create(GUILD_A, "Strahd", DM)).id
        await self.memory.add_session_heard(GUILD_A, self.c, 1_000, [Heard(a, 8, 1)])
        self.assertEqual((await self.memory.lookup_data(GUILD_A, other)).heard, ())
        # This campaign's name written under another campaign or server is skipped.
        self.assertEqual(
            await self.memory.add_session_heard(GUILD_A, other, 1, [Heard(a, 8, 1)]), 0
        )
        far = (await self.campaigns.create(GUILD_B, "Far Away", DM)).id
        skipped = await self.memory.add_session_heard(GUILD_B, far, 1, [Heard(a, 8, 1)])
        self.assertEqual(skipped, 0)
        self.assertEqual(await self.count("memory_heard", GUILD_B), 0)


class Renaming(MemoryTest):
    async def keys(self, entity: str) -> dict[str, str]:
        aliases = await self.memory.aliases(GUILD_A, self.c, entity_id=entity, include_secret=True)
        return {x.key: x.status for x in aliases}

    async def test_fix_spelling_and_undo_it(self) -> None:
        a = await self.add("Beleros", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, a, "Beleros", kind="full", source="dm", status=CONFIRMED
        )
        written = await self.memory.rename_entity(GUILD_A, self.c, a, "Belleros", source="dm")
        self.assertEqual(written.value.name, "Belleros")
        self.assertEqual(await self.keys(a), {"belleros": CONFIRMED})
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)  # one change, one undo
        entity = await self.memory.entity(GUILD_A, self.c, a)
        assert entity is not None
        self.assertEqual((entity.name, await self.keys(a)), ("Beleros", {"beleros": CONFIRMED}))

    async def test_the_right_spelling_already_another_name_becomes_the_name(self) -> None:
        a = await self.add("Beleros", status=CONFIRMED)
        for text in ("Beleros", "Belleros"):
            await self.memory.add_alias(
                GUILD_A, self.c, a, text, kind="full", source="dm", status=CONFIRMED
            )
        await self.memory.rename_entity(GUILD_A, self.c, a, "Belleros", source="dm")
        self.assertEqual(await self.keys(a), {"belleros": CONFIRMED})  # misspelling dropped

    async def test_another_name_becomes_the_main_name(self) -> None:
        a = await self.add("Belleros", status=CONFIRMED)
        for text in ("Belleros", "Bell"):
            await self.memory.add_alias(
                GUILD_A, self.c, a, text, kind="full", source="dm", status=CONFIRMED
            )
        bell = next(
            x for x in await self.memory.aliases(GUILD_A, self.c, entity_id=a) if x.key == "bell"
        )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_main_name(GUILD_A, self.c, a, bell.id, source="entitybot")
        written = await self.memory.set_main_name(GUILD_A, self.c, a, bell.id, source="dm")
        self.assertEqual(written.value.name, "Bell")
        self.assertEqual(await self.keys(a), {"bell": CONFIRMED, "belleros": CONFIRMED})

    async def test_joining_keeps_a_players_character_and_its_player(self) -> None:
        npc = await self.add("Bell Eros", status=CONFIRMED)
        pc = await self.add("Belleros", type="player_character", played_by=55, status=CONFIRMED)
        await self.memory.merge(GUILD_A, self.c, npc, pc, source="dm", dm_said_same=True)
        kept = await self.memory.entity(GUILD_A, self.c, npc)
        assert kept is not None
        self.assertEqual((kept.name, kept.played_by), ("Bell Eros", 55))
        other = await self.add("Kesh", type="player_character", played_by=66, status=CONFIRMED)
        with self.assertRaises(MemoryRuleError):  # two players' characters stay apart
            await self.memory.merge(GUILD_A, self.c, npc, other, source="dm", dm_said_same=True)

    async def test_joining_never_makes_a_secret_name_the_main_name(self) -> None:
        a = await self.add("Belleros", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, a, "the hooded stranger", kind="title", source="dm",
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        stranger = await self.add("the hooded stranger", status=CONFIRMED)
        before = await self.snapshot()
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge(GUILD_A, self.c, stranger, a, source="dm", dm_said_same=True)
        self.assertEqual(await self.snapshot(), before)  # the bulk moves went back too
        await self.memory.merge(GUILD_A, self.c, a, stranger, source="dm", dm_said_same=True)
        self.assertEqual(
            await self.keys(a), {"belleros": CONFIRMED, "the hooded stranger": CONFIRMED}
        )
        secret = await self.memory.aliases(GUILD_A, self.c, entity_id=a, include_secret=True)
        self.assertTrue(next(x for x in secret if x.key == "the hooded stranger").secret)

    async def test_the_main_name_is_never_made_secret_or_dropped(self) -> None:
        a = await self.add("Belleros", status=CONFIRMED)
        (own,) = await self.memory.aliases(GUILD_A, self.c, entity_id=a)
        for change in ({"secret": True}, {"status": REJECTED}):
            with self.assertRaises(MemoryRuleError):
                await self.memory.update_alias(GUILD_A, self.c, own.id, source="dm", **change)

    async def test_never_onto_a_secret_name_and_only_by_the_dm(self) -> None:
        a = await self.add("Belleros", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A,
            self.c,
            a,
            "the hooded stranger",
            kind="title",
            source="dm",
            status=CONFIRMED,
            secret=True,
        )
        with self.assertRaises(MemoryRuleError):
            await self.memory.rename_entity(GUILD_A, self.c, a, "The Hooded Stranger", source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.rename_entity(GUILD_A, self.c, a, "Bel", source="entitybot")


class LookupInPostgres(MemoryTest):
    async def test_whole_campaign_reads_never_join_names_to_entries(self) -> None:
        """#598: joined to a campaign with no table statistics yet (one just given a long
        list), Postgres compared every name with every entry: 1.4 s for 2,000 names. These
        reads filter by the live entries in Python instead."""
        a, b = await self.add("Belleros", status=CONFIRMED), await self.add("Cerric")
        await self.relate(a, "ally_of", b)
        sent: list[str] = []
        real = AsyncConnection.execute

        async def recording(conn: Any, query: Any, *args: Any, **kw: Any) -> Any:
            sent.append(query if isinstance(query, str) else query.as_string(conn))
            return await real(conn, query, *args, **kw)

        with patch.object(AsyncConnection, "execute", recording):
            data = await self.memory.lookup_data(GUILD_A, self.c)
            everyone = await self.memory.aliases(GUILD_A, self.c, include_secret=True)
            facts = await self.memory.relations(GUILD_A, self.c)
            one = await self.memory.aliases(GUILD_A, self.c, entity_id=a)
            its = await self.memory.relations(GUILD_A, self.c, entity_id=b)
            alike = await self.memory.sound_alikes(GUILD_A, self.c, sound_codes("Belleros"))
        self.assertTrue(sent)
        # No query reads names or facts together with entries (heard counts aside).
        joined = [
            q
            for q in sent
            if "memory_entities" in q
            and ("memory_aliases" in q or "memory_relations" in q)
            and "memory_heard" not in q
        ]
        self.assertEqual(joined, [])
        self.assertEqual((len(data.aliases), len(everyone), len(facts)), (2, 2, 1))
        self.assertEqual(([x.text for x in one], len(its)), (["Belleros"], 1))
        self.assertEqual(alike, [(a, "Belleros", "Belleros")])

    async def test_names_and_facts_of_a_rejected_entry_are_left_out(self) -> None:
        a, b = await self.add("Belleros", status=CONFIRMED), await self.add("Cerric")
        gone = await self.add("Bellamy", status=CONFIRMED)
        await self.relate(a, "ally_of", b)
        await self.relate(a, "enemy_of", gone)
        await self.memory.set_entity_status(GUILD_A, self.c, gone, REJECTED, source="dm")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertNotIn(gone, {x.entity_id for x in data.aliases})
        names = await self.memory.aliases(GUILD_A, self.c)
        self.assertNotIn(gone, {x.entity_id for x in names})
        self.assertEqual(await self.memory.aliases(GUILD_A, self.c, entity_id=gone), [])
        facts = await self.memory.relations(GUILD_A, self.c, entity_id=a)  # the other end's gone
        self.assertEqual([{f.subject_id, f.object_id} for f in facts], [{a, b}])
        self.assertEqual(
            await self.memory.sound_alikes(GUILD_A, self.c, sound_codes("Bellamy")), []
        )

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


class MergeMatchesTheOldWalk(MemoryTest):
    """#342 review: on seeded random graphs, the set-based merge leaves every table, flag
    and change-log row as the old row-by-row merge did (tests/merge_reference.py), and a
    bulk undo leaves the same as undoing one row at a time."""

    PREDICATES = ("located_in", "member_of", "ally_of", "enemy_of", "kin_of", "knows")

    async def build(self, campaign: str, seed: int) -> tuple[str, str]:
        """The same graph every time for a seed, ids and all."""
        rng = random.Random(seed)
        ids = (f"{n:032x}" for n in itertools.count(seed * 1_000_000))
        self.ids = patch("dmbot.memory.store.new_id", side_effect=lambda: next(ids))
        self.ids.start()
        self.addCleanup(self.ids.stop)
        self.now = 1_700_000_000

        async def step(write: Awaitable[Any]) -> Any:
            self.now += 1
            try:
                return await write
            except MemoryRuleError:
                return None

        types = ("npc", "npc", "npc", "place", "place", "spell")
        names = ["Keep", "Gone"] + [f"Someone {n}" for n in range(10)]
        ents = []
        for name in names:
            written = await step(
                self.memory.add_entity(
                    GUILD_A, campaign, type="npc" if name in ("Keep", "Gone") else
                    rng.choice(types), name=name, source="dm",
                )
            )  # fmt: skip
            ents.append(written.value.id)
        keep, gone = ents[0], ents[1]
        for n in range(8):  # names, some shared, some secret, some used by someone
            for owner in (keep, gone):
                if rng.random() < 0.6:
                    await step(
                        self.memory.add_alias(
                            GUILD_A, campaign, owner, f"Nick {n}", kind="short", source="dm",
                            secret=rng.random() < 0.3,
                            status=CONFIRMED if rng.random() < 0.3 else PROPOSED,
                        )
                    )  # fmt: skip
        for n in range(3):
            await step(
                self.memory.add_alias(
                    GUILD_A, campaign, rng.choice(ents[2:]), f"Called {n}", kind="nickname",
                    source="dm", used_by=gone,
                )
            )  # fmt: skip
        facts = []
        for _ in range(40):
            a = rng.choice([keep, gone, gone, *ents[2:]])
            b = rng.choice([keep, gone, *ents[2:]])
            pred = rng.choice(self.PREDICATES)
            dm = rng.random() < 0.5
            written = await step(
                self.memory.add_relation(
                    GUILD_A, campaign, a, pred, b, source="dm" if dm else "cleaner",
                    confidence=1.0 if dm else 0.5, secret=rng.random() < 0.2,
                    status=CONFIRMED if dm and rng.random() < 0.5 else PROPOSED,
                )
            )  # fmt: skip
            if written is not None:
                facts.append(written.value[0].id)
        for fact_id in rng.sample(facts, k=min(4, len(facts))):
            await step(
                self.memory.update_relation(
                    GUILD_A, campaign, fact_id, status=REJECTED, source="dm"
                )
            )
        for n in range(5):
            await step(
                self.memory.add_mention(
                    GUILD_A, campaign, gone, line_ref=f"s1:{n}", span=(0, 4),
                    confidence=0.9, method="exact", source="cleaner",
                )
            )  # fmt: skip
        await step(
            self.memory.add_correction(
                GUILD_A, campaign, "gon", action="fix", entity_id=gone, source="dm"
            )
        )
        self.ids.stop()
        return keep, gone

    async def logged(self, campaign: str, batch: int | None) -> list[str]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT table_name, row_id, op, before, after FROM memory_changes"
                " WHERE campaign_id = %s AND batch = %s",
                (campaign, batch),
            )
            return sorted(json.dumps(dict(r), sort_keys=True) for r in await cur.fetchall())

    async def test_facts_come_back_in_id_order_whatever_the_disk_order(self) -> None:
        """#476: the facts a check reads (and so the flags it makes) come in id order,
        never in the order the rows sit on disk."""
        from dmbot.memory import store as store_module

        # Falling ids: each fact is written after one with a higher id, so disk order is
        # the reverse of id order.
        ids = (f"{n:032x}" for n in itertools.count(10**6, -1))
        with patch("dmbot.memory.store.new_id", side_effect=lambda: next(ids)):
            hub = await self.add("Bryn Shander", type="place")
            npcs = [await self.add(f"Guard {n}") for n in range(6)]
            facts = [(await self.relate(npc, "located_in", hub)).value[0].id for npc in npcs]
        self.assertEqual(facts, sorted(facts, reverse=True))
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            got = [r.id for r in await store_module._relations_touching(w, hub)]
        self.assertEqual(got, sorted(facts))

    async def test_seeded_graphs(self) -> None:
        from tests import merge_reference

        compared = 0
        for seed in range(8):
            with self.subTest(seed=seed):
                old = (await self.campaigns.create(GUILD_A, f"Old {seed}", DM)).id
                new = (await self.campaigns.create(GUILD_A, f"New {seed}", DM)).id
                keep, gone = await self.build(old, seed)
                self.assertEqual(await self.build(new, seed), (keep, gone))
                self.assertEqual(
                    await self.snapshot(GUILD_A, old), await self.snapshot(GUILD_A, new)
                )
                start = seed * 1_000_000 + 900_000
                with patch("dmbot.memory.store.new_id", side_effect=(
                    f"{n:032x}" for n in itertools.count(start)
                ).__next__):  # fmt: skip
                    try:
                        old_batch = await merge_reference.merge(
                            self.memory, GUILD_A, old, keep, gone, source="dm", dm_said_same=True
                        )
                    except MemoryRuleError:
                        old_batch = None
                with patch("dmbot.memory.store.new_id", side_effect=(
                    f"{n:032x}" for n in itertools.count(start)
                ).__next__):  # fmt: skip
                    if old_batch is None:  # refused before: refused now, nothing changed
                        snap = await self.snapshot(GUILD_A, new)
                        with self.assertRaises(MemoryRuleError):
                            await self.memory.merge(
                                GUILD_A, new, keep, gone, source="dm", dm_said_same=True
                            )
                        self.assertEqual(await self.snapshot(GUILD_A, new), snap)
                        continue
                    merged = await self.memory.merge(
                        GUILD_A, new, keep, gone, source="dm", dm_said_same=True
                    )
                self.assertEqual(
                    await self.snapshot(GUILD_A, old), await self.snapshot(GUILD_A, new)
                )
                self.assertEqual(
                    await self.logged(old, old_batch), await self.logged(new, merged.batch)
                )
                # Undo: one row at a time in the old copy, in bulk in the new one.
                assert merged.batch is not None
                with patch("dmbot.memory._changes._undo_run_or_not", AsyncMock(return_value=False)):
                    row_by_row = await self.memory.undo(GUILD_A, old, old_batch, source="dm")
                bulk = await self.memory.undo(GUILD_A, new, merged.batch, source="dm")
                self.assertEqual(
                    await self.snapshot(GUILD_A, old), await self.snapshot(GUILD_A, new)
                )
                self.assertEqual(
                    await self.logged(old, row_by_row.batch), await self.logged(new, bulk.batch)
                )
                compared += 1
        self.assertGreaterEqual(compared, 5)  # most seeds merge


class Pruning(MemoryTest):
    """The change log keeps `keep_days` of undo history (#164, part 3)."""

    DAY = 24 * 60 * 60

    async def batches(self, guild: int = GUILD_A, campaign: str | None = None) -> set[int]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT batch FROM memory_changes WHERE campaign_id = %s",
                (campaign or self.c,),
            )
            return {int(r["batch"]) for r in await cur.fetchall()}

    async def test_old_batches_go_and_recent_ones_stay(self) -> None:
        old = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="Old", source="dm")
        self.now += 30 * self.DAY  # under 30 days old when pruned below: kept
        edge = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="Edge", source="dm")
        self.now += 1
        new = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="New", source="dm")
        before = await self.snapshot()

        self.assertGreater(await self.memory.prune_changes(GUILD_A, self.c), 0)

        self.assertEqual(await self.batches(), {edge.batch, new.batch})
        self.assertEqual(await self.snapshot(), before)  # only the log changes
        assert old.batch is not None and new.batch is not None
        with self.assertRaisesRegex(TooLateToUndo, "Undo works for 30 days"):
            await self.memory.undo(GUILD_A, self.c, old.batch)
        await self.memory.undo(GUILD_A, self.c, new.batch)
        self.assertEqual(await self.memory.prune_changes(GUILD_A, self.c), 0)

    async def test_an_undo_kept_after_its_batch_went_still_redoes(self) -> None:
        before = await self.snapshot()
        added = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm")
        after = await self.snapshot()
        self.now += 31 * self.DAY
        assert added.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, added.batch)
        assert undone.batch is not None

        await self.memory.prune_changes(GUILD_A, self.c)

        self.assertEqual(await self.batches(), {undone.batch})
        with self.assertRaisesRegex(MemoryRuleError, "already undone"):
            await self.memory.undo(GUILD_A, self.c, added.batch)  # never undone twice
        self.assertEqual(await self.snapshot(), before)
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_an_undo_goes_with_its_batch(self) -> None:
        added = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm")
        assert added.batch is not None
        await self.memory.undo(GUILD_A, self.c, added.batch)
        self.now += 31 * self.DAY
        self.assertGreater(await self.memory.prune_changes(GUILD_A, self.c), 0)
        self.assertEqual(await self.batches(), set())

    async def test_other_campaigns_and_servers_are_untouched(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        away = (await self.campaigns.create(GUILD_B, "Away", DM)).id
        await self.add("Here")
        await self.add("There", campaign=other)
        await self.memory.add_entity(GUILD_B, away, type="npc", name="Away", source="dm")
        self.now += 31 * self.DAY

        await self.memory.prune_changes(GUILD_A, self.c)

        self.assertEqual(await self.batches(), set())
        self.assertEqual(len(await self.batches(campaign=other)), 1)
        self.assertEqual(len(await self.batches(GUILD_B, away)), 1)

    async def test_a_list_too_old_to_undo_is_told_apart_from_a_wrong_batch(self) -> None:
        from dmbot.memory.models import NewName

        listed = await self.memory.add_names(
            GUILD_A, self.c, [NewName("Bryn Shander", "place", CONFIRMED)], source="dm"
        )
        x = await self.add("X")
        other = await self.memory.set_entity_status(GUILD_A, self.c, x, CONFIRMED, source="dm")
        assert listed.batch is not None and other.batch is not None
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.undo_names(GUILD_A, self.c, other.batch)  # not a list
        self.assertNotIsInstance(refused.exception, TooLateToUndo)
        self.now += 31 * self.DAY
        await self.memory.prune_changes(GUILD_A, self.c)
        with self.assertRaisesRegex(TooLateToUndo, "Undo works for 30 days"):
            await self.memory.undo_names(GUILD_A, self.c, listed.batch)

    async def test_undo_from_before_a_backup_replaced_the_campaign(self) -> None:
        added = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm")
        backup = await self.campaigns.export(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_A, backup, DM, replace_campaign_id=self.c)
        assert added.batch is not None
        with self.assertRaisesRegex(TooLateToUndo, "not from before a backup was loaded"):
            await self.memory.undo(GUILD_A, self.c, added.batch)

    async def test_an_undo_racing_a_prune_keeps_its_own_rows(self) -> None:
        added = await self.memory.add_entity(GUILD_A, self.c, type="npc", name="X", source="dm")
        after = await self.snapshot()
        self.now += 31 * self.DAY
        assert added.batch is not None
        async with self.db.guild(GUILD_A) as conn:  # the prune, not yet committed
            await conn.execute(
                "DELETE FROM memory_changes WHERE campaign_id = %s AND made_at < %s",
                (self.c, self.now - 30 * self.DAY),
            )
            undone = await self.memory.undo(GUILD_A, self.c, added.batch)  # still sees them
        assert undone.batch is not None
        self.assertEqual(await self.batches(), {undone.batch})
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # redo
        self.assertEqual(await self.snapshot(), after)

    async def test_the_number_of_days_is_a_setting(self) -> None:
        memory = MemoryStore(self.db, clock=lambda: self.now, keep_days=1)
        await self.add("X")
        self.now += self.DAY
        self.assertEqual(await memory.prune_changes(GUILD_A, self.c), 0)
        self.now += 1
        self.assertGreater(await memory.prune_changes(GUILD_A, self.c), 0)
        self.assertEqual(await self.batches(), set())
