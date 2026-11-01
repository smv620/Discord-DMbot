"""The internal importance rank (#3172): the floor, the history and the reader. Invented names."""

from __future__ import annotations

import unittest
from typing import Any, cast

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
        ok = ir.Assessment("e1", 3, "mentor")
        self.assertTrue(ok.valid())
        for bad in (
            ir.Assessment("", 3, "mentor"),
            ir.Assessment("e1", 6, "mentor"),
            ir.Assessment("e1", -1, "mentor"),
            ir.Assessment("e1", 3, "because he is cool"),
            ir.Assessment("e1", 3, "mentor", source="dm"),
        ):
            self.assertFalse(bad.valid(), bad)

    def test_counts_by_rank_are_for_a_log_line(self) -> None:
        self.assertEqual(ir.counts({"a": 0, "b": 0, "c": 5, "d": 2}), [2, 0, 1, 0, 0, 1])


class TheNudge(unittest.TestCase):
    def test_six_in_play_lifts_one_rank(self) -> None:
        self.assertEqual(ir.nudged(ir.MINOR, 6, 0), ir.RECURRING)
        self.assertEqual(ir.nudged(ir.MINOR, 5, 0), ir.MINOR)

    def test_a_lore_connection_counts_a_quarter(self) -> None:
        self.assertEqual(ir.nudged(ir.MINOR, 0, 23), ir.MINOR)
        self.assertEqual(ir.nudged(ir.MINOR, 0, 24), ir.RECURRING)
        self.assertEqual(ir.nudged(ir.MINOR, 4, 8), ir.RECURRING)  # 4 + 2

    def test_it_never_goes_past_major_and_never_moves_a_major_or_a_player(self) -> None:
        self.assertEqual(ir.nudged(ir.SIGNIFICANT, 99, 99), ir.MAJOR)
        self.assertEqual(ir.nudged(ir.MAJOR, 99, 99), ir.MAJOR)
        self.assertEqual(ir.nudged(ir.PLAYER, 99, 99), ir.PLAYER)

    def test_it_lifts_only_one_rank_however_many_connections(self) -> None:
        self.assertEqual(ir.nudged(ir.BACKGROUND, 500, 0), ir.MINOR)


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
    async def test_everyone_starts_as_background_and_a_place_has_no_rank(self) -> None:
        got = await self.ranks()
        self.assertEqual(got, {self.garrick: 0, self.wren: 0})

    async def test_a_player_character_is_five_with_no_assessment(self) -> None:
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, self.wren, "player_character", source="dm", played_by=77
        )
        self.assertEqual((await self.ranks())[self.wren], ir.PLAYER)

    async def test_the_newest_assessment_is_current_and_history_is_kept(self) -> None:
        a = ir.Assessment(self.garrick, 3, "mentor", lines=(4, 9))
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [a]), 1)
        self.assertEqual((await self.ranks())[self.garrick], 3)
        # raised, then corrected down: both rows stay, the newest is current
        up = ir.Assessment(self.garrick, 4, "antagonist")
        down = ir.Assessment(self.garrick, 2, "corrected")
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [up, down]), 2)
        self.assertEqual((await self.ranks())[self.garrick], 2)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT rank, basis FROM memory_importance WHERE entity_id = %s ORDER BY seq",
                (self.garrick,),
            )
            self.assertEqual([(r["rank"], r["basis"]) for r in await cur.fetchall()],
                             [(3, "mentor"), (4, "antagonist"), (2, "corrected")])  # fmt: skip

    async def test_the_same_rank_twice_is_written_once(self) -> None:
        a = ir.Assessment(self.garrick, 3, "mentor")
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
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 1, "met_a_pc")])
        self.assertEqual((await self.ranks())[self.garrick], ir.RECURRING)  # (never below)
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "antagonist")]
        )
        self.assertEqual((await self.ranks())[self.garrick], ir.MAJOR)

    async def test_something_that_is_not_a_character_is_not_assessed(self) -> None:
        wrote = await self.store.add(
            GUILD, self.campaign.id,
            [ir.Assessment(self.ashmere, 3, "lore_figure"), ir.Assessment("nope", 3, "mentor")],
        )  # fmt: skip
        self.assertEqual(wrote, 0)

    async def test_a_merged_name_counts_for_the_one_that_stayed(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Garrick the Smith", "npc", CONFIRMED)], source="dm"
        )
        (twin,) = made.value
        assert twin
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 3, "mentor")])
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
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(a, 3, "mentor")])
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
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 4, "antagonist")])
        await self.merge(self.garrick, twin)
        self.assertEqual((await self.ranks())[self.garrick], 4)
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 1, "corrected")])
        self.assertEqual((await self.ranks())[self.garrick], 1)  # the newest, not the highest

    async def test_a_merged_or_rejected_entry_is_not_assessed(self) -> None:
        twin = await self.twin("Garrick the Smith")
        await self.merge(self.garrick, twin)
        self.assertEqual(
            await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 3, "mentor")]), 0
        )

    async def test_no_more_than_twenty_line_numbers_are_kept(self) -> None:
        a = ir.Assessment(self.garrick, 3, "mentor", lines=tuple(range(1, 40)))
        await self.store.add(GUILD, self.campaign.id, [a])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT cardinality(lines) AS n FROM memory_importance")
            (row,) = await cur.fetchall()
        self.assertEqual(row["n"], ir.MAX_LINES)

    async def test_a_correction_back_to_a_twins_old_rank_is_still_written(self) -> None:
        # Garrick 3, the twin 4, merged, then Garrick 3 again: the group's newest is 4, so a 3
        # is a correction and not a repeat (add() compares with the group, as ranks() reads it).
        twin = await self.twin("Garrick the Smith")
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "mentor")])
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(twin, 4, "antagonist")])
        await self.merge(self.garrick, twin)
        self.assertEqual((await self.ranks())[self.garrick], 4)
        wrote = await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "corrected")]
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
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "mentor")])
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "antagonist")]
        )
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 2, "corrected")])
        await self.store.add(
            GUILD, self.campaign.id, [ir.Assessment(twin, 1, "met_a_pc", lines=(1, 2))]
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
            "entity_id": "a" * 32, "rank": 2, "basis": "mentor", "source": "plotbot",
            "session_started_at": None, "recorded_at": 5,
        }  # fmt: skip
        ImportanceSection().check([good])
        for bad in (
            {**good, "rank": 6}, {**good, "rank": True}, {**good, "basis": "cool"},
            {**good, "source": "dm"}, {**good, "entity_id": "x"}, {**good, "recorded_at": 0},
            {**good, "extra": 1},
        ):  # fmt: skip
            with self.assertRaises(CampaignError):
                ImportanceSection().check([bad])
        with self.assertRaises(CampaignError):
            ImportanceSection().check(cast(Any, "not a list"))

    async def test_the_reason_list_in_the_database_is_the_code_list(self) -> None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conrelid = 'memory_importance'::regclass AND contype = 'c'"
                " AND pg_get_constraintdef(oid) LIKE '%basis%'"
            )
            (row,) = await cur.fetchall()
        import re

        self.assertEqual(set(re.findall(r"'([a-z_]+)'::text", row["d"])), set(ir.BASES))


if __name__ == "__main__":
    unittest.main()


class TheNudgeInTheStore(Base):
    made = 0

    async def friends(self, count: int, *, source: str = "dm", secret: bool = False) -> list[str]:
        """`count` other characters, each joined to Garrick by a confirmed connection. `source`
        "background" is a connection that came from a lore upload (written by the DM, then marked
        as the lore read's, which only the reader itself can confirm)."""
        out = []
        for _ in range(count):
            self.made += 1
            other = (
                await self.memory.add_names(
                    GUILD,
                    self.campaign.id,
                    [NewName(f"Friend {self.made}", "npc", CONFIRMED)],
                    source="dm",
                )
            ).value[0]
            assert other
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.garrick, "rival_of", other, source="dm",
                confidence=1.0, status=CONFIRMED, secret=secret,
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

    async def test_six_connections_made_in_play_lift_garrick_one_rank(self) -> None:
        await self.friends(5)
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)
        await self.friends(1, source="dm")
        self.assertEqual((await self.ranks())[self.garrick], ir.MINOR)

    async def test_lore_connections_count_a_quarter(self) -> None:
        await self.friends(23, source="background")
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)
        await self.friends(1, source="background")
        self.assertEqual((await self.ranks())[self.garrick], ir.MINOR)

    async def test_a_secret_connection_adds_no_rank(self) -> None:
        await self.friends(6, secret=True)
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)

    async def test_a_pair_counts_once_however_many_connections_join_it(self) -> None:
        (other,) = await self.friends(1)
        for predicate in ("mentor_of", "ally_of", "knows"):
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.garrick, predicate, other, source="dm",
                confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)

    async def test_a_place_is_not_a_connection_that_lifts(self) -> None:
        for _ in range(6):
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.garrick, "located_in", self.ashmere, source="dm",
                confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        self.assertEqual((await self.ranks())[self.garrick], ir.BACKGROUND)
