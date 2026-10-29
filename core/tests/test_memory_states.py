"""Story memory 5a, slice 1 (#1267): state facts, one-of-a-kind items with `carries`, and the
checks that read them. The first half needs no database; the second runs against Postgres."""

import json
import unittest
from typing import Any

from psycopg import errors as pg_errors

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory.checks import (
    ACTS_AFTER_END,
    HELD_TWICE,
    PLACE_LOOP,
    TOO_MANY,
    Ended,
    Nesting,
    check_relation,
)
from dmbot.memory.models import (
    PROPOSED,
    Entity,
    MemoryRuleError,
    Relation,
    looks_named,
)
from dmbot.memory.ontology import (
    CORE_PREDICATES,
    DM_ONLY_CHANGES,
    STATE_CHANGES,
    Ontology,
    check_condition_change,
    conditions_for,
)
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

ONTO = Ontology.build()


def fact(
    n: int, subject: str, predicate: str, obj: str, *, at: int | None = None, made: int = 1_000
) -> Relation:
    return Relation(
        f"{n:032x}", subject, predicate, obj, "", 1.0, "confirmed", "dm", (), at, None, None,
        None, False, made,
    )  # fmt: skip


class Names(unittest.TestCase):
    def test_a_proper_name_is_one_of_a_kind_and_a_kind_of_thing_is_not(self) -> None:
        for name in ("Heartseeker", "The Sun Blade", "Sword of Kas", "a Sun Blade"):
            self.assertTrue(looks_named(name), name)
        for name in ("a longbow", "longbow", "the rope", "a 50-foot rope"):
            self.assertFalse(looks_named(name), name)

    def test_the_dms_word_beats_the_guess(self) -> None:
        def item(name: str, said: bool | None) -> Entity:
            return Entity("e", "item", name, "", "confirmed", None, "dm", 0, unique_item=said)

        self.assertTrue(item("Heartseeker", None).is_unique)
        self.assertFalse(item("a longbow", None).is_unique)
        self.assertTrue(item("a longbow", True).is_unique)
        self.assertFalse(item("Heartseeker", False).is_unique)


class StateRules(unittest.TestCase):
    def change(self, kind: str, old: str | None, new: str, source: str = "dm") -> None:
        check_condition_change(ONTO, kind, "Cerric", old, new, source)

    def test_beings_and_things_have_their_own_conditions(self) -> None:
        self.assertEqual(
            conditions_for(ONTO, "character"),
            ("alive", "dead", "missing", "captured", "transformed", "unknown"),
        )
        self.assertEqual(conditions_for(ONTO, "item"), ("intact", "destroyed", "lost", "unknown"))
        self.assertEqual(conditions_for(ONTO, "place"), conditions_for(ONTO, "item"))
        for kind in ("faction", "event", "concept", "person"):
            self.assertIsNone(conditions_for(ONTO, kind), kind)
            with self.assertRaises(MemoryRuleError):
                self.change(kind, None, "alive")

    def test_a_condition_the_kind_cannot_have_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            self.change("character", None, "intact")
        with self.assertRaises(MemoryRuleError):
            self.change("item", None, "alive")

    def test_a_first_condition_can_be_anything_the_kind_has(self) -> None:
        for c in conditions_for(ONTO, "character") or ():
            self.change("character", None, c, "entitybot")

    def test_every_listed_change_is_between_conditions_of_one_kind(self) -> None:
        beings, things = (
            set(conditions_for(ONTO, "character") or ()),
            set(conditions_for(ONTO, "item") or ()),
        )
        threads = set(conditions_for(ONTO, "quest") or ())
        for old, news in STATE_CHANGES.items():
            home = (
                threads
                if old in threads
                else beings
                if old in beings and not news - beings
                else things
            )
            self.assertLessEqual(news, home | {"unknown"}, old)
        self.assertLessEqual(
            DM_ONLY_CHANGES, {(o, n) for o, ns in STATE_CHANGES.items() for n in ns}
        )

    def test_dead_to_alive_is_refused_without_the_dms_say_so(self) -> None:
        for source in ("entitybot", "cleaner", "scan", "character"):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(MemoryRuleError, "Only the DM"),
            ):
                self.change("character", "dead", "alive", source)
        self.change("character", "dead", "alive", "dm")

    def test_destroyed_to_intact_is_refused_without_the_dms_say_so(self) -> None:
        with self.assertRaises(MemoryRuleError):
            self.change("item", "destroyed", "intact", "entitybot")
        self.change("item", "destroyed", "intact", "dm")

    def test_ordinary_changes_need_no_one(self) -> None:
        self.change("character", "alive", "dead", "entitybot")
        self.change("character", "missing", "alive", "entitybot")  # news, not a contradiction
        self.change("item", "intact", "lost", "entitybot")
        self.change("item", "lost", "intact", "entitybot")

    def test_a_change_the_table_does_not_list_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            self.change("item", "destroyed", "lost", "dm")
        with self.assertRaises(MemoryRuleError):
            self.change("character", "dead", "missing", "dm")

    def test_the_same_condition_again_is_no_change(self) -> None:
        self.change("character", "dead", "dead", "entitybot")


class Unique(unittest.TestCase):
    def carries(self, n: int, who: str, what: str, **kw: Any) -> Relation:
        return fact(n, who, "carries", what, **kw)

    def check(self, new: Relation, others: list[Relation], *, unique: bool) -> list[str]:
        return [
            p.kind
            for p in check_relation(ONTO, new, "character", "item", others, object_unique=unique)
        ]

    def test_two_characters_carrying_a_longbow_is_fine(self) -> None:
        mira, snot = self.carries(1, "mira", "bow"), self.carries(2, "snot", "bow")
        self.assertEqual(self.check(snot, [mira], unique=False), [])

    def test_two_characters_carrying_heartseeker_is_a_flag(self) -> None:
        mira, snot = self.carries(1, "mira", "bow"), self.carries(2, "snot", "bow")
        problems = check_relation(ONTO, snot, "character", "item", [mira], object_unique=True)
        self.assertEqual([(p.kind, p.other_id) for p in problems], [(HELD_TWICE, mira.id)])

    def test_one_holder_after_another_is_fine_when_the_first_ended(self) -> None:
        ended = Relation(
            f"{1:032x}", "mira", "carries", "bow", "", 1.0, "confirmed", "dm", (), 10, 20, None,
            None, False, 1_000,
        )  # fmt: skip
        later = self.carries(2, "snot", "bow", at=30)
        self.assertEqual(self.check(later, [ended], unique=True), [])

    def test_the_same_holder_twice_is_not_two_holders(self) -> None:
        a, b = self.carries(1, "mira", "bow"), self.carries(2, "mira", "bow")
        self.assertEqual(self.check(b, [a], unique=True), [])

    def test_a_rejected_holder_does_not_count(self) -> None:
        mira = Relation(
            f"{1:032x}", "mira", "carries", "bow", "", 1.0, "rejected", "dm", (), None, None,
            None, None, False, 1,
        )  # fmt: skip
        self.assertEqual(self.check(self.carries(2, "snot", "bow"), [mira], unique=True), [])

    def test_owning_has_no_limit(self) -> None:
        a, b = fact(1, "mira", "owns", "bow"), fact(2, "snot", "owns", "bow")
        problems = check_relation(ONTO, b, "character", "item", [a], object_unique=True)
        self.assertEqual(problems, [])

    def test_carries_is_a_being_holding_an_item(self) -> None:
        carries = ONTO.predicates["carries"]
        self.assertEqual((carries.subject_types, carries.object_types), (("character",), ("item",)))
        self.assertEqual(carries.max_per_object, 1)
        self.assertIsNone(ONTO.predicates["owns"].max_per_object)
        self.assertEqual([p.key for p in CORE_PREDICATES if p.max_per_object], ["carries"])

    def test_a_place_cannot_carry(self) -> None:
        problems = check_relation(
            ONTO, self.carries(1, "inn", "bow"), "place", "item", [], object_unique=False
        )
        self.assertEqual([p.kind for p in problems], ["wrong_subject"])


class Places(unittest.TestCase):
    def nesting(self, *pairs: tuple[str, str]) -> Nesting:
        return Nesting(pairs)

    def check(
        self, new: Relation, others: list[Relation], nesting: Nesting, kinds: tuple[str, str]
    ) -> list[str]:
        return [
            p.kind for p in check_relation(ONTO, new, kinds[0], kinds[1], others, nesting=nesting)
        ]

    def test_an_inn_in_a_town_in_a_region_is_fine(self) -> None:
        nest = self.nesting(("inn", "town"), ("town", "region"))
        new = fact(2, "inn", "located_in", "town")
        self.assertEqual(self.check(new, [], nest, ("place", "place")), [])
        self.assertEqual(nest.above("inn"), {"town", "region"})

    def test_a_loop_between_places_is_a_flag(self) -> None:
        nest = self.nesting(("inn", "town"), ("town", "region"))
        new = fact(3, "region", "located_in", "inn")
        self.assertEqual(self.check(new, [], nest, ("place", "place")), [PLACE_LOOP])

    def test_a_loop_cannot_hang_the_walk(self) -> None:
        nest = self.nesting(("a", "b"), ("b", "c"), ("c", "a"))
        self.assertEqual(nest.above("a"), {"a", "b", "c"})

    def test_a_character_in_the_inn_and_the_town_it_is_in_is_in_one_place(self) -> None:
        nest = self.nesting(("inn", "town"))
        in_inn = fact(1, "mira", "located_in", "inn")
        in_town = fact(2, "mira", "located_in", "town")
        self.assertEqual(self.check(in_town, [in_inn], nest, ("character", "place")), [])

    def test_a_character_in_two_places_that_are_not_nested_is_a_flag(self) -> None:
        nest = self.nesting(("inn", "town"))
        in_inn = fact(1, "mira", "located_in", "inn")
        in_keep = fact(2, "mira", "located_in", "keep")
        self.assertEqual(self.check(in_keep, [in_inn], nest, ("character", "place")), [TOO_MANY])

    def test_two_places_inside_each_other_through_a_chain_count_as_nested(self) -> None:
        nest = self.nesting(("room", "inn"), ("inn", "town"), ("town", "region"))
        a = fact(1, "mira", "located_in", "room")
        b = fact(2, "mira", "located_in", "region")
        self.assertEqual(self.check(b, [a], nest, ("character", "place")), [])

    def test_without_the_places_the_old_rule_stands(self) -> None:
        a, b = fact(1, "mira", "located_in", "inn"), fact(2, "mira", "located_in", "town")
        problems = check_relation(ONTO, b, "character", "place", [a])
        self.assertEqual([p.kind for p in problems], [TOO_MANY])

    def test_a_loop_is_only_between_places(self) -> None:
        nest = self.nesting(("inn", "town"))
        new = fact(2, "town", "located_in", "inn")
        self.assertEqual(self.check(new, [], nest, ("character", "place")), [])


class Acting(unittest.TestCase):
    def check(self, new: Relation, ended: dict[str, int]) -> list[str]:
        marks = {k: Ended(v, v) for k, v in ended.items()}
        return [
            p.kind for p in check_relation(ONTO, new, "character", "character", [], ended=marks)
        ]

    def test_a_dead_character_acting_after_dying_is_a_flag(self) -> None:
        later = fact(1, "cerric", "serves", "mira", at=200)
        self.assertEqual(self.check(later, {"cerric": 100}), [ACTS_AFTER_END])

    def test_a_fact_from_before_or_the_same_session_is_fine(self) -> None:
        self.assertEqual(
            self.check(fact(1, "cerric", "serves", "mira", at=50), {"cerric": 100}), []
        )
        self.assertEqual(
            self.check(fact(2, "cerric", "serves", "mira", at=100), {"cerric": 100}), []
        )

    def test_a_fact_with_no_date_is_dated_when_it_was_stored(self) -> None:
        self.assertEqual(
            self.check(fact(1, "cerric", "serves", "mira", made=500), {"cerric": 100}),
            [ACTS_AFTER_END],
        )
        self.assertEqual(
            self.check(fact(2, "cerric", "serves", "mira", made=50), {"cerric": 100}), []
        )

    def test_being_served_by_the_dead_or_related_to_them_is_not_acting(self) -> None:
        self.assertEqual(
            self.check(fact(1, "mira", "serves", "cerric", at=200), {"cerric": 100}), []
        )
        self.assertEqual(
            self.check(fact(2, "cerric", "kin_of", "mira", at=200), {"cerric": 100}), []
        )

    def test_a_two_way_fact_acts_with_both_ends(self) -> None:
        later = fact(1, "cerric", "ally_of", "mira", at=200)
        self.assertEqual(self.check(later, {"mira": 100}), [ACTS_AFTER_END])

    def test_nothing_ended_nothing_flagged(self) -> None:
        self.assertEqual(self.check(fact(1, "cerric", "serves", "mira", at=200), {}), [])


class Storage(MemoryTest):
    async def person(self, name: str = "Cerric", **kw: Any) -> str:
        return await self.add(name, **kw)

    async def flags(self, kind: str | None = None) -> list[Any]:
        found = await self.memory.flags(GUILD_A, self.c)
        return [f for f in found if kind is None or f.kind == kind]

    # ---- state facts ------------------------------------------------------------------

    async def test_a_new_condition_closes_the_old_and_the_history_is_kept(self) -> None:
        a = await self.person()
        self.now += 10
        first = await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="entitybot")
        self.now += 10
        second = await self.memory.set_condition(
            GUILD_A, self.c, a, "dead", source="entitybot", from_session_at=777
        )
        assert first.value[0] is not None and second.value[0] is not None
        history = await self.memory.state_history(GUILD_A, self.c, a)
        self.assertEqual(
            [(s.condition, s.source) for s in history],
            [("alive", "entitybot"), ("dead", "entitybot")],
        )
        self.assertEqual([s.closed_at for s in history], [self.now, None])
        self.assertEqual((history[1].from_session_at, history[1].since), (777, 777))
        self.assertEqual(history[0].since, history[0].created_at)
        current = await self.memory.states(GUILD_A, self.c)
        self.assertEqual({k: v.condition for k, v in current.items()}, {a: "dead"})

    async def test_saying_the_same_again_changes_nothing(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        again = await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        self.assertEqual((again.value, again.batch), ((None, []), None))
        self.assertEqual(len(await self.memory.state_history(GUILD_A, self.c, a)), 1)

    async def test_dead_to_alive_needs_the_dm(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="entitybot")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="entitybot")
        back = await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="dm")
        assert back.value[0] is not None
        self.assertEqual(back.value[0].condition, "alive")

    async def test_a_proposal_does_not_change_what_the_dm_said(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="dm")
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM"):
            await self.memory.set_condition(GUILD_A, self.c, a, "missing", source="entitybot")
        await self.memory.set_condition(GUILD_A, self.c, a, "missing", source="dm")

    async def test_only_characters_items_and_places_have_a_condition(self) -> None:
        group = await self.add("The Harpers", type="faction")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(GUILD_A, self.c, group, "alive", source="dm")
        tower = await self.add("Tower", type="place")
        await self.memory.set_condition(GUILD_A, self.c, tower, "destroyed", source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(GUILD_A, self.c, tower, "dead", source="dm")

    async def test_a_condition_on_a_missing_or_rejected_entry_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(GUILD_A, self.c, "f" * 32, "dead", source="dm")

    async def test_a_bad_date_is_refused(self) -> None:
        a = await self.person()
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(
                GUILD_A, self.c, a, "dead", source="dm", from_session_at=-1
            )

    async def test_the_database_allows_one_current_condition_for_each_entry(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="dm")
        with self.assertRaises(pg_errors.UniqueViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO memory_states (guild_id, campaign_id, id, entity_id, condition,"
                    " source, created_at) VALUES (%s, %s, %s, %s, 'dead', 'dm', 1)",
                    (GUILD_A, self.c, "1" * 32, a),
                )

    async def test_a_changed_kind_closes_a_condition_it_cannot_keep(self) -> None:
        thing = await self.add("Heartseeker", type="item")
        await self.memory.set_condition(GUILD_A, self.c, thing, "intact", source="dm")
        await self.memory.set_entity_type(GUILD_A, self.c, thing, "character", source="dm")
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})
        self.assertEqual(len(await self.memory.state_history(GUILD_A, self.c, thing)), 1)

    async def test_a_merged_entry_brings_its_condition_unless_the_kept_one_has_its_own(
        self,
    ) -> None:
        keep, gone, other = (
            await self.add("Belleros"),
            await self.add("Bell"),
            await self.add("Bel"),
        )
        await self.memory.set_condition(GUILD_A, self.c, gone, "dead", source="dm")
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        states = await self.memory.states(GUILD_A, self.c)
        self.assertEqual({k: v.condition for k, v in states.items()}, {keep: "dead"})
        await self.memory.set_condition(GUILD_A, self.c, other, "alive", source="dm")
        third = await self.add("Belle")
        await self.memory.set_condition(GUILD_A, self.c, third, "missing", source="dm")
        await self.memory.merge(GUILD_A, self.c, other, third, source="dm", dm_said_same=True)
        states = await self.memory.states(GUILD_A, self.c, [other])
        self.assertEqual(states[other].condition, "alive")

    async def test_undo_of_a_state_change_puts_the_old_one_back(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="dm")
        before = await self.snapshot()
        died = await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        assert died.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, died.batch)
        self.assertEqual(await self.snapshot(), before)
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # undoing the undo redoes it
        states = await self.memory.states(GUILD_A, self.c)
        self.assertEqual(states[a].condition, "dead")

    async def test_undo_of_the_first_condition_removes_it(self) -> None:
        a = await self.person()
        first = await self.memory.set_condition(GUILD_A, self.c, a, "alive", source="dm")
        assert first.batch is not None
        await self.memory.undo(GUILD_A, self.c, first.batch)
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})

    # ---- one-of-a-kind items ----------------------------------------------------------

    async def two_carry(self, item: str) -> tuple[Any, Any]:
        mira, snot = await self.add("Mira"), await self.add("Snot")
        first = await self.relate(mira, "carries", item)
        second = await self.relate(snot, "carries", item)
        return first, second

    async def test_two_carrying_a_longbow_is_fine_and_two_carrying_heartseeker_is_flagged(
        self,
    ) -> None:
        longbow = await self.add("a longbow", type="item")
        await self.two_carry(longbow)
        self.assertEqual(await self.flags(), [])
        heartseeker = await self.add("Heartseeker", type="item")
        first, second = await self.two_carry(heartseeker)
        found = await self.flags(HELD_TWICE)
        self.assertEqual(
            [(f.relation_id, f.other_id) for f in found], [(second.value[0].id, first.value[0].id)]
        )

    async def test_the_dm_can_say_a_named_item_is_not_unique_and_the_other_way(self) -> None:
        item = await self.add("Heartseeker", type="item")
        await self.memory.set_unique(GUILD_A, self.c, item, False, source="dm")
        await self.two_carry(item)
        self.assertEqual(await self.flags(), [])
        plain = await self.add("a longbow", type="item")
        await self.memory.set_unique(GUILD_A, self.c, plain, True, source="dm")
        await self.two_carry(plain)
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)

    async def test_switching_unique_on_flags_what_two_already_carry(self) -> None:
        item = await self.add("a longbow", type="item")
        await self.two_carry(item)
        self.assertEqual(await self.flags(), [])
        written = await self.memory.set_unique(GUILD_A, self.c, item, True, source="dm")
        self.assertEqual([f.kind for f in written.value[1]], [HELD_TWICE])
        again = await self.memory.set_unique(GUILD_A, self.c, item, True, source="dm")
        self.assertEqual(again.value[1], [])
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)

    async def test_the_flag_goes_when_the_dm_switches_it_off(self) -> None:
        item = await self.add("Heartseeker", type="item")
        await self.two_carry(item)
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)
        await self.memory.set_unique(GUILD_A, self.c, item, False, source="dm")
        self.assertEqual(await self.flags(), [])

    async def test_only_the_dm_sets_it_and_only_on_an_item(self) -> None:
        item = await self.add("Heartseeker", type="item")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_unique(GUILD_A, self.c, item, False, source="entitybot")
        with self.assertRaisesRegex(MemoryRuleError, "item"):
            await self.memory.set_unique(GUILD_A, self.c, await self.person(), True, source="dm")

    async def test_the_flag_shows_after_the_recheck_too(self) -> None:
        item = await self.add("Heartseeker", type="item")
        await self.two_carry(item)
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(closed.value, [])  # still true, so still open
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)

    async def test_the_older_word_for_holding_still_means_owning(self) -> None:
        # "owns" lost its "carries" synonym to the new relationship; "has" is still its own.
        item = await self.add("Heartseeker", type="item")
        mira = await self.add("Mira")
        owned = await self.relate(mira, "owns", item)
        self.assertEqual(owned.value[1], [])

    # ---- places -----------------------------------------------------------------------

    async def test_nesting(self) -> None:
        inn = await self.add("Rusty Anchor", type="place")
        town = await self.add("Port Nyanzaru", type="place")
        region = await self.add("Chult", type="place")
        await self.relate(inn, "located_in", town)
        await self.relate(town, "located_in", region)
        mira = await self.add("Mira")
        await self.relate(mira, "located_in", inn)
        await self.relate(mira, "located_in", town)  # the inn is in the town: one place
        await self.relate(mira, "located_in", region)
        self.assertEqual(await self.flags(), [])
        loop = await self.relate(region, "located_in", inn)
        self.assertEqual([f.kind for f in loop.value[1]], [PLACE_LOOP])

    async def test_a_character_in_two_unrelated_places_is_still_flagged(self) -> None:
        keep, inn = await self.add("Keep", type="place"), await self.add("Inn", type="place")
        mira = await self.add("Mira")
        await self.relate(mira, "located_in", keep)
        again = await self.relate(mira, "located_in", inn)
        self.assertEqual([f.kind for f in again.value[1]], [TOO_MANY])

    async def test_a_flag_closes_when_the_places_turn_out_to_be_nested(self) -> None:
        keep, inn = await self.add("Keep", type="place"), await self.add("Inn", type="place")
        mira = await self.add("Mira")
        await self.relate(mira, "located_in", keep)
        await self.relate(mira, "located_in", inn)
        self.assertEqual(len(await self.flags(TOO_MANY)), 1)
        await self.relate(inn, "located_in", keep)  # the inn is inside the keep after all
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(len(closed.value), 1)

    # ---- state-gated actions ----------------------------------------------------------

    async def test_a_dead_character_acting_after_dying_is_flagged_and_brought_back_clears_it(
        self,
    ) -> None:
        cerric, mira = await self.person(), await self.add("Mira")
        await self.memory.set_condition(
            GUILD_A, self.c, cerric, "dead", source="dm", from_session_at=100
        )
        later = await self.relate(cerric, "serves", mira, from_session_at=200)
        self.assertEqual([f.kind for f in later.value[1]], [ACTS_AFTER_END])
        before = await self.relate(cerric, "ally_of", mira, from_session_at=50)
        self.assertEqual(before.value[1], [])
        back = await self.memory.set_condition(GUILD_A, self.c, cerric, "alive", source="dm")
        self.assertEqual(await self.flags(ACTS_AFTER_END), [])  # answered: "brought back"
        assert back.batch is not None
        await self.memory.undo(GUILD_A, self.c, back.batch)  # un-answering brings it back
        self.assertEqual(len(await self.flags(ACTS_AFTER_END)), 1)

    async def test_dying_flags_what_the_character_already_did_afterwards(self) -> None:
        cerric, mira = await self.person(), await self.add("Mira")
        await self.relate(cerric, "serves", mira, from_session_at=200)
        await self.relate(cerric, "knows", mira, from_session_at=10)
        written = await self.memory.set_condition(
            GUILD_A, self.c, cerric, "dead", source="dm", from_session_at=100
        )
        self.assertEqual([f.kind for f in written.value[1]], [ACTS_AFTER_END])
        again = await self.memory.set_condition(GUILD_A, self.c, cerric, "alive", source="dm")
        self.assertEqual(again.value[1], [])
        self.assertEqual(await self.flags(), [])

    async def test_a_soft_condition_stops_nothing(self) -> None:
        cerric, mira = await self.person(), await self.add("Mira")
        await self.memory.set_condition(
            GUILD_A, self.c, cerric, "missing", source="dm", from_session_at=100
        )
        later = await self.relate(cerric, "serves", mira, from_session_at=200)
        self.assertEqual(later.value[1], [])

    async def test_a_destroyed_item_is_dead_too(self) -> None:
        sword, mira = await self.add("Heartseeker", type="item"), await self.add("Mira")
        await self.memory.set_condition(
            GUILD_A, self.c, sword, "destroyed", source="dm", from_session_at=100
        )
        # Items rarely act; `appears_in` is the one an item can take part in.
        battle = await self.add("The Battle", type="event")
        later = await self.relate(sword, "appears_in", battle, from_session_at=200)
        self.assertEqual([f.kind for f in later.value[1]], [ACTS_AFTER_END])
        del mira

    async def test_the_recheck_keeps_a_flag_that_is_still_true(self) -> None:
        cerric, mira = await self.person(), await self.add("Mira")
        await self.memory.set_condition(
            GUILD_A, self.c, cerric, "dead", source="dm", from_session_at=100
        )
        await self.relate(cerric, "serves", mira, from_session_at=200)
        closed = await self.memory.resolve_stale_flags(GUILD_A, self.c)
        self.assertEqual(closed.value, [])
        self.assertEqual(len(await self.flags(ACTS_AFTER_END)), 1)

    # ---- isolation --------------------------------------------------------------------

    async def test_another_campaign_and_server_see_and_reach_no_states(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        self.assertEqual(await self.memory.states(GUILD_A, other), {})
        self.assertEqual(await self.memory.state_history(GUILD_A, other, a), [])
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(GUILD_A, other, a, "alive", source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.states(GUILD_B, self.c)
        self.assertEqual(await self.count("memory_states", GUILD_B), 0)
        self.assertEqual(await self.count("memory_states"), 1)

    async def test_the_database_refuses_a_state_on_another_campaigns_entry(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        a = await self.person()
        with self.assertRaises(pg_errors.ForeignKeyViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO memory_states (guild_id, campaign_id, id, entity_id, condition,"
                    " source, created_at) VALUES (%s, %s, %s, %s, 'dead', 'dm', 1)",
                    (GUILD_A, other, "2" * 32, a),
                )

    async def test_row_level_security_hides_and_refuses_another_servers_rows(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_states")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.guild(GUILD_B) as conn:
                await conn.execute(
                    "INSERT INTO memory_states (guild_id, campaign_id, id, entity_id, condition,"
                    " source, created_at) VALUES (%s, %s, %s, %s, 'dead', 'dm', 1)",
                    (GUILD_A, self.c, "3" * 32, a),
                )

    async def test_the_new_table_has_forced_row_level_security(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relname = 'memory_states'"
                " AND relnamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        self.assertEqual((row or {}).get("relrowsecurity"), True)
        self.assertEqual((row or {}).get("relforcerowsecurity"), True)

    # ---- backups ----------------------------------------------------------------------

    async def fill(self) -> None:
        cerric = await self.person()
        sword = await self.add("Heartseeker", type="item")
        plain = await self.add("a longbow", type="item")
        await self.memory.set_condition(GUILD_A, self.c, cerric, "alive", source="entitybot")
        self.now += 5
        await self.memory.set_condition(
            GUILD_A, self.c, cerric, "dead", source="entitybot", from_session_at=42
        )
        await self.memory.set_condition(GUILD_A, self.c, sword, "lost", source="dm")
        await self.memory.set_unique(GUILD_A, self.c, plain, True, source="dm")
        await self.memory.set_unique(GUILD_A, self.c, sword, False, source="dm")

    async def test_states_and_the_unique_flag_survive_backup_and_restore(self) -> None:
        await self.fill()
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copy = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        original.pop("memory_mentions"), copy.pop("memory_mentions")
        self.assertEqual(copy, original)
        self.assertEqual(len(copy["memory_states"]), 3)
        self.assertEqual(
            sorted(str(r["unique_item"]) for r in copy["memory_entities"]),
            ["False", "None", "True"],
        )
        states = await self.memory.states(GUILD_B, restored.id)
        self.assertEqual(sorted(s.condition for s in states.values()), ["dead", "lost"])

    async def test_a_backup_from_before_has_no_states_and_no_unique_flag(self) -> None:
        await self.person()
        data = await self.campaigns.export(GUILD_A, self.c)
        old = [
            {k: v for k, v in r.items() if k != "unique_item"} if r["table"] == "entity" else r
            for r in data["sections"]["memory"]
            if r["table"] != "state"
        ]
        data = {**data, "sections": {**data["sections"], "memory": old}}
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertEqual(await self.memory.states(GUILD_B, restored.id), {})
        entities = await self.memory.entities(GUILD_B, restored.id)
        self.assertEqual([e.unique_item for e in entities], [None])

    async def test_the_json_of_a_backup_is_plain_data(self) -> None:
        await self.fill()
        data = await self.campaigns.export(GUILD_A, self.c)
        json.dumps(data)
        tags = {r["table"] for r in data["sections"]["memory"]}
        self.assertIn("state", tags)

    async def test_damaged_states_are_refused(self) -> None:
        await self.fill()
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = backup["sections"]["memory"]
        states = [r for r in rows if r["table"] == "state"]
        current = next(r for r in states if r["closed_at"] is None and r["condition"] == "dead")
        entity_row = next(
            r for r in rows if r["table"] == "entity" and r["id"] == current["entity_id"]
        )

        def changed(tag: str, **fields: Any) -> list[Any]:
            return [{**r, **fields} if r is current else r for r in rows]

        sword = next(r for r in rows if r["table"] == "entity" and r["name"] == "Heartseeker")
        broken: list[list[Any]] = [
            changed("state", condition="intact"),  # an item's word for a character
            changed("state", condition="spaceship"),
            changed("state", entity_id="0" * 32),
            changed("state", closed_at="soon"),
            [*rows, {**current, "id": "9" * 32}],  # two current for one entry
            [{**r, "type": "faction"} if r is entity_row else r for r in rows],  # no state there
            [{**r, "unique_item": True} if r is entity_row else r for r in rows],  # not an item
            [{**r, "unique_item": "yes"} if r is sword else r for r in rows],
            [*rows, {k: v for k, v in current.items() if k != "closed_at"}],
        ]
        for i, bad in enumerate(broken):
            data = {**backup, "sections": {**backup["sections"], "memory": bad}}
            with self.subTest(case=i), self.assertRaisesRegex(CampaignError, "damaged"):
                await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_history_of_an_entry_that_changed_kind_is_still_a_good_backup(self) -> None:
        thing = await self.add("Heartseeker", type="item")
        await self.memory.set_condition(GUILD_A, self.c, thing, "intact", source="dm")
        await self.memory.set_entity_type(GUILD_A, self.c, thing, "character", source="dm")
        data = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertEqual(len(await self.memory.state_history(GUILD_B, restored.id, thing)), 1)

    async def test_deleting_a_campaign_takes_its_states(self) -> None:
        a = await self.person()
        await self.memory.set_condition(GUILD_A, self.c, a, "dead", source="dm")
        await self.campaigns.delete(GUILD_A, self.c)
        self.assertEqual(await self.count("memory_states"), 0)

    # ---- gaps the reviewers named ----------------------------------------------------

    async def test_merging_two_holders_of_one_item_flags_it_and_undo_puts_it_back(self) -> None:
        item = await self.add("Heartseeker", type="item")
        mira, snot = await self.add("Mira"), await self.add("Snot")
        await self.relate(mira, "carries", item)
        await self.relate(snot, "owns", item)  # not a holding
        extra = await self.add("Mira the Bold")
        await self.relate(extra, "carries", item)
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)

    async def test_merging_a_dead_character_in_flags_what_it_did_after_dying(self) -> None:
        keep, gone, friend = (
            await self.add("Belleros"),
            await self.add("Bell"),
            await self.add("Mira"),
        )
        await self.memory.set_condition(
            GUILD_A, self.c, keep, "dead", source="dm", from_session_at=100
        )
        await self.relate(gone, "serves", friend, from_session_at=200)
        self.assertEqual(await self.flags(), [])
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        self.assertEqual([f.kind for f in await self.flags()], [ACTS_AFTER_END])

    async def test_undo_of_a_merge_that_moved_a_condition_puts_it_back(self) -> None:
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        await self.memory.set_condition(GUILD_A, self.c, gone, "dead", source="dm")
        before = await self.snapshot()
        merged = await self.memory.merge(
            GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True
        )
        assert merged.batch is not None
        await self.memory.undo(GUILD_A, self.c, merged.batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_merging_an_item_into_a_character_leaves_its_condition_behind(self) -> None:
        keep, gone = await self.add("Belleros"), await self.add("Bell", type="item")
        await self.memory.set_condition(GUILD_A, self.c, gone, "intact", source="dm")
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})  # none current
        data = await self.campaigns.export(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_B, data, DM)  # and the backup still loads

    async def test_the_condition_left_behind_by_a_merge_is_closed(self) -> None:
        keep, gone = await self.add("Belleros"), await self.add("Bell")
        await self.memory.set_condition(GUILD_A, self.c, keep, "alive", source="dm")
        await self.memory.set_condition(GUILD_A, self.c, gone, "dead", source="dm")
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        states = await self.memory.states(GUILD_A, self.c)
        self.assertEqual({k: v.condition for k, v in states.items()}, {keep: "alive"})

    async def test_an_undated_fact_is_not_set_against_a_session_date(self) -> None:
        cerric, mira = await self.person(), await self.add("Mira")
        await self.memory.set_condition(
            GUILD_A, self.c, cerric, "dead", source="dm", from_session_at=self.now - 1000
        )
        self.now += 10
        later = await self.relate(cerric, "serves", mira)  # no date: stored after it was stored
        self.assertEqual([f.kind for f in later.value[1]], [ACTS_AFTER_END])
        before = await self.memory.state_history(GUILD_A, self.c, cerric)
        self.assertEqual(len(before), 1)

    async def test_switching_off_closes_the_alerts_about_the_holders(self) -> None:
        item = await self.add("Heartseeker", type="item")
        await self.two_carry(item)
        self.assertEqual(len(await self.flags(HELD_TWICE)), 1)
        await self.memory.set_unique(GUILD_A, self.c, item, False, source="dm")
        self.assertEqual(await self.flags(), [])

    async def test_undo_of_the_one_of_a_kind_switch(self) -> None:
        item = await self.add("a longbow", type="item")
        before = await self.snapshot()
        done = await self.memory.set_unique(GUILD_A, self.c, item, True, source="dm")
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_three_holders_flag_each_pair_once_and_a_rejected_one_does_not_count(
        self,
    ) -> None:
        item = await self.add("a longbow", type="item")
        people = [await self.add(n) for n in ("Mira", "Snot", "Kesh", "Ulf")]
        rels = [(await self.relate(p, "carries", item)).value[0] for p in people]
        await self.memory.update_relation(
            GUILD_A, self.c, rels[3].id, source="dm", status="rejected"
        )
        written = await self.memory.set_unique(GUILD_A, self.c, item, True, source="dm")
        self.assertEqual(len(written.value[1]), 3)  # three holders, three pairs

    async def test_places_in_another_campaign_do_not_make_a_loop_here(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        a1 = await self.add("Inn", type="place")
        t1 = await self.add("Town", type="place")
        await self.relate(a1, "located_in", t1)
        a2 = (
            await self.memory.add_entity(GUILD_A, other, type="place", name="Town", source="dm")
        ).value.id
        b2 = (
            await self.memory.add_entity(GUILD_A, other, type="place", name="Inn", source="dm")
        ).value.id
        written = await self.memory.add_relation(
            GUILD_A, other, a2, "located_in", b2, source="dm", confidence=1.0
        )
        self.assertEqual(written.value[1], [])

    async def test_a_place_fact_that_ended_does_not_count_as_nesting(self) -> None:
        keep, inn = await self.add("Keep", type="place"), await self.add("Inn", type="place")
        mira = await self.add("Mira")
        old = await self.relate(inn, "located_in", keep)
        await self.memory.update_relation(
            GUILD_A, self.c, old.value[0].id, source="dm", to_session_at=5
        )
        await self.relate(mira, "located_in", keep)
        again = await self.relate(mira, "located_in", inn)
        self.assertEqual([f.kind for f in again.value[1]], [TOO_MANY])

    async def test_a_kind_change_away_from_item_forgets_one_of_a_kind(self) -> None:
        item = await self.add("Heartseeker", type="item")
        await self.memory.set_unique(GUILD_A, self.c, item, False, source="dm")
        await self.memory.set_entity_type(GUILD_A, self.c, item, "concept", source="dm")
        entity = await self.memory.entity(GUILD_A, self.c, item)
        assert entity is not None
        self.assertIsNone(entity.unique_item)

    # ---- send-back: every write that changes a kind (#1272) -----------------------------

    async def test_confirming_a_proposed_item_as_a_character_keeps_the_backup_restorable(
        self,
    ) -> None:
        thing = await self.add("Bell", type="item", status=PROPOSED, source="scan")
        await self.memory.set_condition(GUILD_A, self.c, thing, "intact", source="scan")
        await self.memory.set_unique(GUILD_A, self.c, thing, True, source="dm")
        await self.memory.confirm_entity(GUILD_A, self.c, thing, "npc", source="dm")
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})
        entity = await self.memory.entity(GUILD_A, self.c, thing)
        assert entity is not None
        self.assertIsNone(entity.unique_item)
        data = await self.campaigns.export(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_B, data, DM)  # not refused as damaged

    async def test_confirming_a_group_of_names_keeps_the_backup_restorable(self) -> None:
        things = [
            await self.add(n, type="item", status=PROPOSED, source="scan") for n in ("Bell", "Ash")
        ]
        for t in things:
            await self.memory.set_condition(GUILD_A, self.c, t, "lost", source="scan")
        await self.memory.confirm_kinds(GUILD_A, self.c, things, "npc", source="dm")
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})
        data = await self.campaigns.export(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_a_merge_that_turns_an_item_into_a_players_character_settles_it(self) -> None:
        keep = await self.add("Bell", type="item", status=PROPOSED, source="scan")
        await self.memory.set_condition(GUILD_A, self.c, keep, "intact", source="scan")
        await self.memory.set_unique(GUILD_A, self.c, keep, True, source="dm")
        gone = (
            await self.memory.confirm_entity(
                GUILD_A,
                self.c,
                (await self.add("Belleros", type="npc", status=PROPOSED, source="scan")),
                "player_character",
                source="dm",
                played_by=555,
            )
        ).value.id
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        entity = await self.memory.entity(GUILD_A, self.c, keep)
        assert entity is not None
        self.assertEqual((entity.type, entity.unique_item), ("character", None))
        self.assertEqual(await self.memory.states(GUILD_A, self.c), {})
        data = await self.campaigns.export(GUILD_A, self.c)
        await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_only_the_dm_can_merge_two_different_conditions(self) -> None:
        keep = await self.add("Belleros", status=PROPOSED, source="scan")
        gone = await self.add("Bell", status=PROPOSED, source="scan")
        await self.memory.set_condition(GUILD_A, self.c, keep, "alive", source="scan")
        await self.memory.set_condition(GUILD_A, self.c, gone, "dead", source="scan")
        before = await self.snapshot()
        with self.assertRaisesRegex(MemoryRuleError, "Only the DM can say these two"):
            await self.memory.merge(GUILD_A, self.c, keep, gone, source="entitybot")
        self.assertEqual(await self.snapshot(), before)  # nothing half-done
        # The same condition on both is no conflict; the DM may settle different ones.
        same_keep = await self.add("Tam", status=PROPOSED, source="scan")
        same_gone = await self.add("Tammy", status=PROPOSED, source="scan")
        for entry in (same_keep, same_gone):
            await self.memory.set_condition(GUILD_A, self.c, entry, "dead", source="scan")
        await self.memory.merge(GUILD_A, self.c, same_keep, same_gone, source="entitybot")
        other_keep = await self.add("Aldwin", status=PROPOSED, source="scan")
        other_gone = await self.add("Ald", status=PROPOSED, source="scan")
        await self.memory.set_condition(GUILD_A, self.c, other_keep, "alive", source="scan")
        await self.memory.set_condition(GUILD_A, self.c, other_gone, "dead", source="scan")
        await self.memory.merge(
            GUILD_A, self.c, other_keep, other_gone, source="dm", dm_said_same=True
        )
        states = await self.memory.states(GUILD_A, self.c, [other_keep])
        self.assertEqual(states[other_keep].condition, "alive")


class Migration0070(MemoryTest):
    """0070 adds a table, a column and widens a check; it changes no row, so it needs no
    row-level-security opening, and this proves rows already there survive it."""

    async def test_rows_already_there_survive_and_the_new_pieces_work(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0070"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        c = (await campaigns.create(GUILD_A, "Frozen Wastes", DM)).id
        a, b, rel, flag = ("1" * 32, "2" * 32, "3" * 32, "4" * 32)
        async with self.db.guild(GUILD_A) as conn:
            for eid, name, kind in ((a, "Heartseeker", "item"), (b, "Mira", "character")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, role, needs_look)"
                    " VALUES (%s, %s, %s, %s, %s, '', 'confirmed', 'dm', 1, NULL, false)",
                    (GUILD_A, c, eid, kind, name),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at,"
                " definition_version) VALUES (%s, %s, %s, %s, 'owns', %s, '', 1, 'confirmed',"
                " 'dm', ARRAY[]::text[], false, 1, 1)",
                (GUILD_A, c, rel, b, a),
            )
            await conn.execute(
                "INSERT INTO memory_flags (guild_id, campaign_id, id, kind, relation_id, status,"
                " created_at) VALUES (%s, %s, %s, 'too_many', %s, 'open', 1)",
                (GUILD_A, c, flag, rel),
            )
        await self.db.migrate()
        from dmbot.memory.store import MemoryStore

        memory = MemoryStore(self.db, clock=lambda: 1)
        entities = {e.name: e for e in await memory.entities(GUILD_A, c)}
        self.assertEqual(entities["Heartseeker"].unique_item, None)  # the name decides
        self.assertTrue(entities["Heartseeker"].is_unique)
        self.assertEqual([f.id for f in await memory.flags(GUILD_A, c)], [flag])
        self.assertEqual(len(await memory.relations(GUILD_A, c)), 1)
        # The new kinds of flag and the new table are there, and still isolated.
        written = await memory.set_condition(GUILD_A, c, b, "dead", source="dm")
        assert written.value[0] is not None
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_flags SET kind = 'held_twice' WHERE id = %s", (flag,))
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
