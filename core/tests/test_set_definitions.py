"""Relationship definitions that travel with a shared rule set (#1456): which of a campaign's own
relationships may be offered, what a follower gets (read-only, marked as the set's), that the
follower's own and the official ones are never overwritten, that Unfollow removes the unused and
deprecates the used, that Cherry pick's ticked ones stay as the campaign's own, one undo for one
sync, and that a follower's memory backup holds its own and not the set's."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import set_definitions as sd
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.ontology import PredicateTerm
from tests.test_memory_store import GUILD_A, MemoryTest

SET = "a" * 32
OTHER_SET = "b" * 32


def term(key: str = "vowed_to", label: str = "vows loyalty to", **kw: Any) -> PredicateTerm:
    base: dict[str, Any] = dict(
        key=key, label=label, description="Has made a binding oath to.",
        subject_types=("character",), object_types=("character",), core=False,
        examples=("Mira vows loyalty to Cerric.",), inverse_label="is vowed to by",
        covers=("oaths",), synonyms=(),
    )  # fmt: skip
    return PredicateTerm(**{**base, **kw})


class Shaping(unittest.TestCase):
    def test_only_the_campaigns_own_active_relationships_on_official_kinds_may_travel(self) -> None:
        self.assertTrue(sd.shareable(term()))
        self.assertFalse(sd.shareable(term(core=True)))
        self.assertFalse(sd.shareable(term(status="deprecated")))
        self.assertFalse(sd.shareable(term(key="plays")))  # an official key
        self.assertFalse(sd.shareable(term(subject_types=("ship",))))  # a kind only one table has

    def test_a_definition_carries_its_words_and_nothing_else(self) -> None:
        d = sd.definition_of(term(parent="knows", conflicts_with=("enemy_of",)))
        self.assertEqual(
            set(d),
            {
                "key", "label", "inverse_label", "description", "subject_types", "object_types",
                "symmetric", "version",
            },
        )  # fmt: skip
        self.assertNotIn("parent", d)
        self.assertNotIn("conflicts_with", d)

    def test_untrusted_definitions_are_cleaned_and_bad_ones_dropped(self) -> None:
        good = sd.definition_of(term())
        bad = [
            "nope",
            {**good, "key": "x" * 301},
            {**good, "subject_types": ["ship"]},  # a custom kind can't cross
            {**good, "subject_types": "not a list"},
            {**good, "version": 0},
            {k: v for k, v in good.items() if k != "label"},
        ]
        got = sd.valid_definitions([*bad, good, good, {**good, "secret": "x", "note": "y"}])
        self.assertEqual(len(got), 1)  # the duplicate key is dropped too
        self.assertEqual(set(got[0]), set(good))
        self.assertEqual(sd.valid_definitions("not a list"), [])
        many = [{**good, "key": f"rel_{i:02d}"} for i in range(40)]
        self.assertEqual(len(sd.valid_definitions(many)), sd.MAX_DEFINITIONS)

    def test_the_tick_line_shows_the_label_and_the_description(self) -> None:
        line = sd.tick_line(sd.definition_of(term()))
        self.assertTrue(
            line.startswith("vows loyalty to (back: is vowed to by): Has made a binding oath")
        )
        self.assertLessEqual(len(line), 140)


class Travelling(MemoryTest):
    async def own(self, key: str = "vowed_to", label: str = "vows loyalty to") -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, term(key, label), examples=["Mira vows loyalty to Cerric."],
            reason="the table uses it", source="dm",
        )  # fmt: skip

    async def follower(self) -> str:
        return (await self.campaigns.create(GUILD_A, "Follower", 77)).id

    async def borrowed(self, campaign: str) -> dict[str, Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT key, from_set, status FROM memory_predicates WHERE guild_id = %s"
                " AND campaign_id = %s AND from_set IS NOT NULL",
                (GUILD_A, campaign),
            )
            return {r["key"]: dict(r) for r in await cur.fetchall()}

    async def test_the_publisher_is_offered_its_own_shareable_relationships_only(self) -> None:
        await self.own()
        await self.memory.add_predicate(
            GUILD_A, self.c,
            term("serves_on", "serves on", subject_types=("character",), object_types=("faction",)),
            examples=["x"], reason="y", source="dm",
        )  # fmt: skip
        offered = await sd.own_definitions(self.memory, GUILD_A, self.c)
        self.assertEqual([d["key"] for d in offered], ["serves_on", "vowed_to"])
        # The official ones are not offered, and neither is a borrowed one.
        other = await self.follower()
        await sd.sync(
            self.memory, GUILD_A, other, SET, "Frostmaiden", [sd.definition_of(term())], source="dm"
        )
        self.assertEqual(await sd.own_definitions(self.memory, GUILD_A, other), [])

    async def test_follow_adds_them_read_only_and_marked_as_the_sets_in_one_batch(self) -> None:
        other = await self.follower()
        done = await sd.sync(
            self.memory, GUILD_A, other, SET, "Frostmaiden",
            [sd.definition_of(term())], source="dm",
        )  # fmt: skip
        self.assertEqual(done.added, ("vows loyalty to",))
        self.assertEqual((await self.borrowed(other))["vowed_to"]["from_set"], SET)
        # It works like any relationship of the campaign: a fact can use it.
        a, b = (
            await self.add("Mira", campaign=other, type="character"),
            await self.add("Cerric", campaign=other, type="character"),
        )
        await self.memory.add_relation(
            GUILD_A, other, a, "vowed_to", b, source="dm", confidence=1.0
        )
        # Read-only: the campaign cannot retire it.
        with self.assertRaises(MemoryRuleError) as caught:
            await self.memory.deprecate_term(GUILD_A, other, "vowed_to", source="dm")
        self.assertIn("comes from a shared set", str(caught.exception))
        # Not the publisher's: nothing appeared there.
        self.assertEqual(await self.borrowed(self.c), {})

    async def test_one_undo_takes_the_whole_sync_back(self) -> None:
        other = await self.follower()
        defs = [sd.definition_of(term()), sd.definition_of(term("serves_on", "serves on"))]
        done = await sd.sync(self.memory, GUILD_A, other, SET, "Frostmaiden", defs, source="dm")
        self.assertEqual(len(await self.borrowed(other)), 2)
        assert done.batch is not None
        await self.memory.undo(GUILD_A, other, done.batch)
        self.assertEqual(await self.borrowed(other), {})

    async def test_the_followers_own_and_the_official_ones_are_never_overwritten(self) -> None:
        other = await self.follower()
        await self.memory.add_predicate(
            GUILD_A, other, term("vowed_to", "swears loyalty to", description="Mine, not theirs."),
            examples=["x"], reason="y", source="dm",
        )  # fmt: skip
        official = sd.definition_of(term("plays", "plays"))
        done = await sd.sync(
            self.memory, GUILD_A, other, SET, "Frostmaiden",
            [sd.definition_of(term()), official], source="dm",
        )  # fmt: skip
        self.assertEqual(sorted(done.kept_own), ["plays", "vows loyalty to"])
        self.assertEqual(done.added, ())
        self.assertEqual(await self.borrowed(other), {})
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT label, description FROM memory_predicates WHERE campaign_id = %s"
                " AND key = 'vowed_to'",
                (other,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            (row["label"], row["description"]), ("swears loyalty to", "Mine, not theirs.")
        )

    async def test_a_definition_the_memorys_rules_refuse_is_left_out_with_the_rest_added(
        self,
    ) -> None:
        other = await self.follower()
        hit = sd.definition_of(
            term("grappled", "grappled")
        )  # a word from the game's rules, not a relationship
        done = await sd.sync(
            self.memory, GUILD_A, other, SET, "Frostmaiden",
            [hit, sd.definition_of(term())], source="dm",
        )  # fmt: skip
        self.assertEqual(done.refused, ("grappled",))
        self.assertEqual(done.added, ("vows loyalty to",))

    async def test_a_new_version_updates_in_place_and_a_dropped_one_goes(self) -> None:
        other = await self.follower()
        v1 = sd.definition_of(term())
        await sd.sync(
            self.memory,
            GUILD_A,
            other,
            SET,
            "S",
            [v1, sd.definition_of(term("serves_on", "serves on"))],
            source="dm",
        )
        v2 = {**v1, "description": "Swore an oath of service to.", "version": 2}
        done = await sd.sync(self.memory, GUILD_A, other, SET, "S", [v2], source="dm")
        self.assertEqual((done.updated, done.removed), (("vows loyalty to",), ("serves on",)))
        self.assertEqual(list(await self.borrowed(other)), ["vowed_to"])

    async def test_unfollow_removes_the_unused_and_deprecates_the_used(self) -> None:
        other = await self.follower()
        defs = [sd.definition_of(term()), sd.definition_of(term("serves_on", "serves on"))]
        await sd.sync(self.memory, GUILD_A, other, SET, "S", defs, source="dm")
        a, b = (
            await self.add("Mira", campaign=other, type="character"),
            await self.add("Cerric", campaign=other, type="character"),
        )
        await self.memory.add_relation(
            GUILD_A, other, a, "vowed_to", b, source="dm", confidence=1.0
        )
        done = await sd.release(self.memory, GUILD_A, other, SET, source="dm")
        self.assertEqual((done.removed, done.deprecated), (("serves on",), ("vows loyalty to",)))
        # The retired one keeps its mark as the set's (still read-only; Follow brings it back).
        self.assertEqual(
            {k: v["status"] for k, v in (await self.borrowed(other)).items()},
            {"vowed_to": "deprecated"},
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT key, status FROM memory_predicates WHERE campaign_id = %s"
                " AND key IN ('vowed_to', 'serves_on')",
                (other,),
            )
            rows = {r["key"]: r["status"] for r in await cur.fetchall()}
        self.assertEqual(rows, {"vowed_to": "deprecated"})  # old facts still read
        facts = await self.memory.relations(GUILD_A, other)
        self.assertEqual([f.predicate for f in facts], ["vowed_to"])

    async def test_cherry_pick_keeps_the_ticked_ones_as_the_campaigns_own(self) -> None:
        other = await self.follower()
        defs = [sd.definition_of(term()), sd.definition_of(term("serves_on", "serves on"))]
        await sd.sync(self.memory, GUILD_A, other, SET, "S", defs, source="dm")
        done = await sd.release(self.memory, GUILD_A, other, SET, keep={"vowed_to"}, source="dm")
        self.assertEqual(done.removed, ("serves on",))
        self.assertEqual(await self.borrowed(other), {})
        # vowed_to is now ordinary: the campaign may retire it.
        await self.memory.deprecate_term(GUILD_A, other, "vowed_to", source="dm")

    async def test_one_set_cannot_release_anothers_definitions(self) -> None:
        other = await self.follower()
        await sd.sync(
            self.memory, GUILD_A, other, SET, "S", [sd.definition_of(term())], source="dm"
        )
        await sd.release(self.memory, GUILD_A, other, OTHER_SET, source="dm")
        self.assertEqual(list(await self.borrowed(other)), ["vowed_to"])

    async def test_a_backup_with_a_fact_on_a_borrowed_relationship_checks_and_loads(self) -> None:
        from dmbot.memory.backup import MemorySection

        other = await self.follower()
        await sd.sync(
            self.memory, GUILD_A, other, SET, "S", [sd.definition_of(term())], source="dm"
        )
        a = await self.add("Mira", campaign=other, type="character")
        b = await self.add("Cerric", campaign=other, type="character")
        await self.memory.add_relation(
            GUILD_A, other, a, "vowed_to", b, source="dm", confidence=1.0
        )
        async with self.db.guild(GUILD_A) as conn:
            rows = await MemorySection().dump(conn, GUILD_A, other)
        predicates = {r["key"]: r for r in rows if r.get("table") == "predicate"}
        self.assertEqual(predicates["vowed_to"]["from_set"], SET)  # still marked as the set's
        checked = MemorySection().check(rows)  # a fact on it is no reason to refuse the file
        fresh = (await self.campaigns.create(GUILD_A, "Restored", 77)).id
        async with self.db.guild(GUILD_A) as conn:
            await MemorySection().load(conn, GUILD_A, fresh, checked)
        self.assertEqual(list(await self.borrowed(fresh)), ["vowed_to"])

    async def test_an_old_backup_without_the_mark_loads_and_a_bad_mark_is_refused(self) -> None:
        from dmbot.memory.backup import MemorySection

        other = await self.follower()
        await self.memory.add_predicate(
            GUILD_A, other, term("mentor_of", "mentors"), examples=["x"], reason="y", source="dm"
        )
        async with self.db.guild(GUILD_A) as conn:
            rows = await MemorySection().dump(conn, GUILD_A, other)
        old = [{k: v for k, v in r.items() if k != "from_set"} for r in rows]
        MemorySection().check(old)  # from before sets could lend relationships
        bad = [{**r, "from_set": "not an id"} if r.get("table") == "predicate" else r for r in rows]
        with self.assertRaises(CampaignError):
            MemorySection().check(bad)

    async def test_undoing_a_cherry_pick_puts_the_marks_back(self) -> None:
        other = await self.follower()
        defs = [sd.definition_of(term()), sd.definition_of(term("serves_on", "serves on"))]
        await sd.sync(self.memory, GUILD_A, other, SET, "S", defs, source="dm")
        a = await self.add("Mira", campaign=other, type="character")
        b = await self.add("Cerric", campaign=other, type="character")
        await self.memory.add_relation(
            GUILD_A, other, a, "vowed_to", b, source="dm", confidence=1.0
        )
        kept = await sd.sync(
            self.memory, GUILD_A, other, SET, "S", [defs[0]], source="dm", then_own=True
        )
        self.assertEqual(
            await self.borrowed(other), {}
        )  # the kept one is its own; the other is gone
        assert kept.batch is not None
        await self.memory.undo(GUILD_A, other, kept.batch)
        back = await self.borrowed(other)
        self.assertEqual(sorted(back), ["serves_on", "vowed_to"])  # both the set's again
        self.assertEqual({v["from_set"] for v in back.values()}, {SET})

    async def test_a_used_one_that_was_retired_comes_back_on_a_second_follow(self) -> None:
        other = await self.follower()
        defs = [sd.definition_of(term())]
        await sd.sync(self.memory, GUILD_A, other, SET, "S", defs, source="dm")
        a = await self.add("Mira", campaign=other, type="character")
        b = await self.add("Cerric", campaign=other, type="character")
        await self.memory.add_relation(
            GUILD_A, other, a, "vowed_to", b, source="dm", confidence=1.0
        )
        await sd.release(self.memory, GUILD_A, other, SET, source="dm")
        again = await sd.sync(self.memory, GUILD_A, other, SET, "S", defs, source="dm")
        self.assertEqual(again.updated, ("vows loyalty to",))  # reactivated, not "you already have"
        self.assertEqual(again.kept_own, ())
        self.assertEqual((await self.borrowed(other))["vowed_to"]["status"], "active")

    async def test_a_changed_meaning_is_checked_like_a_new_one(self) -> None:
        other = await self.follower()
        v1 = sd.definition_of(term())
        await sd.sync(self.memory, GUILD_A, other, SET, "S", [v1], source="dm")
        bad = {**v1, "label": "grappled", "version": 2}  # a word from the game's rules
        done = await sd.sync(self.memory, GUILD_A, other, SET, "S", [bad], source="dm")
        self.assertEqual((done.updated, done.refused), ((), ("grappled",)))
        same_version = {**v1, "description": "Swore an oath of service.", "version": 1}
        again = await sd.sync(self.memory, GUILD_A, other, SET, "S", [same_version], source="dm")
        self.assertEqual(again.updated, ("vows loyalty to",))  # a wording change is applied too

    async def test_a_term_pointing_at_a_borrowed_one_does_not_stop_the_unfollow(self) -> None:
        other = await self.follower()
        await sd.sync(
            self.memory, GUILD_A, other, SET, "S", [sd.definition_of(term())], source="dm"
        )
        async with self.db.guild(GUILD_A) as conn:  # the campaign's own term names it as parent
            await conn.execute(
                "INSERT INTO memory_predicates (guild_id, campaign_id, key, parent, label,"
                " description, examples, reason, subject_types, object_types, is_symmetric,"
                " conflicts_with, status, created_at) VALUES (%s, %s, 'sworn_sibling', 'vowed_to',"
                " 'swears beside', 'x', ARRAY['x'], 'y', ARRAY['character'], ARRAY['character'],"
                " false, ARRAY[]::text[], 'active', 1)",
                (GUILD_A, other),
            )
        done = await sd.release(self.memory, GUILD_A, other, SET, source="dm")
        self.assertEqual((done.removed, done.deprecated), ((), ("vows loyalty to",)))

    async def test_nothing_of_the_publishers_memory_reaches_any_table_of_the_follower(self) -> None:
        from dmbot.memory._changes import ALL

        await self.own()
        a = await self.add("Mira", type="character")
        b = await self.add("Cerric", type="character")
        await self.memory.add_relation(
            GUILD_A, self.c, a, "vowed_to", b, source="dm", confidence=1.0
        )
        other = await self.follower()
        await sd.sync(
            self.memory, GUILD_A, other, SET, "S", [sd.definition_of(term())], source="dm"
        )
        async with self.db.guild(GUILD_A) as conn:
            for table in ALL:
                # (every campaign has The truth and The party from the start: no story in them)
                if table.name in ("memory_predicates", "memory_changes", "memory_perspectives"):
                    continue
                cur = await conn.execute(
                    f"SELECT count(*) AS n FROM {table.name} WHERE campaign_id = %s", (other,)
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["n"], 0, table.name)


if __name__ == "__main__":
    unittest.main()
