"""The internal importance rank (#3172): the floor, the history and the reader. Invented names."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar, cast

from dmbot.memory import importance_rank as ir
from dmbot.memory.importance_backup import ImportanceSection
from dmbot.memory.models import CONFIRMED, Heard, NewName
from tests.test_memory_names import DM, GUILD, NamesTest


class TheRules(unittest.TestCase):
    def test_the_levels_are_the_owners_six(self) -> None:
        self.assertEqual(
            ir.LEVEL_NAMES,
            ("Background", "Minor", "Recurring", "Significant", "Major", "Player character"),
        )
        self.assertEqual(ir.level_name(99), "Player character")

    def test_the_floor_is_what_the_code_can_count(self) -> None:
        self.assertEqual(ir.floor(True, 0), ir.PLAYER)
        self.assertEqual(ir.floor(False, 0), ir.BACKGROUND)
        self.assertEqual(ir.floor(False, 1), ir.BACKGROUND)  # (one session is not recurring)
        self.assertEqual(ir.floor(False, 2), ir.RECURRING)

    def test_a_rank_is_never_read_below_the_floor(self) -> None:
        self.assertEqual(ir.effective(ir.RECURRING, ir.MINOR), ir.RECURRING)
        self.assertEqual(ir.effective(ir.RECURRING, ir.MAJOR), ir.MAJOR)
        self.assertEqual(ir.effective(ir.BACKGROUND, None), ir.BACKGROUND)

    def test_an_assessment_needs_a_rank_a_known_reason_and_a_source(self) -> None:
        ok = ir.Assessment("e1", 3, "reading")
        self.assertTrue(ok.valid())
        for bad in (
            ir.Assessment("", 3, "reading"),
            ir.Assessment("e1", 6, "reading"),
            ir.Assessment("e1", -1, "reading"),
            ir.Assessment("e1", 3, "because he is cool"),
            ir.Assessment("e1", 3, "reading", source="dm"),
        ):
            self.assertFalse(bad.valid(), bad)

    def test_counts_by_rank_are_for_a_log_line(self) -> None:
        self.assertEqual(ir.counts({"a": 0, "b": 0, "c": 5, "d": 2}), [2, 0, 1, 0, 0, 1])


class TheFloors(unittest.TestCase):
    def test_the_connection_table_is_a_lookup_not_a_sum(self) -> None:
        got = [ir.connection_floor(n) for n in (0, 3, 4, 7, 8, 11, 12, 99)]
        self.assertEqual(got, [0, 0, 1, 1, 2, 2, 3, 3])

    def test_no_count_of_connections_reaches_major_or_the_players_five(self) -> None:
        self.assertEqual(ir.connection_floor(10_000), ir.SIGNIFICANT)

    def test_the_highest_floor_wins_and_they_do_not_add_up(self) -> None:
        self.assertEqual(ir.floor(False, 2, 4), ir.RECURRING)  # heard twice 2, four pairs 1
        self.assertEqual(ir.floor(False, 0, 12), ir.SIGNIFICANT)
        self.assertEqual(ir.floor(False, 5, 12), ir.SIGNIFICANT)  # not 2 + 3
        self.assertEqual(ir.floor(False, 0, 0, True), ir.SIGNIFICANT)  # a companion
        self.assertEqual(ir.floor(False, 0, 12, True), ir.SIGNIFICANT)
        self.assertEqual(ir.floor(True, 0, 0), ir.PLAYER)


class Base(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ir.ImportanceStore(self.db, lambda: 1_000)
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Garrick", "npc", CONFIRMED), NewName("Wren", "npc", CONFIRMED),
             NewName("Ashmere", "place", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        garrick, wren, ashmere = made.value
        assert garrick and wren and ashmere
        self.garrick: str = garrick  # (typed, so a test can pass them on)
        self.wren: str = wren
        self.ashmere: str = ashmere

    async def ranks(self) -> dict[str, int]:
        return await self.store.ranks(GUILD, self.campaign.id)


class TheStore(Base):
    async def test_everyone_starts_as_background_places_included(self) -> None:
        got = await self.ranks()
        self.assertEqual(got, {self.garrick: 0, self.wren: 0, self.ashmere: 0})

    async def test_a_player_character_is_five_with_no_assessment(self) -> None:
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, self.wren, "player_character", source="dm", played_by=77
        )
        self.assertEqual((await self.ranks())[self.wren], ir.PLAYER)

    async def test_the_newest_assessment_is_current_and_history_is_kept(self) -> None:
        a = ir.Assessment(self.garrick, 3, "reading", lines=(4, 9))
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [a]), 1)
        self.assertEqual((await self.ranks())[self.garrick], 3)
        # raised, then corrected down: both rows stay, the newest is current
        up = ir.Assessment(self.garrick, 4, "reading")
        down = ir.Assessment(self.garrick, 2, "dm_corrected")
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [up, down]), 2)
        self.assertEqual((await self.ranks())[self.garrick], 2)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT rank, reason FROM memory_importance WHERE entity_id = %s ORDER BY seq",
                (self.garrick,),
            )
            self.assertEqual([(r["rank"], r["reason"]) for r in await cur.fetchall()],
                             [(3, "reading"), (4, "reading"), (2, "dm_corrected")])  # fmt: skip

    async def test_the_same_rank_twice_is_written_once(self) -> None:
        a = ir.Assessment(self.garrick, 3, "reading")
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [a]), 1)
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [a]), 0)

    async def test_a_floor_holds_a_lower_assessment_up(self) -> None:
        await self.memory.add_session_heard(
            GUILD, self.campaign.id, 100, [Heard(self.garrick, DM, 3)]
        )
        await self.memory.add_session_heard(
            GUILD, self.campaign.id, 200, [Heard(self.garrick, DM, 1)]
        )
        self.assertEqual((await self.ranks())[self.garrick], ir.RECURRING)  # heard in two sessions
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 1, "reading")])
        self.assertEqual((await self.ranks())[self.garrick], ir.RECURRING)  # (never below)
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "reading")])
        self.assertEqual((await self.ranks())[self.garrick], ir.MAJOR)

    async def test_a_place_is_assessed_like_any_entry_and_an_unknown_id_is_not(self) -> None:
        wrote = await self.store.add(
            GUILD, self.campaign.id,
            [ir.Assessment(self.ashmere, 3, "reading"), ir.Assessment("nope", 3, "reading")],
        )  # fmt: skip
        self.assertEqual(wrote, 1)
        self.assertEqual((await self.ranks())[self.ashmere], ir.SIGNIFICANT)

    async def test_a_merged_name_counts_for_the_one_that_stayed(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Garrick the Smith", "npc", CONFIRMED)], source="dm"
        )
        (twin,) = made.value
        assert twin
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 3, "reading")])
        await self.memory.merge(
            GUILD, self.campaign.id, self.garrick, twin, source="dm", dm_said_same=True
        )
        got = await self.ranks()
        self.assertEqual(got[self.garrick], 3)
        self.assertNotIn(twin, got)

    async def twin(self, name: str) -> str:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName(name, "npc", CONFIRMED)], source="dm"
        )
        (one,) = made.value
        assert one
        return one

    async def merge(self, keep: str, gone: str) -> None:
        await self.memory.merge(GUILD, self.campaign.id, keep, gone, source="dm", dm_said_same=True)

    async def test_a_chain_of_merges_still_reaches_the_character_that_stayed(self) -> None:
        a, b = await self.twin("Garrick the Smith"), await self.twin("Old Garrick")
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(a, 3, "reading")])
        await self.memory.add_session_heard(GUILD, self.campaign.id, 100, [Heard(a, DM, 1)])
        await self.memory.add_session_heard(GUILD, self.campaign.id, 200, [Heard(b, DM, 1)])
        await self.merge(b, a)  # A into B
        await self.merge(self.garrick, b)  # then B into the character that stays
        got = await self.ranks()
        self.assertEqual(got[self.garrick], 3)  # (A's assessment, two merges away)
        self.assertEqual({a, b} & set(got), set())

    async def test_sessions_of_merged_names_add_up_toward_the_floor(self) -> None:
        twin = await self.twin("Garrick the Smith")
        await self.memory.add_session_heard(
            GUILD, self.campaign.id, 100, [Heard(self.garrick, DM, 1)]
        )
        await self.memory.add_session_heard(GUILD, self.campaign.id, 200, [Heard(twin, DM, 1)])
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)
        await self.merge(self.garrick, twin)
        self.assertEqual((await self.ranks())[self.garrick], ir.RECURRING)  # two sessions now

    async def test_a_correction_after_a_merge_lowers_the_rank_again(self) -> None:
        twin = await self.twin("Garrick the Smith")
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 4, "reading")])
        await self.merge(self.garrick, twin)
        self.assertEqual((await self.ranks())[self.garrick], 4)
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 1, "dm_corrected")]
        )
        self.assertEqual((await self.ranks())[self.garrick], 1)  # the newest, not the highest

    async def test_a_merged_or_rejected_entry_is_not_assessed(self) -> None:
        twin = await self.twin("Garrick the Smith")
        await self.merge(self.garrick, twin)
        self.assertEqual(
            await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 3, "reading")]), 0
        )

    async def test_no_more_than_twenty_line_numbers_are_kept(self) -> None:
        a = ir.Assessment(self.garrick, 3, "reading", lines=tuple(range(1, 40)))
        await self.store.add(GUILD, self.campaign.id, [a])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT cardinality(lines) AS n FROM memory_importance")
            (row,) = await cur.fetchall()
        self.assertEqual(row["n"], ir.MAX_LINES)

    async def test_a_correction_back_to_a_twins_old_rank_is_still_written(self) -> None:
        # Garrick 3, the twin 4, merged, then Garrick 3 again: the group's newest is 4, so a 3
        # is a correction and not a repeat (add() compares with the group, as ranks() reads it).
        twin = await self.twin("Garrick the Smith")
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "reading")])
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 4, "reading")])
        await self.merge(self.garrick, twin)
        self.assertEqual((await self.ranks())[self.garrick], 4)
        wrote = await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "dm_corrected")]
        )
        self.assertEqual(wrote, 1)
        self.assertEqual((await self.ranks())[self.garrick], 3)

    async def test_the_walk_that_finds_who_a_name_became_is_the_same_for_both(self) -> None:
        rows: dict[str, dict[str, object]] = {
            "a": {"status": "merged", "merged_into": "b"},
            "b": {"status": "merged", "merged_into": "c"},
            "c": {"status": "confirmed", "merged_into": None},
            "loop1": {"status": "merged", "merged_into": "loop2"},
            "loop2": {"status": "merged", "merged_into": "loop1"},
            "lost": {"status": "merged", "merged_into": "nowhere"},
        }
        got = ir.roots(rows)
        self.assertEqual((got["a"], got["b"], got["c"]), ("c", "c", "c"))
        self.assertEqual((got["loop1"], got["lost"]), (None, None))

    async def test_a_backup_brings_the_ranks_back_in_the_same_order(self) -> None:
        twin = await self.twin("Wren the Quiet")
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "reading")])
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "reading")])
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 2, "dm_corrected")]
        )
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(twin, 1, "reading", lines=(1, 2))]
        )
        section = ImportanceSection()
        async with self.db.guild(GUILD) as conn:
            rows = await section.dump(conn, GUILD, self.campaign.id)
        self.assertEqual([r["rank"] for r in rows], [3, 4, 2, 1])  # oldest first
        self.assertTrue(all("lines" not in r for r in rows))  # (no line numbers in a file)
        before = await self.ranks()
        async with self.db.guild(GUILD) as conn:
            await section.clear(conn, GUILD, self.campaign.id)
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)
        async with self.db.guild(GUILD) as conn:
            await section.load(conn, GUILD, self.campaign.id, section.check(rows))
        self.assertEqual(await self.ranks(), before)  # the newest (2) is still the newest
        # and a row for an entry the campaign no longer has is dropped, not refused
        async with self.db.guild(GUILD) as conn:
            await section.load(conn, GUILD, self.campaign.id, [{**rows[0], "entity_id": "f" * 32}])

    async def test_a_damaged_importance_section_is_refused_whole(self) -> None:
        from dmbot.campaigns.models import CampaignError

        good = {
            "entity_id": "a" * 32, "rank": 2, "reason": "reading", "source": "plotbot",
            "session_started_at": None, "recorded_at": 5,
        }  # fmt: skip
        ImportanceSection().check([good])
        for bad in (
            {**good, "rank": 6}, {**good, "rank": True}, {**good, "reason": "cool"},
            {**good, "source": "dm"}, {**good, "entity_id": "x"}, {**good, "recorded_at": 0},
            {**good, "extra": 1},
        ):  # fmt: skip
            with self.assertRaises(CampaignError):
                ImportanceSection().check([bad])
        with self.assertRaises(CampaignError):
            ImportanceSection().check(cast(Any, "not a list"))

    async def test_a_place_s_rank_comes_back_from_a_backup_too(self) -> None:
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.ashmere, 4, "reading")])
        section = ImportanceSection()
        async with self.db.guild(GUILD) as conn:
            rows = await section.dump(conn, GUILD, self.campaign.id)
            await section.clear(conn, GUILD, self.campaign.id)
            await section.load(conn, GUILD, self.campaign.id, section.check(rows))
        self.assertEqual((await self.ranks())[self.ashmere], 4)  # (every kind is ranked, #3233)

    async def test_a_backup_from_before_0388_reads_its_basis_as_the_migration_did(self) -> None:
        from dmbot.campaigns.models import CampaignError

        old = {
            "entity_id": "a" * 32, "rank": 2, "basis": "mentor", "source": "plotbot",
            "session_started_at": None, "recorded_at": 5,
        }  # fmt: skip
        got = ImportanceSection().check([old, {**old, "basis": "corrected"}])
        self.assertEqual([r[2] for r in got], ["reading", "dm_corrected"])
        for bad in ({**old, "basis": "cool"}, {**old, "reason": "reading"}):
            with self.assertRaises(CampaignError):
                ImportanceSection().check([bad])

    async def test_the_reason_list_in_the_database_is_the_code_list(self) -> None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conrelid = 'memory_importance'::regclass AND contype = 'c'"
                " AND pg_get_constraintdef(oid) LIKE '%reason%'"
            )
            (row,) = await cur.fetchall()
        import re

        self.assertEqual(set(re.findall(r"'([a-z_]+)'::text", row["d"])), set(ir.REASONS))


if __name__ == "__main__":
    unittest.main()


class TheFloorsInTheStore(Base):
    made = 0

    async def friends(self, count: int, *, source: str = "dm", kind: str = "npc") -> list[str]:
        """`count` other entries, each joined to Garrick by a confirmed connection. `source`
        other than "dm" is set on the rows afterwards (only the readers themselves confirm)."""
        out = []
        for _ in range(count):
            self.made += 1
            other = (
                await self.memory.add_names(
                    GUILD,
                    self.campaign.id,
                    [NewName(f"Friend {self.made}", kind, CONFIRMED)],
                    source="dm",
                )
            ).value[0]
            assert other
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.garrick, "rival_of", other, source="dm",
                confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
            if source != "dm":
                async with self.db.guild(GUILD) as conn:
                    await conn.execute(
                        "UPDATE memory_relations SET source = %s"
                        " WHERE object_id = %s OR subject_id = %s",
                        (source, other, other),
                    )
            out.append(other)
        return out

    async def garrick_rank(self) -> int:
        return (await self.ranks())[self.garrick]

    async def test_the_table_lifts_garrick_at_four_eight_and_twelve(self) -> None:
        seen = {}
        for step in (3, 1, 3, 1, 3, 1):  # 3, 4, 7, 8, 11, 12 pairs
            await self.friends(step)
            seen[self.made] = await self.garrick_rank()
        self.assertEqual(seen[3], 0)
        self.assertEqual((seen[4], seen[7]), (1, 1))
        self.assertEqual((seen[8], seen[11]), (2, 2))
        self.assertEqual(seen[12], 3)

    async def test_lore_and_secret_connections_count_in_full(self) -> None:
        await self.friends(2, source="background")
        for _ in range(2):
            (other,) = await self.friends(1)
            async with self.db.guild(GUILD) as conn:
                await conn.execute(
                    "UPDATE memory_relations SET secret = true"
                    " WHERE object_id = %s OR subject_id = %s", (other, other),
                )  # fmt: skip
        self.assertEqual(await self.garrick_rank(), ir.MINOR)  # four pairs

    async def test_a_pair_counts_once_however_many_connections_join_it(self) -> None:
        (other,) = await self.friends(1)
        for predicate in ("mentor_of", "ally_of", "knows"):
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.garrick, predicate, other, source="dm",
                confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        await self.friends(2)
        self.assertEqual(await self.garrick_rank(), ir.BACKGROUND)  # three pairs

    async def test_an_ended_connection_and_a_plays_or_party_link_count_for_nothing(self) -> None:
        await self.friends(4)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE memory_relations SET to_session_at = 5")
        self.assertEqual(await self.garrick_rank(), ir.BACKGROUND)
        await self.friends(4, source="party")
        self.assertEqual(await self.garrick_rank(), ir.BACKGROUND)

    async def test_a_worked_out_link_counts_once_the_dm_has_confirmed_it(self) -> None:
        await self.friends(4, source="worked_out")
        self.assertEqual(await self.garrick_rank(), ir.MINOR)

    async def test_a_place_is_ranked_too_and_a_table_person_is_not(self) -> None:
        for _ in range(4):
            self.made += 1
            other = (
                await self.memory.add_names(
                    GUILD,
                    self.campaign.id,
                    [NewName(f"Hall {self.made}", "place", CONFIRMED)],
                    source="dm",
                )
            ).value[0]
            assert other
            await self.memory.add_relation(
                GUILD, self.campaign.id, other, "located_in", self.ashmere, source="dm",
                confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        ranks = await self.ranks()
        self.assertEqual(ranks[self.ashmere], ir.MINOR)  # four places in it


class ThePurePart(unittest.TestCase):
    """The website hands over only counted facts; the same functions the bot's reader uses turn
    them into ranks (#3236), so the two cannot disagree. Invented ids."""

    ENTITIES: ClassVar[dict[str, dict[str, object]]] = {
        "pc": {"type": "character", "role": "player_character", "status": "confirmed",
               "merged_into": None},
        "friend": {"type": "character", "role": "npc", "status": "confirmed", "merged_into": None},
        "old": {"type": "character", "role": "npc", "status": "merged", "merged_into": "friend"},
        "town": {"type": "place", "role": None, "status": "confirmed", "merged_into": None},
    }  # fmt: skip

    def test_a_player_character_is_five_and_a_companion_is_at_least_significant(self) -> None:
        ended = ir.roots(self.ENTITIES)
        links, companions = ir.links_from(
            [("friend", "pc", ir.COMPANION_PREDICATE)], self.ENTITIES, ended
        )
        out = ir.ranks_from(self.ENTITIES, ended, {}, {}, links, companions)
        self.assertEqual(out["pc"], ir.PLAYER)
        self.assertGreaterEqual(out["friend"], ir.COMPANION_FLOOR)
        self.assertNotIn("old", out)

    def test_a_merged_names_sessions_and_assessment_count_for_the_survivor(self) -> None:
        ended = ir.roots(self.ENTITIES)
        heard = {"old": {1}, "friend": {2}}
        group = ir.newest_by_group(ended, {"old": (5, 4)})
        out = ir.ranks_from(self.ENTITIES, ended, group, heard, {}, ())
        self.assertEqual(out["friend"], 4)  # (the older twin's assessment stands for it)
        self.assertEqual(
            ir.ranks_from(self.ENTITIES, ended, {}, heard, {}, ())["friend"],
            ir.RECURRING,  # (two sessions across the merge)
        )

    def test_a_pair_counts_once_and_an_unconfirmed_end_counts_for_nothing(self) -> None:
        ghost: dict[str, object] = {
            "type": "character", "role": "npc", "status": "proposed", "merged_into": None
        }  # fmt: skip
        entities: dict[str, dict[str, object]] = {**self.ENTITIES, "ghost": ghost}
        ended = ir.roots(entities)
        links, _ = ir.links_from(
            [("friend", "town", "lives_in"), ("town", "friend", "ruled_by"),
             ("friend", "ghost", "knows")], entities, ended
        )  # fmt: skip
        self.assertEqual(links, {"friend": 1, "town": 1})
