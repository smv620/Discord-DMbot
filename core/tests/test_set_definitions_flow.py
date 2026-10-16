"""A shared rule set carrying relationship definitions, end to end (#1456): publish with ticked
definitions, Follow bringing them read-only, an update changing one, Unfollow, Cherry pick taking
them one by one, the follower's same-name definition winning, the pure diff and apply, and a
follower's backup. The memory side alone is in test_set_definitions.py."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import set_definitions as sd
from dmbot.memory.backup import MemorySection
from dmbot.memory.store import MemoryStore
from dmbot.rules import rule_sets, set_diff, set_sync
from dmbot.rules.house import HouseRuleStore
from dmbot.rules.rule_sets import FollowedSetsSection, RuleSetStore
from dmbot.ui import shared_rules_defs as ui
from tests.pg import DatabaseTest
from tests.test_set_definitions import term

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B = 11, 22
NOW = 1_700_000_000


def rules_only(*texts: str) -> dict[str, Any]:
    return {
        "rules": [{"number": i + 1, "rule": t, "instead": None} for i, t in enumerate(texts)],
        "link": None,
    }


class Pure(unittest.TestCase):
    def defs(self, *keys: str, version: int = 1) -> list[dict[str, Any]]:
        return [{**sd.definition_of(term(k, f"label {k}")), "version": version} for k in keys]

    def test_content_carries_the_definitions_and_old_content_has_none(self) -> None:
        got = rule_sets.content_of([], None, self.defs("vowed_to"))
        self.assertEqual([d["key"] for d in got["definitions"]], ["vowed_to"])
        self.assertEqual(rule_sets.valid_content(rules_only("A"))["definitions"], [])
        junk = {**rules_only("A"), "definitions": [{"key": "x"}, "nope"]}
        self.assertEqual(rule_sets.valid_content(junk)["definitions"], [])
        self.assertEqual(
            rule_sets.valid_content(rule_sets.content_of([], None, self.defs("vowed_to")))[
                "definitions"
            ],
            self.defs("vowed_to"),
        )

    def test_content_from_outside_cannot_carry_more_than_a_definition_may(self) -> None:
        good = self.defs("vowed_to")[0]
        hostile = {
            **rules_only("A"),
            "definitions": [
                {**good, "key": "Bad Key!"},  # (kept out when the memory checks it as new)
                {**good, "label": "x" * 400},
                {**good, "subject_types": ["character"] * 30},
                {**good, "subject_types": ["character"] * 30},
                {**good, "extra": "ignored", "secret_note": "never"},
            ],
        }
        got = rule_sets.valid_content(hostile)["definitions"]
        self.assertTrue(all(set(d) == set(good) for d in got))
        self.assertTrue(all(len(d["label"]) <= 300 for d in got))
        self.assertLessEqual(len(got), 2)

    def test_the_diff_finds_added_changed_and_removed_by_key(self) -> None:
        old = {**rules_only("A"), "definitions": self.defs("a_one", "b_two")}
        new = {
            **rules_only("A"),
            "definitions": [*self.defs("b_two", version=2), *self.defs("c_three")],
        }
        got = {(c.kind, c.key) for c in set_diff.diff_definitions(old, new)}
        self.assertEqual(got, {("removed", "a_one"), ("changed", "b_two"), ("added", "c_three")})
        self.assertEqual(set_diff.diff_definitions(old, old), [])

    def test_apply_takes_the_ticked_definitions_one_by_one(self) -> None:
        old = {**rules_only("A"), "definitions": self.defs("a_one", "b_two")}
        new = {
            **rules_only("A", "B"),
            "definitions": [*self.defs("b_two", version=2), *self.defs("c_three")],
        }
        rule_changes = set_diff.diff(old, new)
        defs = set_diff.diff_definitions(old, new)
        only_c = set_diff.apply(old, rule_changes, {2}, new, defs, {"c_three"})
        self.assertEqual([d["key"] for d in only_c["definitions"]], ["a_one", "b_two", "c_three"])
        self.assertEqual(only_c["definitions"][1]["version"], 1)  # b_two's change was not taken
        everything = set_diff.apply(old, rule_changes, None, new, defs, None)
        self.assertEqual([d["key"] for d in everything["definitions"]], ["b_two", "c_three"])
        none = set_diff.apply(old, rule_changes, set(), None, defs, set())
        self.assertEqual(none["definitions"], old["definitions"])

    def test_the_words_count_relationships_with_the_rules(self) -> None:
        change = set_diff.DefChange("added", "x_y", None, self.defs("x_y")[0])
        self.assertEqual(set_diff.count_words([], [change]), "1 relationship")
        rule = set_diff.Change("added", 1, None, {"number": 1, "rule": "A", "instead": None})
        self.assertEqual(
            set_diff.count_words([rule], [change, change]), "1 rule and 2 relationships"
        )
        self.assertEqual(set_diff.describe_definition(change), "New relationship: label x_y")
        gone = set_diff.DefChange("removed", "x_y", change.after, None)
        self.assertEqual(set_diff.describe_definition(gone), "Removed from the set: label x_y")
        edit = set_diff.DefChange("changed", "x_y", change.after, change.after)
        self.assertIn("(meaning changed)", set_diff.describe_definition(edit))

    def test_the_followers_line_and_notes(self) -> None:
        content = {**rules_only("A"), "definitions": self.defs("x_one", "y_two")}
        line = ui.offer_line(content)
        self.assertIn("brings 2 relationships", line)
        self.assertIn("**label x\\_one**", line)  # (markdown-safe)
        self.assertEqual(ui.offer_line(rules_only("A")), "")
        note = ui.followed_note(sd.Synced(added=("A",), kept_own=("B",), refused=("C",)))
        self.assertIn("Added 1 relationship", note)
        self.assertIn("You already have **B**, so DMbot kept yours.", note)
        self.assertIn("couldn't add **C**. The rest were added.", note)
        gone = ui.stopped_note(sd.Synced(removed=("A",), deprecated=("B",)))
        self.assertIn("went too", gone)
        self.assertIn("stays for the old facts that use it", gone)
        self.assertEqual(ui.followed_note(None) + ui.stopped_note(None), "")

    def test_the_tick_list_is_short_enough_for_discord(self) -> None:
        many = [{**sd.definition_of(term(f"rel_{i:02d}", f"label {i}")), "description": "d" * 250}
                for i in range(25)]  # fmt: skip
        self.assertLessEqual(len(ui.tick_list_text(many)), 2000)
        self.assertEqual(len(ui.tick_options(many, {d["key"] for d in many})), 25)


class Flow(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: NOW)
        self.campaigns.register_section(MemorySection())
        self.campaigns.register_section(FollowedSetsSection())
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: NOW)
        self.memory = MemoryStore(self.db, clock=lambda: NOW)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(GUILD_A, self.pub, DM_A, "No flanking")
        await self.memory.add_predicate(
            GUILD_A, self.pub, term(), examples=["Mira vows loyalty to Cerric."],
            reason="the table uses it", source="dm",
        )  # fmt: skip
        await self.memory.add_predicate(
            GUILD_A, self.pub, term("guards", "guards"), examples=["x"], reason="y", source="dm"
        )
        self.rules = await self.houses.list(GUILD_A, self.pub)

    async def publish(self, keys: list[str]) -> str:
        offered = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish(
            GUILD_A, self.pub, DM_A, "Frostmaiden", self.rules, None, [offered[k] for k in keys]
        )
        return await self.sets.make_code(GUILD_A, self.pub, DM_A)

    async def follow(self, code: str) -> rule_sets.Follow:
        offer = await self.sets.by_code(GUILD_B, DM_B, code)
        done = await self.sets.follow(GUILD_B, self.far, DM_B, offer)
        await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        return done

    async def borrowed(self) -> dict[str, str]:
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT key, status FROM memory_predicates WHERE guild_id = %s AND campaign_id = %s"
                " AND from_set IS NOT NULL",
                (GUILD_B, self.far),
            )
            return {r["key"]: r["status"] for r in await cur.fetchall()}

    async def test_only_the_ticked_definitions_are_in_the_set_and_nothing_else_of_memory(
        self,
    ) -> None:
        code = await self.publish(["vowed_to"])
        offer = await self.sets.by_code(GUILD_B, DM_B, code)
        self.assertEqual([d["key"] for d in offer.content["definitions"]], ["vowed_to"])
        self.assertEqual(set(offer.content), {"rules", "link", "definitions"})
        self.assertEqual(
            set(offer.content["definitions"][0]), set(sd.definition_of(term()))
        )  # words only

    async def test_the_followers_copy_has_a_plain_example_and_none_of_the_publishers_names(
        self,
    ) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.pub, term("watches", "watches", examples=("Mira watches Tobin.",)),
            examples=["Mira watches Tobin."], reason="y", source="dm",
        )  # fmt: skip
        await self.follow(await self.publish(["watches"]))
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT examples, covers, not_this, synonyms FROM memory_predicates"
                " WHERE campaign_id = %s AND key = 'watches'",
                (self.far,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["examples"], ["X watches Y."])
        self.assertNotIn("Tobin", repr(dict(row)))
        self.assertEqual((row["covers"], row["not_this"], row["synonyms"]), ([], [], []))

    async def test_follow_brings_them_read_only_and_unfollow_takes_them_away(self) -> None:
        code = await self.publish(["vowed_to", "guards"])
        await self.follow(code)
        self.assertEqual(set(await self.borrowed()), {"vowed_to", "guards"})
        # The publisher's own are untouched, and not marked as anyone's.
        offered = await sd.own_definitions(self.memory, GUILD_A, self.pub)
        self.assertEqual(sorted(d["key"] for d in offered), ["guards", "vowed_to"])
        await self.sets.unfollow(GUILD_B, self.far, DM_B)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(sorted(done.removed), ["guards", "vows loyalty to"])
        self.assertEqual(await self.borrowed(), {})

    async def test_undo_of_an_unfollow_brings_them_back(self) -> None:
        await self.follow(await self.publish(["vowed_to"]))
        gone = await self.sets.unfollow(GUILD_B, self.far, DM_B)
        await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(await self.borrowed(), {})
        await self.sets.restore(GUILD_B, self.far, DM_B, gone)
        await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(set(await self.borrowed()), {"vowed_to"})

    async def test_the_followers_own_same_name_definition_wins_and_is_said(self) -> None:
        await self.memory.add_predicate(
            GUILD_B, self.far, term(label="swears fealty to", description="Mine."),
            examples=["x"], reason="y", source="dm",
        )  # fmt: skip
        offer_code = await self.publish(["vowed_to"])
        offer = await self.sets.by_code(GUILD_B, DM_B, offer_code)
        await self.sets.follow(GUILD_B, self.far, DM_B, offer)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(done.kept_own, ("vows loyalty to",))
        self.assertEqual(await self.borrowed(), {})
        self.assertIn("You already have", ui.followed_note(done))

    async def test_a_new_version_changes_a_definition_in_place_and_drops_a_removed_one(
        self,
    ) -> None:
        await self.follow(await self.publish(["vowed_to", "guards"]))
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        changed = {**mine["vowed_to"], "description": "Swore a binding oath.", "version": 2}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, self.rules, None, plan_ok=True, definitions=[changed]
        )
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        self.assertEqual(
            {(c.kind, c.key) for c in update.def_changes},
            {("changed", "vowed_to"), ("removed", "guards")},
        )
        await self.sets.take_update(GUILD_B, self.far, DM_B)  # Follow
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual((done.updated, done.removed), (("vows loyalty to",), ("guards",)))
        self.assertEqual(set(await self.borrowed()), {"vowed_to"})

    async def test_a_relationship_added_at_publish_reaches_the_follower_as_new_and_marked(
        self,
    ) -> None:
        # Published with one; the DM later ticks a second at "Publish my changes" (#1475): the new
        # version holds both, the follower is told it is new and gets it marked as the set's.
        await self.follow(await self.publish(["vowed_to"]))
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, self.rules, None, plan_ok=True,
            definitions=[mine["vowed_to"], mine["guards"]],
        )  # fmt: skip
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        self.assertEqual({(c.kind, c.key) for c in update.def_changes}, {("added", "guards")})
        await self.sets.take_update(GUILD_B, self.far, DM_B)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(done.added, ("guards",))
        self.assertEqual(set(await self.borrowed()), {"vowed_to", "guards"})

    async def test_a_relationship_left_out_of_a_new_version_goes_or_is_retired_at_the_follower(
        self,
    ) -> None:
        # Untick at "Publish my changes" (#1479): the new version holds only vowed_to. The
        # follower's unused copy of guards goes; a copy that facts use is retired, not deleted.
        await self.follow(await self.publish(["vowed_to", "guards"]))
        a, b = [
            (
                await self.memory.add_entity(
                    GUILD_B, self.far, type="character", name=n, source="dm", status="confirmed"
                )
            ).value.id
            for n in ("Mira", "Cerric")
        ]
        await self.memory.add_relation(
            GUILD_B, self.far, a, "vowed_to", b, source="dm", confidence=1.0
        )
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, self.rules, None, plan_ok=True,
            definitions=[mine["guards"]],
        )  # fmt: skip
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        self.assertEqual(
            {(c.kind, c.key) for c in update.def_changes},
            {("removed", "vowed_to")},
        )
        await self.sets.take_update(GUILD_B, self.far, DM_B)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual((done.removed, done.deprecated), ((), ("vows loyalty to",)))
        self.assertEqual(await self.borrowed(), {"vowed_to": "deprecated", "guards": "active"})
        # The publisher's own relationships are untouched.
        own = {d["key"] for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        self.assertEqual(own, {"vowed_to", "guards"})

    async def test_an_unused_relationship_left_out_is_removed_at_the_follower(self) -> None:
        await self.follow(await self.publish(["vowed_to", "guards"]))
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, self.rules, None, plan_ok=True,
            definitions=[mine["vowed_to"]],
        )  # fmt: skip
        await self.sets.take_update(GUILD_B, self.far, DM_B)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual((done.removed, done.deprecated), (("guards",), ()))
        self.assertEqual(set(await self.borrowed()), {"vowed_to"})

    async def test_cherry_pick_takes_the_relationships_one_by_one_and_keeps_them_as_its_own(
        self,
    ) -> None:
        await self.follow(await self.publish(["vowed_to"]))
        await self.memory.add_predicate(
            GUILD_A,
            self.pub,
            term("serves_on", "serves on"),
            examples=["x"],
            reason="y",
            source="dm",
        )
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, self.rules, None, plan_ok=True,
            definitions=[mine["vowed_to"], mine["serves_on"]],
        )  # fmt: skip
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        self.assertEqual([c.key for c in update.def_changes], ["serves_on"])
        # Take every rule change but leave the new relationship out: that is a Cherry pick.
        taken = await self.sets.take_update(
            GUILD_B,
            self.far,
            DM_B,
            accepted={c.number for c in update.changes},
            accepted_defs=set(),
        )
        self.assertTrue(taken.merged)
        self.assertEqual([d["key"] for d in taken.kept_definitions], ["vowed_to"])
        done = await set_sync.keep_as_own(self.memory, GUILD_B, self.far, taken)
        self.assertEqual(done.added, ())
        self.assertEqual(await self.borrowed(), {})  # none is the set's any more
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT key FROM memory_predicates WHERE guild_id = %s AND campaign_id = %s"
                " AND key IN ('vowed_to', 'serves_on')",
                (GUILD_B, self.far),
            )
            have = {r["key"] for r in await cur.fetchall()}
        self.assertEqual(have, {"vowed_to"})  # kept as its own; the one left out never came
        # A kept definition is the campaign's own now: it may retire it.
        await self.memory.deprecate_term(GUILD_B, self.far, "vowed_to", source="dm")

    async def test_cherry_pick_that_takes_the_new_relationship_adds_it_as_its_own(self) -> None:
        await self.follow(await self.publish(["vowed_to"]))
        await self.memory.add_predicate(
            GUILD_A,
            self.pub,
            term("serves_on", "serves on"),
            examples=["x"],
            reason="y",
            source="dm",
        )
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, [], None, plan_ok=True,
            definitions=[mine["vowed_to"], mine["serves_on"]],
        )  # fmt: skip
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        taken = await self.sets.take_update(
            GUILD_B, self.far, DM_B, accepted=set(), accepted_defs={"serves_on"}
        )
        self.assertTrue(taken.merged)
        done = await set_sync.keep_as_own(self.memory, GUILD_B, self.far, taken)
        self.assertEqual(done.added, ("serves on",))
        self.assertEqual(await self.borrowed(), {})

    async def test_a_followers_backup_holds_the_marked_definitions_and_the_follow_matches(
        self,
    ) -> None:
        await self.memory.add_predicate(
            GUILD_B, self.far, term("mentor_of", "mentors"), examples=["x"], reason="y", source="dm"
        )
        await self.follow(await self.publish(["vowed_to"]))
        async with self.db.guild(GUILD_B) as conn:
            rows = await MemorySection().dump(conn, GUILD_B, self.far)
            followed = await FollowedSetsSection().dump(conn, GUILD_B, self.far)
        marks = {r["key"]: r["from_set"] for r in rows if r.get("table") == "predicate"}
        self.assertIsNone(marks["mentor_of"])
        self.assertIsNotNone(marks["vowed_to"])
        self.assertEqual([d["key"] for d in followed[0]["content"]["definitions"]], ["vowed_to"])
        MemorySection().check(rows)

    async def test_a_restore_that_lost_the_follow_cleans_up_at_the_next_reconcile(self) -> None:
        await self.follow(await self.publish(["vowed_to"]))
        await self.sets.unfollow(GUILD_B, self.far, DM_B)  # (as a restore without the follow)
        done = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertEqual(done.removed, ("vows loyalty to",))
        self.assertEqual(await self.borrowed(), {})

    async def test_the_two_kinds_of_pick_are_independent(self) -> None:
        await self.follow(await self.publish(["vowed_to", "guards"]))
        mine = {d["key"]: d for d in await sd.own_definitions(self.memory, GUILD_A, self.pub)}
        await self.sets.publish_changes(
            GUILD_A, self.pub, DM_A, [], None, plan_ok=True, definitions=[mine["vowed_to"]]
        )  # one rule goes and one relationship goes
        update = await self.sets.pending_update(GUILD_B, self.far)
        assert update is not None
        # Rules all taken, the relationship removal refused: a Cherry pick that keeps guards.
        taken = await self.sets.take_update(
            GUILD_B,
            self.far,
            DM_B,
            accepted={c.number for c in update.changes},
            accepted_defs=set(),
        )
        self.assertTrue(taken.merged)
        self.assertEqual(sorted(d["key"] for d in taken.kept_definitions), ["guards", "vowed_to"])

    async def test_reconcile_is_a_no_op_when_nothing_changed(self) -> None:
        await self.follow(await self.publish(["vowed_to"]))
        again = await set_sync.reconcile(self.memory, self.sets, GUILD_B, self.far)
        self.assertFalse(again.changed)

    async def test_nothing_of_the_publishers_memory_reaches_the_follower(self) -> None:
        a = await self.memory.add_entity(
            GUILD_A, self.pub, name="Mira", type="character", source="dm"
        )
        await self.follow(await self.publish(["vowed_to"]))
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_entities WHERE campaign_id = %s", (self.far,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
        _ = a


if __name__ == "__main__":
    unittest.main()
