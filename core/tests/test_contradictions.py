"""Contradictions by point of view (#2660): two assertions that clash under the ontology, held by
the same point of view, whose story times and belief times overlap. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory import contradictions as cx
from dmbot.memory.checks import CONTRADICTION
from dmbot.memory.models import MemoryRuleError, new_id
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest


class Finding(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.finder = cx.Contradictions(self.memory)
        self.bram = await self.add("Bram")
        self.mira = await self.add("Mira")

    async def pair(
        self,
        a_from: int | None = None,
        a_to: int | None = None,
        b_from: int | None = None,
        b_to: int | None = None,
    ) -> tuple[str, str]:
        """An alliance and a feud between the same two names, both confirmed and public."""
        ally = await self.relate(
            self.bram, "ally_of", self.mira, status="confirmed",
            from_session_at=a_from, to_session_at=a_to,
        )  # fmt: skip
        enemy = await self.relate(
            self.bram, "enemy_of", self.mira, status="confirmed",
            from_session_at=b_from, to_session_at=b_to,
        )  # fmt: skip
        return ally.value[0].id, enemy.value[0].id

    async def view(self, kind: str) -> str:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s",
                (GUILD_A, self.c, kind),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def believe(
        self, view: str, relation: str, confidence: float, status: str = "theory",
        session_at: int | None = None,
    ) -> None:  # fmt: skip
        """A belief row as a restored backup brings it (the party's guesses and denials have no
        other door than a reading being placed, which is not what this tests)."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, session_at, evidence_ids, seq, recorded_at)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s, '{}', COALESCE(max(seq), 0) + 1, 1"
                " FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s",
                (GUILD_A, self.c, new_id(), view, relation, confidence, status, session_at,
                 GUILD_A, self.c),
            )  # fmt: skip

    async def anchor(self) -> None:
        """Give every belief the story's first session as its start, so the times in a test are
        the ones it sets (a belief made along with a connection has no session of its own)."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_beliefs SET session_at = 0 WHERE session_at IS NULL")

    async def guess(self, view: str, predicate: str) -> None:
        """A waiting guess connection between Bram and Mira and one character's guess on it (the
        proposed, secret connection a reading placed holds, made by the extractor)."""
        relation = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, '', 0.5, 'proposed', 'extractor', '{}', true, 1)",
                (GUILD_A, self.c, relation, self.bram, predicate, self.mira),
            )
        await self.believe(view, relation, 0.75, "accusation", session_at=0)

    async def character(self, name: str) -> str:
        """A point of view of one character (a perspective row, as placing makes one)."""
        entity = await self.add(name, type="character", role="player_character", played_by=9)
        made = new_id()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(  # (a player's character already has its view since #2995)
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s",
                (GUILD_A, self.c, entity),
            )
            have = await cur.fetchone()
            if have is not None:
                return str(have["id"])
            await conn.execute(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) VALUES (%s, %s, %s, 'pc', %s, 1)",
                (GUILD_A, self.c, made, entity),
            )
        return made

    async def test_the_same_point_of_view_holding_both_gives_one_candidate(self) -> None:
        ally, enemy = await self.pair()
        found = await self.finder.find(GUILD_A, self.c)
        truth = await self.view("truth")
        (candidate,) = [c for c in found if c.perspective_id == truth]
        self.assertEqual(candidate.kind, CONTRADICTION)
        self.assertEqual({candidate.first_relation_id, candidate.second_relation_id}, {ally, enemy})
        self.assertEqual(candidate.first_relation_id, min(ally, enemy))  # one pair, one candidate

    async def test_the_dms_view_against_the_party_is_not_a_candidate(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        await self.believe(truth, enemy, 0.0, "false_evidence")  # the DM's view denies the feud
        await self.believe(party, ally, 0.0, "false_evidence")  # the party never held the alliance
        await self.believe(party, enemy, 1.0, "revealed_fact")  # and believes the feud
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_two_characters_who_disagree_give_none(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        for view in (truth, party):  # leave nobody else holding both
            await self.believe(view, enemy, 0.0, "false_evidence")
        one, two = await self.character("Rook"), await self.character("Sable")
        await self.believe(one, ally, 0.75, "accusation")
        await self.believe(two, enemy, 0.75, "accusation")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_one_character_holding_both_is_a_candidate_of_their_view(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        for view in (truth, party):
            await self.believe(view, enemy, 0.0, "false_evidence")
        rook = await self.character("Rook")
        await self.believe(rook, ally, 0.75, "accusation")
        await self.believe(rook, enemy, 0.5, "theory")
        (candidate,) = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(candidate.perspective_id, rook)

    async def test_a_long_shot_asserts_nothing_so_it_cannot_contradict(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        for view in (truth, party):
            await self.believe(view, enemy, 0.0, "false_evidence")
        rook = await self.character("Rook")
        await self.believe(rook, ally, 0.75, "accusation")
        await self.believe(rook, enemy, 0.25, "theory")  # "unlikely"
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_story_times_that_do_not_overlap_give_none(self) -> None:
        await self.pair(a_from=1, a_to=5, b_from=10, b_to=20)  # allies, then (later) enemies
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_belief_times_that_do_not_overlap_give_none(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        await self.anchor()
        await self.believe(truth, enemy, 0.0, "false_evidence")
        await self.believe(party, enemy, 0.0, "false_evidence", session_at=0)  # (not yet)
        await self.believe(party, ally, 1.0, "revealed_fact", session_at=1)
        await self.believe(party, ally, 0.0, "false_evidence", session_at=5)  # changed its mind
        await self.believe(party, enemy, 1.0, "revealed_fact", session_at=6)
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_belief_times_that_do_overlap_give_one(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        await self.believe(truth, enemy, 0.0, "false_evidence")
        await self.believe(party, ally, 1.0, "revealed_fact", session_at=1)
        await self.believe(party, enemy, 1.0, "revealed_fact", session_at=3)  # while it still held
        (candidate,) = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(candidate.perspective_id, party)

    async def test_missing_game_time_is_flagged_and_known_game_time_is_not(self) -> None:
        await self.pair()
        truth = await self.view("truth")
        found = [c for c in await self.finder.find(GUILD_A, self.c) if c.perspective_id == truth]
        self.assertEqual([c.times_unknown for c in found], [True])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET from_game_time = 100 WHERE guild_id = %s"
                " AND campaign_id = %s",
                (GUILD_A, self.c),
            )
        found = [c for c in await self.finder.find(GUILD_A, self.c) if c.perspective_id == truth]
        self.assertEqual([c.times_unknown for c in found], [False])

    async def test_candidates_with_known_game_times_sort_before_the_unknown(self) -> None:
        one = cx.Candidate("p", CONTRADICTION, "a", "b", True)
        two = cx.Candidate("p", CONTRADICTION, "c", "d", False)
        sorted_ = sorted([one, two], key=lambda c: (c.times_unknown, c.kind, c.first_relation_id))
        self.assertEqual(sorted_, [two, one])

    async def test_a_rejected_or_waiting_connection_is_not_held(self) -> None:
        await self.relate(self.bram, "ally_of", self.mira, status="confirmed")
        await self.relate(self.bram, "enemy_of", self.mira, status="proposed")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_being_in_the_inn_and_the_town_it_is_in_is_one_place_not_two(self) -> None:
        town = await self.add("Harrowmere", type="place")
        inn = await self.add("The Gull", type="place")
        await self.relate(inn, "located_in", town, status="confirmed")
        await self.relate(self.bram, "located_in", inn, status="confirmed")
        await self.relate(self.bram, "located_in", town, status="confirmed")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_the_partys_copies_of_two_facts_the_dm_holds_together_are_no_candidate(
        self,
    ) -> None:
        one = await self.add("Harrowmere", type="place")
        two = await self.add("Stonebridge", type="place")
        await self.relate(self.bram, "located_in", one, status="confirmed")
        await self.relate(self.bram, "located_in", two, status="confirmed")
        # The truth holds both and so do the party's copies: the DM was warned when confirming
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_too_many_is_found_when_a_view_believes_what_the_truth_does_not_hold_together(
        self,
    ) -> None:
        one = await self.add("Harrowmere", type="place")
        two = await self.add("Stonebridge", type="place")
        await self.relate(self.bram, "located_in", one, status="confirmed")
        later = await self.relate(self.bram, "located_in", two, status="confirmed")
        truth, party = await self.view("truth"), await self.view("party")
        await self.anchor()
        await self.believe(truth, later.value[0].id, 0.0, "false_evidence", session_at=0)
        found = await self.finder.find(GUILD_A, self.c)
        self.assertEqual([(c.perspective_id, c.kind) for c in found], [(party, "too_many")])

    async def test_two_clashing_guesses_of_one_character_are_found_and_marked(self) -> None:
        rook = await self.character("Rook")
        for predicate in ("ally_of", "enemy_of"):
            await self.guess(rook, predicate)
        found = await self.finder.find(GUILD_A, self.c)
        (candidate,) = found
        self.assertEqual((candidate.perspective_id, candidate.kind), (rook, CONTRADICTION))
        self.assertTrue(candidate.involves_guess)

    async def test_a_guess_against_a_kept_fact_of_the_same_view_is_marked_too(self) -> None:
        ally, _ = await self.pair()
        rook = await self.character("Rook")
        await self.believe(rook, ally, 0.9, "revealed_fact", session_at=0)  # holds the kept one
        await self.guess(rook, "enemy_of")
        (candidate,) = [
            c for c in await self.finder.find(GUILD_A, self.c) if c.perspective_id == rook
        ]
        self.assertTrue(candidate.involves_guess)

    async def test_a_kept_pair_is_not_marked_as_a_guess(self) -> None:
        await self.pair()
        found = await self.finder.find(GUILD_A, self.c)
        self.assertTrue(found)
        self.assertFalse(any(c.involves_guess for c in found))

    async def test_a_one_of_a_kind_item_in_two_hands_is_found_for_a_belief_the_truth_denies(
        self,
    ) -> None:
        sword = await self.add("Heartseeker", type="item")
        await self.relate(self.bram, "carries", sword, status="confirmed")
        theirs = await self.relate(self.mira, "carries", sword, status="confirmed")
        truth, party = await self.view("truth"), await self.view("party")
        await self.anchor()
        await self.believe(truth, theirs.value[0].id, 0.0, "false_evidence", session_at=0)
        found = await self.finder.find(GUILD_A, self.c)
        self.assertEqual([(c.perspective_id, c.kind) for c in found], [(party, "held_twice")])

    async def test_a_one_of_a_kind_item_in_two_hands_the_truth_holds_is_no_candidate(self) -> None:
        sword = await self.add("Heartseeker", type="item")
        await self.relate(self.bram, "carries", sword, status="confirmed")
        await self.relate(self.mira, "carries", sword, status="confirmed")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_a_pair_is_reported_once_however_it_is_met(self) -> None:
        sword = await self.add("Heartseeker", type="item")
        await self.relate(self.bram, "carries", sword, status="confirmed")
        theirs = await self.relate(self.mira, "carries", sword, status="confirmed")
        await self.anchor()
        await self.believe(await self.view("truth"), theirs.value[0].id, 0.0, "false_evidence", 0)
        found = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(
            len(found), len({(c.first_relation_id, c.second_relation_id) for c in found})
        )
        self.assertEqual(len(found), 1)  # (each of the two beliefs raises it; it is one pair)

    async def test_a_middle_belief_of_less_than_even_odds_splits_the_history(self) -> None:
        ally, enemy = await self.pair()
        truth, party = await self.view("truth"), await self.view("party")
        await self.anchor()
        await self.believe(truth, enemy, 0.0, "false_evidence", session_at=0)
        await self.believe(party, enemy, 0.0, "false_evidence", session_at=0)
        await self.believe(party, enemy, 0.9, "revealed_fact", session_at=1)
        await self.believe(party, enemy, 0.3, "theory", session_at=4)  # unsure again at 4
        await self.believe(party, ally, 0.0, "false_evidence", session_at=0)  # not yet allies
        await self.believe(party, ally, 1.0, "revealed_fact", session_at=6)  # after it let go
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_another_campaign_never_shows(self) -> None:
        await self.pair()
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        a = await self.add("Bram", campaign=elsewhere)
        b = await self.add("Mira", campaign=elsewhere)
        for predicate in ("ally_of", "enemy_of"):
            await self.memory.add_relation(
                GUILD_A, elsewhere, a, predicate, b, source="dm", confidence=1.0,
                status="confirmed",
            )  # fmt: skip
        here = await self.finder.find(GUILD_A, self.c)
        there = await self.finder.find(GUILD_A, elsewhere)
        self.assertEqual((len(here), len(there)), (2, 2))  # (the DM's view and the party's)
        ids = {c.first_relation_id for c in here} | {c.second_relation_id for c in here}
        self.assertTrue(ids.isdisjoint({c.first_relation_id for c in there}))
        with self.assertRaises(MemoryRuleError):  # not another server's campaign
            await self.finder.find(GUILD_B, self.c)


class Pure(unittest.TestCase):
    def test_a_belief_history_is_cut_into_spans_by_session(self) -> None:
        from dmbot.memory.models import Belief

        def belief(seq: int, at: int | None) -> Belief:
            return Belief("i", "p", "r", 1.0, "theory", at, (), seq, 0)

        got = cx.spans([belief(2, 5), belief(1, 1), belief(3, None)])
        # (the second was replaced by one with no session: its end cannot be placed)
        self.assertEqual([(b.seq, st, en) for b, st, en in got], [(1, 1, 5), (3, None, None)])

    def test_a_belief_formed_and_changed_in_the_same_session_was_never_held(self) -> None:
        from dmbot.memory.models import Belief

        def belief(seq: int, at: int | None) -> Belief:
            return Belief("i", "p", "r", 1.0, "theory", at, (), seq, 0)

        got = cx.spans([belief(1, 3), belief(2, 3), belief(3, 6)])
        self.assertEqual([(b.seq, st, en) for b, st, en in got], [(2, 3, 6), (3, 6, None)])
        inverted = cx.spans([belief(1, 9), belief(2, 4)])  # recorded out of order
        self.assertEqual([(b.seq, st, en) for b, st, en in inverted], [(2, 4, None)])

    def test_a_denial_and_a_long_shot_assert_nothing(self) -> None:
        from dmbot.memory.models import Belief

        def belief(c: float, status: str) -> Belief:
            return Belief("i", "p", "r", c, status, None, (), 1, 0)

        self.assertFalse(cx.asserts(belief(0.0, "false_evidence")))
        self.assertFalse(cx.asserts(belief(0.4, "theory")))
        self.assertTrue(cx.asserts(belief(0.5, "theory")))
        self.assertTrue(cx.asserts(belief(1.0, "revealed_fact")))


if __name__ == "__main__":
    unittest.main()
