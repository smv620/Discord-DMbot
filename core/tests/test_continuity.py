"""Continuity warnings after a session (#1320): what a session's unanswered claims clash with in
the confirmed story, how sure DMbot is, and what each answer writes. Runs against Postgres."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.checks import ACTS_AFTER_END, CONTRADICTION, HELD_TWICE
from dmbot.memory.claims import GONE, ClaimStore, Extracted
from dmbot.memory.continuity import (
    BACK_FROM_THE_END,
    CHANGED,
    IGNORE,
    LIKELY,
    NOT_TRUE,
    POSSIBLE,
    PROBABLE,
    ContinuityStore,
    Levels,
    reached_critical_mass,
)
from dmbot.memory.models import ALIVE, DEAD, MemoryRuleError
from dmbot.ui import continuity as ui
from tests.test_memory_store import GUILD_A, GUILD_B, ConfirmedMemoryTest

SMALL = Levels(min_facts=1, min_sessions=1)  # a little story is enough in a test
AGREED = {7, 8}  # who is recorded now
LATER = 500  # the session being checked
EARLIER = 100


def said(subject: str, relationship: str, obj: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "dm_said")


class Levelling(unittest.TestCase):
    def test_a_probability_reaches_the_level_it_has_earned(self) -> None:
        levels = Levels()
        self.assertEqual(
            [levels.level_of(p) for p in (0.1, 0.25, 0.5, 0.51, 0.79, 0.8, 1.0)],
            [None, POSSIBLE, POSSIBLE, PROBABLE, PROBABLE, LIKELY, LIKELY],
        )

    def test_the_boundaries_are_the_ones_in_the_design(self) -> None:
        levels = Levels()
        self.assertEqual(levels.level_of(0.50), POSSIBLE)  # probable is MORE than 50%
        self.assertEqual(levels.level_of(0.80), LIKELY)  # likely is 80% or more
        self.assertEqual(levels.level_of(0.25), POSSIBLE)  # possible is 25% or more
        self.assertIsNone(levels.level_of(0.24))

    def test_critical_mass_needs_both_enough_facts_and_enough_sessions(self) -> None:
        levels = Levels()
        self.assertFalse(reached_critical_mass(49, 3, levels))
        self.assertFalse(reached_critical_mass(50, 2, levels))
        self.assertTrue(reached_critical_mass(50, 3, levels))


class Continuity(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.store = ContinuityStore(self.memory)
        self.ulfgar = await self.add("Ulfgar", type="character")
        self.cerric = await self.add("Cerric", type="character")
        self.bram = await self.add("Bram", type="character")
        self.kesh = await self.add("Kesh", type="character")
        self.bow = await self.add("Heartseeker", type="item")
        await self.memory.set_unique(GUILD_A, self.c, self.bow, True, source="dm")

    async def peace(self) -> None:
        """A confirmed fact that clashes with nothing below: Cerric is Kesh's ally (Session 1)."""
        await self.relate(
            self.cerric, "ally_of", self.kesh, status="confirmed", from_session_at=EARLIER
        )

    async def story(self) -> None:
        """What the DM has confirmed so far: Bram carries Heartseeker (Session 1)."""
        await self.relate(
            self.bram, "carries", self.bow, status="confirmed", from_session_at=EARLIER
        )

    async def claim(self, *found: Extracted, speakers: dict[str, int] | None = None) -> None:
        await self.claims.propose(
            GUILD_A, self.c, list(found), speakers or {"1": 7}, opted_in=AGREED,
            prompt_version="p1", model="m1", session_started_at=LATER,
        )  # fmt: skip

    async def check(self, opted_in: set[int] | None = None, levels: Levels = SMALL) -> Any:
        return await self.store.check(
            GUILD_A, self.c, opted_in=AGREED if opted_in is None else opted_in, levels=levels
        )

    async def test_nothing_is_shown_below_the_critical_mass(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        report = await self.check(levels=Levels(min_facts=50, min_sessions=3))
        self.assertFalse(report.ready)
        self.assertEqual(report.conflicts, ())
        self.assertEqual(report.facts, 1)

    async def test_a_one_of_a_kind_item_in_two_hands_is_probable(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        report = await self.check()
        self.assertTrue(report.ready)
        (conflict,) = report.conflicts
        self.assertEqual((conflict.kind, conflict.level), (HELD_TWICE, PROBABLE))
        self.assertEqual(conflict.earlier_words, "Bram carries Heartseeker")

    async def test_a_dead_one_acting_again_is_probable_and_says_who_died(self) -> None:
        await self.peace()
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual((conflict.kind, conflict.level), (ACTS_AFTER_END, PROBABLE))
        self.assertEqual((conflict.entity_name, conflict.ended_condition), ("Ulfgar", DEAD))

    async def test_saying_a_dead_one_is_alive_is_likely(self) -> None:
        await self.peace()
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "is", "alive"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual((conflict.kind, conflict.level), (BACK_FROM_THE_END, LIKELY))

    async def test_an_ally_called_an_enemy_is_a_likely_contradiction(self) -> None:
        await self.relate(
            self.ulfgar, "ally_of", self.cerric, status="confirmed", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "enemy of", "Cerric"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual((conflict.kind, conflict.level), (CONTRADICTION, LIKELY))

    async def test_raising_the_settings_above_a_rule_silences_it(self) -> None:
        await self.relate(
            self.ulfgar, "ally_of", self.cerric, status="confirmed", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "enemy of", "Cerric"))
        quiet = Levels(possible=0.95, probable=0.95, likely=0.95, min_facts=1, min_sessions=1)
        self.assertEqual((await self.check(levels=quiet)).conflicts, ())

    # ---- the DM's 📌 100% truth (#2447) -----------------------------------------------------

    async def test_a_claim_against_a_pinned_truth_is_off_the_rails_even_before_the_critical_mass(
        self,
    ) -> None:
        kept = await self.relate(self.bram, "carries", self.bow, status="confirmed")
        await self.memory.pin(GUILD_A, self.c, kept.value[0].id, True, source="dm")
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        report = await self.check(levels=Levels(min_facts=50, min_sessions=3))
        self.assertFalse(report.ready)
        (conflict,) = report.conflicts
        self.assertTrue(conflict.pinned)
        text = ui.line_text(conflict, {})
        self.assertIn("Check: ", text)
        self.assertIn("You pinned: Bram carries Heartseeker", text)
        self.assertIn("1 go against a pin", ui.waiting_text(1, 1))
        self.assertNotIn("pinned", ui.waiting_text(2))

    async def test_nothing_pinned_below_the_critical_mass_says_nothing(self) -> None:
        await self.relate(self.bram, "carries", self.bow, status="confirmed")
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        report = await self.check(levels=Levels(min_facts=50, min_sessions=3))
        self.assertEqual(report.conflicts, ())

    async def test_a_pinned_secret_truth_is_still_checked_and_a_pinned_one_comes_first(
        self,
    ) -> None:
        kept = await self.relate(self.bram, "carries", self.bow, status="confirmed", secret=True)
        await self.memory.pin(GUILD_A, self.c, kept.value[0].id, True, source="dm")
        await self.relate(
            self.ulfgar, "ally_of", self.cerric, status="confirmed", from_session_at=EARLIER
        )
        await self.claim(
            said("Ulfgar", "enemy of", "Cerric"), said("Kesh", "carries", "Heartseeker")
        )
        first, second = (await self.check()).conflicts
        self.assertEqual((first.pinned, second.pinned), (True, False))

    async def test_story_changed_on_a_pinned_clash_unpins_it_in_the_same_change(self) -> None:
        kept = await self.relate(self.bram, "carries", self.bow, status="confirmed")
        rid = kept.value[0].id
        await self.memory.pin(GUILD_A, self.c, rid, True, source="dm")
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        await self.store.answer(
            GUILD_A, self.c, conflict.claim.id, CHANGED, opted_in=AGREED, levels=SMALL
        )
        self.assertEqual(await self.memory.pinned_connections(GUILD_A, self.c), set())

    async def test_an_ended_connection_is_not_pinned_any_more(self) -> None:
        kept = await self.relate(self.bram, "carries", self.bow, status="confirmed")
        rid = kept.value[0].id
        await self.memory.pin(GUILD_A, self.c, rid, True, source="dm")
        await self.memory.update_relation(GUILD_A, self.c, rid, source="dm", to_session_at=300)
        self.assertEqual(await self.memory.pinned_connections(GUILD_A, self.c), set())

    async def test_a_secret_fact_is_not_pointed_at(self) -> None:
        await self.relate(
            self.ulfgar, "ally_of", self.cerric, status="confirmed", from_session_at=EARLIER
        )
        await self.relate(self.bram, "carries", self.bow, status="confirmed", secret=True)
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_with_nothing_waiting_the_story_is_not_read(self) -> None:
        from unittest.mock import patch

        await self.story()
        with patch.object(ContinuityStore, "_world", side_effect=AssertionError("read")):
            report = await self.check()  # no claims at all
        self.assertEqual((report.claims, report.conflicts, report.ready), (0, (), True))

    async def test_the_line_for_an_ended_entry_names_where_it_was_said(self) -> None:
        await self.peace()
        await self.claim(said("Ulfgar", "is", "dead"))
        (waiting,) = await self.claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        await self.claims.confirm(GUILD_A, self.c, waiting.id, opted_in=AGREED)
        await self.claims.propose(  # a later session than the one that said he died
            GUILD_A, self.c, [said("Ulfgar", "carries", "Heartseeker")], {"1": 7},
            opted_in=AGREED, prompt_version="p1", model="m1", session_started_at=LATER + 400,
        )  # fmt: skip
        (conflict,) = (await self.check()).conflicts
        assert conflict.earlier is not None
        self.assertEqual(conflict.earlier.quote, "Ulfgar is dead")

    async def test_a_claim_that_fits_the_story_makes_no_warning(self) -> None:
        await self.story()
        await self.claim(
            said("Ulfgar", "ally of", "Cerric"), said("Bram", "carries", "Heartseeker")
        )
        self.assertEqual((await self.check()).conflicts, ())

    async def test_only_confirmed_facts_count_not_proposals(self) -> None:
        await self.relate(
            self.bram, "carries", self.bow, status="proposed", from_session_at=EARLIER
        )
        await self.relate(
            self.ulfgar, "ally_of", self.cerric, status="confirmed", from_session_at=EARLIER
        )
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_the_earlier_line_is_quoted_only_while_its_speakers_are_recorded(self) -> None:
        await self.claim(said("Bram", "carries", "Heartseeker"), speakers={"1": 8})
        (waiting,) = await self.claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        await self.claims.confirm(GUILD_A, self.c, waiting.id, opted_in=AGREED)
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        assert conflict.earlier is not None
        self.assertEqual(conflict.earlier.quote, "Bram carries Heartseeker")
        # Speaker 8 stops being recorded: the warning stays, the words do not.
        (conflict,) = (await self.check(opted_in={7})).conflicts
        assert conflict.earlier is not None
        self.assertIsNone(conflict.earlier.quote)

    async def test_another_campaigns_story_never_matches(self) -> None:
        await self.story()
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        kesh = await self.add("Kesh", campaign=other, type="character")
        bow = await self.add("Heartseeker", campaign=other, type="item")
        await self.memory.set_unique(GUILD_A, other, bow, True, source="dm")
        self.assertTrue(kesh and bow)
        await self.claims.propose(
            GUILD_A, other, [said("Kesh", "carries", "Heartseeker")], {"1": 7}, opted_in=AGREED,
            prompt_version="p1", model="m1", session_started_at=LATER,
        )  # fmt: skip
        report = await self.store.check(GUILD_A, other, opted_in=AGREED, levels=SMALL)
        self.assertEqual((report.conflicts, report.facts), ((), 0))
        # And another server cannot even open this one.
        with self.assertRaises(MemoryRuleError):
            await self.store.check(GUILD_B, self.c, opted_in=AGREED, levels=SMALL)

    # ---- the three answers ----------------------------------------------------------------

    async def answer(self, action: str, claim_id: str) -> str:
        return await self.store.answer(
            GUILD_A, self.c, claim_id, action, opted_in=AGREED, levels=SMALL
        )

    async def test_alive_after_all_brings_them_back_and_keeps_the_history(self) -> None:
        await self.peace()
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        await self.answer(CHANGED, conflict.claim.id)
        states = await self.memory.states(GUILD_A, self.c)
        self.assertEqual(states[self.ulfgar].condition, ALIVE)
        history = await self.memory.state_history(GUILD_A, self.c, self.ulfgar)
        by = {s.condition: s for s in history}  # the death is kept, closed; the clock is fixed
        self.assertEqual(sorted(by), [ALIVE, DEAD])
        self.assertIsNotNone(by[DEAD].closed_at)
        self.assertIsNone(by[ALIVE].closed_at)
        facts = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.ulfgar, confirmed_only=True
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual((await self.check()).conflicts, ())

    async def test_it_changed_hands_ends_the_old_fact_and_dates_the_new_one(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        await self.answer(CHANGED, conflict.claim.id)
        mine = {
            r.subject_id: r
            for r in await self.memory.relations(GUILD_A, self.c, confirmed_only=True)
        }
        self.assertEqual(mine[self.bram].to_session_at, LATER)  # Bram's ends where Kesh's starts
        self.assertEqual(mine[self.kesh].from_session_at, LATER)
        self.assertIsNone(mine[self.kesh].to_session_at)

    async def test_not_true_writes_nothing_and_is_not_asked_again(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        await self.answer(NOT_TRUE, conflict.claim.id)
        self.assertEqual((await self.check()).conflicts, ())
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, confirmed_only=True)), 1)
        with self.assertRaises(MemoryRuleError) as raised:
            await self.answer(NOT_TRUE, conflict.claim.id)
        self.assertEqual(str(raised.exception), GONE)

    async def test_ignore_writes_nothing_and_leaves_the_claim_to_answer_later(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        await self.answer(IGNORE, conflict.claim.id)
        (still,) = await self.claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        self.assertEqual(still.status, "proposed")
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, confirmed_only=True)), 1)

    async def test_a_failure_part_way_leaves_nothing_changed(self) -> None:
        from unittest.mock import patch

        await self.peace()
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=EARLIER
        )
        await self.claim(said("Ulfgar", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        with (
            patch.object(self.memory, "add_relation_in", side_effect=MemoryRuleError("no")),
            self.assertRaises(MemoryRuleError),
        ):
            await self.answer(CHANGED, conflict.claim.id)
        states = await self.memory.states(GUILD_A, self.c)
        self.assertEqual(states[self.ulfgar].condition, DEAD)  # not brought back without the fact
        (still,) = await self.claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        self.assertEqual(still.status, "proposed")

    async def test_a_stopped_speaker_cannot_be_recorded_through_an_answer(self) -> None:
        await self.story()
        await self.claim(said("Kesh", "carries", "Heartseeker"))
        (conflict,) = (await self.check()).conflicts
        with self.assertRaises(MemoryRuleError):
            await self.store.answer(
                GUILD_A, self.c, conflict.claim.id, CHANGED, opted_in={8}, levels=SMALL
            )
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, confirmed_only=True)), 1)


class Words(unittest.TestCase):
    def test_the_count_says_how_many_and_never_what(self) -> None:
        self.assertIn("1 heads-up ", ui.waiting_text(1))
        self.assertIn("3 heads-ups", ui.waiting_text(3))
        for text in (ui.waiting_text(1), ui.waiting_text(3)):
            self.assertNotIn("Ulfgar", text)


if __name__ == "__main__":
    unittest.main()


class Crossing(ConfirmedMemoryTest):
    async def test_crossing_the_line_is_logged_once(self) -> None:
        from dmbot.memory import continuity

        continuity._CROSSED.discard(self.c)
        a, b = (
            await self.add("Ulfgar", type="character"),
            await self.add("Cerric", type="character"),
        )
        await self.relate(a, "ally_of", b, status="confirmed", from_session_at=1)
        with self.assertLogs("dmbot.memory.continuity", level="INFO") as logged:
            await ContinuityStore(self.memory).check(GUILD_A, self.c, opted_in=AGREED, levels=SMALL)
            await ContinuityStore(self.memory).check(GUILD_A, self.c, opted_in=AGREED, levels=SMALL)
        self.assertEqual(len(logged.output), 1)
