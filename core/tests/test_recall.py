"""The recall service (#1350): what may be said about some names, to whom, at which story time,
with what evidence, in what size. Runs against Postgres (the memory store is its source)."""

from __future__ import annotations

import os
import statistics
import time
import unittest
from collections.abc import Sequence
from typing import Any
from unittest import mock

from dmbot.memory import recall
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import DEAD, Heard, MemoryRuleError
from dmbot.memory.recall import (
    DM_SCREEN,
    PLAYERS,
    TRANSCRIPT,
    AsOf,
    Item,
    Line,
    Quote,
    Recall,
    RecallService,
    check_citations,
)
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

AGREED = {7, 8}  # who is recorded now
S1, S2, S3 = 100, 200, 300  # sessions: when each started


def item(id_: str, score: float, text: str = "x" * 20, quotes: tuple[Quote, ...] = ()) -> Item:
    return Item(recall.FACT, id_, text, score, 1, False, quotes)


class Pure(unittest.TestCase):
    """The parts that need no database."""

    def test_cut_keeps_the_best_whole_items_in_a_stable_order(self) -> None:
        items = [item("b", 1.0), item("a", 1.0), item("c", 3.0), item("d", 0.1)]
        one = recall._cut(PLAYERS, AsOf(), (), items, 10_000)
        self.assertEqual([i.id for i in one.items], ["c", "a", "b", "d"])  # ties by id
        self.assertEqual(one.cut, 0)
        two = recall._cut(PLAYERS, AsOf(), (), list(reversed(items)), 10_000)
        self.assertEqual(one.items, two.items)  # the same question, the same answer

    def test_cut_stops_at_the_budget_and_says_how_many_it_left_out(self) -> None:
        items = [item(str(n), 10 - n) for n in range(6)]
        size = items[0].size
        got = recall._cut(PLAYERS, AsOf(), (), items, size * 3 + 1)
        self.assertEqual([i.id for i in got.items], ["0", "1", "2"])
        self.assertEqual((got.cut, got.size), (3, size * 3))

    def test_an_item_that_does_not_fit_with_its_quotes_is_kept_without_them(self) -> None:
        quote = Quote("L1", "q" * 100, S1, None)
        big = item("a", 2.0, quotes=(quote,))
        got = recall._cut(PLAYERS, AsOf(), (), [big], big.size - 1)
        (kept,) = got.items
        self.assertEqual(kept.quotes, ())
        self.assertEqual(got.cut, 0)

    def test_nothing_fits_a_budget_of_zero(self) -> None:
        got = recall._cut(PLAYERS, AsOf(), (), [item("a", 1.0)], 0)
        self.assertEqual((got.items, got.cut), ((), 1))

    def test_an_answer_may_cite_only_what_it_was_given(self) -> None:
        quote = Quote("L9", "hi", S1, None)
        given = Recall(PLAYERS, AsOf(), (item("f1", 1.0, quotes=(quote,)), item("f2", 0.5)))
        self.assertEqual(check_citations(["f1", "L9", "f2"], given), ())
        self.assertEqual(check_citations(["f1", "nope", "L8", "nope"], given), ("nope", "L8"))
        self.assertEqual(check_citations([], given), ())
        self.assertEqual(check_citations(["f1"], Recall(TRANSCRIPT, AsOf())), ("f1",))

    def test_recent_facts_score_higher_and_a_fact_with_no_time_is_middling(self) -> None:
        now = 100 * 86400
        self.assertGreater(recall._recency(now, now), recall._recency(now - 90 * 86400, now))
        self.assertEqual(recall._recency(None, now), 0.5)
        self.assertEqual(recall._recency(now, None), 0.5)
        self.assertEqual(recall._recency(now + 5, now), 1.0)  # not from the future

    def test_the_time_filter_has_the_values_it_needs(self) -> None:
        self.assertEqual(recall._true_at(AsOf()), ("to_session_at IS NULL", []))
        sql, values = recall._true_at(AsOf(session_at=S2, game_time=7))
        self.assertEqual(values, [S2, S2, 7, 7])
        self.assertIn("from_game_time", sql)

    def test_a_wrong_audience_is_refused(self) -> None:
        async def go() -> None:
            service = RecallService(mock.Mock())
            await service.recall(1, "c", seeds=[], audience="everyone")

        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(go())


class Fake:
    """A transcript reader for the tests: the lines it knows, by reference."""

    def __init__(self, *lines: Line) -> None:
        self.lines = {line.ref: line for line in lines}
        self.asked: list[Sequence[str]] = []

    async def __call__(self, refs: Sequence[str]) -> dict[str, Line]:
        self.asked.append(refs)
        return {r: self.lines[r] for r in refs if r in self.lines}


class Recalling(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.add("Mira", type="character", role="npc", description="Hides a vow")
        self.cult = await self.add("Ash Cult", type="faction")
        self.ulfgar = await self.add("Ulfgar", type="character", role="npc")
        self.temple = await self.add("Old Temple", type="place")
        self.cerric = await self.add("Cerric", type="character", role="npc")
        self.far = await self.add("Far Keep", type="place")
        # Mira is in the cult (S1); the cult is based at the temple (S1); Mira loves Ulfgar in
        # secret (S2); Ulfgar is allied with Cerric (S1, over by S3); Cerric holds the Far Keep.
        self.member = await self.fact(self.mira, "member_of", self.cult, from_session_at=S1)
        self.based = await self.fact(self.cult, "located_in", self.temple, from_session_at=S1)
        self.secret = await self.fact(
            self.mira, "ally_of", self.ulfgar, from_session_at=S2, secret=True
        )
        self.old_ally = await self.fact(
            self.ulfgar, "ally_of", self.cerric, from_session_at=S1, to_session_at=S3
        )
        self.holds = await self.fact(self.cerric, "located_in", self.far, from_session_at=S1)

    async def fact(self, a: str, pred: str, b: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")
        written = await self.relate(a, pred, b, **kw)
        return str(written.value[0].id)

    def service(self, lines: Fake | None = None) -> RecallService:
        return RecallService(self.memory, lines)

    async def recall(self, audience: str, *seeds: str, **kw: Any) -> Recall:
        kw.setdefault("opted_in", AGREED)
        return await self.service(kw.pop("lines", None)).recall(
            GUILD_A, kw.pop("campaign", self.c), seeds=list(seeds), audience=audience, **kw
        )

    def ids(self, got: Recall) -> set[str]:
        return {i.id for i in got.items}

    # ---- audiences -------------------------------------------------------------------

    async def test_the_dm_screen_sees_everything_including_secrets_and_notes(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.mira, DEAD, source="dm")
        got = await self.recall(DM_SCREEN, "Mira")
        self.assertIn(self.secret, self.ids(got))
        text = " | ".join(i.text for i in got.items)
        self.assertIn("Hides a vow", text)  # the DM's note on the entry
        self.assertIn("dead", text)
        secret = next(i for i in got.items if i.id == self.secret)
        self.assertTrue(secret.secret)

    async def test_players_see_no_secret_fact_no_note_and_no_condition(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.mira, DEAD, source="dm")
        got = await self.recall(PLAYERS, "Mira", hops=2)
        self.assertNotIn(self.secret, self.ids(got))
        self.assertIn(self.member, self.ids(got))
        text = " | ".join(i.text for i in got.items)
        self.assertNotIn("Hides a vow", text)
        self.assertNotIn("dead", text)
        self.assertNotIn("Ulfgar", text)  # known only through the secret fact
        self.assertFalse(any(i.secret for i in got.items))

    async def test_a_secret_name_means_nothing_to_players(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "The Hooded Stranger", kind="nickname", source="dm",
            secret=True,
        )  # fmt: skip
        players = await self.recall(PLAYERS, "The Hooded Stranger")
        self.assertEqual((players.items, players.seeds), ((), ()))
        dm = await self.recall(DM_SCREEN, "the hooded stranger")
        self.assertEqual(dm.seeds, (self.mira,))

    async def test_a_fact_from_the_story_plan_never_reaches_players(self) -> None:
        with mock.patch.object(recall, "PLAN_SOURCES", frozenset({"dm"})):
            players = await self.recall(PLAYERS, "Mira")
            dm = await self.recall(DM_SCREEN, "Mira")
        self.assertEqual([i for i in players.items if i.kind == recall.FACT], [])
        self.assertIn(self.member, self.ids(dm))

    async def test_the_transcript_audience_is_always_empty_and_reads_nothing(self) -> None:
        with mock.patch.object(self.memory, "_read", side_effect=AssertionError("read")):
            got = await self.recall(TRANSCRIPT, "Mira")
        self.assertEqual((got.items, got.ids, got.size), ((), frozenset(), 0))

    async def test_only_confirmed_facts_count(self) -> None:
        proposed = await self.fact(self.mira, "ally_of", self.cerric, status="proposed")
        got = await self.recall(DM_SCREEN, "Mira")
        self.assertNotIn(proposed, self.ids(got))

    # ---- story time ------------------------------------------------------------------

    async def test_a_fact_is_not_known_before_it_began_or_after_it_ended(self) -> None:
        early = await self.recall(DM_SCREEN, "Ulfgar", as_of=AsOf(session_at=S1 + 10))
        self.assertIn(self.old_ally, self.ids(early))
        self.assertNotIn(self.secret, self.ids(early))  # begins in S2
        later = await self.recall(DM_SCREEN, "Ulfgar", as_of=AsOf(session_at=S3 + 10))
        self.assertNotIn(self.old_ally, self.ids(later))  # ended at S3
        self.assertIn(self.secret, self.ids(later))

    async def test_with_no_time_given_only_what_still_holds_is_used(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar")
        self.assertNotIn(self.old_ally, self.ids(got))

    # ---- the walk --------------------------------------------------------------------

    async def test_one_step_reaches_the_facts_of_the_seed_only(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=1, as_of=AsOf(session_at=S2))
        facts = {i.id for i in got.items if i.kind == recall.FACT}
        self.assertEqual(facts, {self.secret, self.old_ally})

    async def test_two_steps_reach_the_neighbours_facts_and_no_further(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=2, as_of=AsOf(session_at=S2))
        facts = {i.id for i in got.items if i.kind == recall.FACT}
        # The cult is a group, so its own fact (based at the temple) comes with it at no step.
        self.assertEqual(facts, {self.secret, self.old_ally, self.member, self.holds, self.based})
        self.assertEqual(next(i for i in got.items if i.id == self.holds).hops, 2)

    async def test_a_group_holds_its_members_together_at_no_extra_step(self) -> None:
        friend = await self.add("Brann", type="character", role="npc")
        await self.fact(friend, "member_of", self.cult, from_session_at=S1)
        # Mira -> Ash Cult is one step; Ash Cult -> Brann costs nothing more.
        got = await self.recall(DM_SCREEN, "Mira", hops=1)
        names = " | ".join(i.text for i in got.items)
        self.assertIn("Brann", names)

    async def test_seeds_can_be_ids_or_names_and_unknown_ones_are_ignored(self) -> None:
        got = await self.recall(DM_SCREEN, "ULFGAR", self.mira, "Nobody Here", "f" * 32)
        self.assertEqual(got.seeds, (self.ulfgar, self.mira))
        self.assertEqual([i.id for i in got.items[:2]], [self.ulfgar, self.mira])  # seeds first

    async def test_the_more_a_name_is_heard_the_higher_its_facts_rank(self) -> None:
        await self.memory.add_session_heard(GUILD_A, self.c, S2, [Heard(self.cerric, 7, 40)])
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=1, as_of=AsOf(session_at=S2))
        facts = [i.id for i in got.items if i.kind == recall.FACT]
        self.assertEqual(facts[0], self.old_ally)  # Cerric is heard often
        # Counts from someone who is not recorded now do not count.
        quiet = await self.recall(
            DM_SCREEN, "Ulfgar", hops=1, as_of=AsOf(session_at=S2), opted_in={9}
        )
        scores = {i.id: i.score for i in quiet.items}
        self.assertLess(
            scores[self.old_ally], next(i.score for i in got.items if i.id == self.old_ally)
        )

    async def test_newer_facts_rank_above_older_ones(self) -> None:
        got = await self.recall(DM_SCREEN, "Mira", hops=1)
        scores = {i.id: i.score for i in got.items}
        self.assertGreater(scores[self.secret], scores[self.member])  # S2 over S1

    # ---- evidence --------------------------------------------------------------------

    async def evidence(self, *lines: Line) -> tuple[Fake, str]:
        """A fact with mentions of the given lines."""
        mentions = []
        for line in lines:
            written = await self.memory.add_mention(
                GUILD_A, self.c, self.mira, line_ref=line.ref, span=(0, 4), confidence=0.9,
                method="exact", source="cleaner", session_started_at=S1,
            )  # fmt: skip
            mentions.append(written.value)
        fact = await self.fact(
            self.mira, "ally_of", self.cerric, from_session_at=S2, mention_ids=mentions
        )
        return Fake(*lines), fact

    async def test_a_fact_carries_quotes_from_people_recorded_now(self) -> None:
        lines, fact = await self.evidence(
            Line("L1", "Mira is   with the cult", 7, S1, 1_000),
            Line("L2", "I never said this", 9, S1, 2_000),  # 9 is not recorded now
            Line("L3", "the DM asking DMbot", 7, S1, 3_000, sidebar=True),
        )
        got = await self.recall(PLAYERS, "Mira", lines=lines, hops=1)
        quotes = next(i for i in got.items if i.id == fact).quotes
        self.assertEqual([q.line_ref for q in quotes], ["L1"])
        self.assertEqual(quotes[0].text, "Mira is with the cult")  # spaces tidied
        self.assertEqual((quotes[0].session_started_at, quotes[0].at_ms), (S1, 1_000))
        self.assertIn("L1", got.ids)
        self.assertNotIn("L2", got.ids)
        self.assertNotIn("L3", got.ids)

    async def test_at_most_a_few_quotes_newest_first_and_each_is_short(self) -> None:
        many = [Line(f"L{n}", "w" * 400, 7, S1, n) for n in range(6)]
        lines, fact = await self.evidence(*many)
        got = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        quotes = next(i for i in got.items if i.id == fact).quotes
        self.assertEqual(len(quotes), recall.EVIDENCE_PER_FACT)
        self.assertEqual([q.line_ref for q in quotes], ["L5", "L4", "L3"])
        self.assertTrue(all(len(q.text) <= recall.QUOTE_CHARS for q in quotes))

    async def test_a_person_who_stops_being_recorded_leaves_the_next_recall(self) -> None:
        lines, fact = await self.evidence(Line("L1", "words", 7, S1, 1))
        before = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        after = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in={8})
        self.assertTrue(next(i for i in before.items if i.id == fact).quotes)
        self.assertEqual(next(i for i in after.items if i.id == fact).quotes, ())

    async def test_without_a_transcript_or_anyone_recorded_there_are_no_quotes(self) -> None:
        lines, fact = await self.evidence(Line("L1", "words", 7, S1, 1))
        none = await self.recall(DM_SCREEN, "Mira", hops=1)
        nobody = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in=())
        for got in (none, nobody):
            self.assertEqual(next(i for i in got.items if i.id == fact).quotes, ())
        self.assertEqual(lines.asked, [])  # not even asked

    async def test_the_lines_of_a_confirmed_claim_are_quoted_only_while_all_its_speakers_are(
        self,
    ) -> None:
        claims = ClaimStore(self.memory)
        await claims.propose(
            GUILD_A, self.c, [Extracted("Mira", "ally_of", "Cerric", "", ("C1", "C2"), "dm_said")],
            {"C1": 7, "C2": 8}, opted_in=AGREED, prompt_version="p", model="m",
            session_started_at=S2,
        )  # fmt: skip
        (waiting,) = await claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        done = await claims.confirm(GUILD_A, self.c, waiting.id, opted_in=AGREED)
        fact = done.claim.fact_id
        assert fact is not None
        lines = Fake(Line("C1", "she trusts him", 7, S2, 1), Line("C2", "since the fire", 8, S2, 2))
        both = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        self.assertEqual(
            {q.line_ref for q in next(i for i in both.items if i.id == fact).quotes}, {"C1", "C2"}
        )
        one = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in={7})
        self.assertEqual(next(i for i in one.items if i.id == fact).quotes, ())

    # ---- budget ----------------------------------------------------------------------

    async def test_a_small_budget_cuts_the_lowest_scores_and_says_so(self) -> None:
        full = await self.recall(DM_SCREEN, "Mira", hops=2)
        small = await self.recall(DM_SCREEN, "Mira", hops=2, budget=full.size // 2)
        self.assertLess(len(small.items), len(full.items))
        self.assertEqual(small.items, full.items[: len(small.items)])
        self.assertEqual(small.cut, len(full.items) - len(small.items))
        self.assertLessEqual(small.size, full.size // 2)

    async def test_the_same_question_gets_the_same_answer(self) -> None:
        one = await self.recall(DM_SCREEN, "Mira", hops=2)
        two = await self.recall(DM_SCREEN, "Mira", hops=2)
        self.assertEqual(one, two)

    async def test_an_answer_is_checked_against_what_it_was_given(self) -> None:
        got = await self.recall(PLAYERS, "Mira")
        self.assertEqual(check_citations([self.member], got), ())
        self.assertEqual(check_citations([self.secret], got), (self.secret,))

    # ---- isolation -------------------------------------------------------------------

    async def test_another_campaign_and_another_server_never_appear(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other Table", 99)).id
        mira2 = await self.add("Mira", campaign=other, type="character", role="npc")
        spy = await self.add("Spy", campaign=other, type="character", role="npc")
        leak = await self.memory.add_relation(
            GUILD_A, other, mira2, "ally_of", spy, source="dm", confidence=1.0, status="confirmed"
        )
        got = await self.recall(DM_SCREEN, "Mira", hops=2)
        self.assertNotIn(leak.value[0].id, self.ids(got))
        self.assertNotIn("Spy", " ".join(i.text for i in got.items))
        # By id, too: another campaign's entry id means nothing here.
        by_id = await self.recall(DM_SCREEN, mira2, hops=2)
        self.assertEqual((by_id.items, by_id.seeds), ((), ()))
        # And from the other campaign, nothing of this one.
        there = await self.recall(DM_SCREEN, "Mira", campaign=other, hops=2)
        self.assertIn("Spy", " ".join(i.text for i in there.items))
        self.assertNotIn("Ash Cult", " ".join(i.text for i in there.items))
        # Another server asking for this campaign's id gets nothing at all.
        with self.assertRaises(MemoryRuleError):
            await self.service().recall(GUILD_B, self.c, seeds=["Mira"], audience=DM_SCREEN)


def note(line: str) -> None:
    """Put the numbers on the CI run's summary page, where the pull request can quote them."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class Speed(MemoryTest):
    """One recall on a campaign of about 3,000 entries and 10,000 facts, under 50 ms."""

    ENTRIES, FACTS = 3000, 10000

    async def build(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                " description, status, source, created_at)"
                " SELECT %s, %s, md5('e' || n), CASE WHEN n %% 40 = 0 THEN 'faction'"
                " ELSE 'character' END, 'Name ' || n, '', 'confirmed', 'dm', 1"
                " FROM generate_series(0, %s - 1) AS n",
                (GUILD_A, self.c, self.ENTRIES),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at)"
                " SELECT %s, %s, md5('a' || n), md5('e' || n), 'Name ' || n, 'name ' || n,"
                " 'full', false, 'confirmed', '{}', 'dm', 1 FROM generate_series(0, %s - 1) AS n",
                (GUILD_A, self.c, self.ENTRIES),
            )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, from_session_at,"
                " secret, created_at)"
                " SELECT %s, %s, md5('r' || n || ':' || k), md5('e' || n),"
                " 'ally_of', md5('e' || ((n + k) %% %s)), '', 1.0, 'confirmed', 'dm', '{}',"
                " 1000 + n, (n %% 50 = 0), 1"
                " FROM generate_series(0, %s - 1) AS n, generate_series(1, 4) AS k"
                " WHERE n * 4 + k <= %s",
                (GUILD_A, self.c, self.ENTRIES, self.ENTRIES, self.FACTS),
            )
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) SELECT %s, %s, md5('e' || n), 1000, 7, 1 + n %% 9"
                " FROM generate_series(0, %s - 1) AS n",
                (GUILD_A, self.c, self.ENTRIES),
            )

    async def test_a_recall_on_a_big_campaign_is_fast(self) -> None:
        await self.build()
        service = RecallService(self.memory)
        seeds = ["Name 17", "Name 1200", "Name 2000"]

        async def once(audience: str) -> tuple[float, Recall]:
            started = time.perf_counter()
            got = await service.recall(
                GUILD_A, self.c, seeds=seeds, audience=audience, opted_in=AGREED
            )
            return (time.perf_counter() - started) * 1000, got

        await once(DM_SCREEN)  # the first call warms the connection and the plans
        for audience in (DM_SCREEN, PLAYERS):
            runs = [await once(audience) for _ in range(7)]
            median = statistics.median(ms for ms, _ in runs)
            got = runs[0][1]
            self.assertGreater(len(got.items), 20)  # it really found a neighbourhood
            summary = (
                f"recall {audience}: median {median:.1f} ms, worst {max(ms for ms, _ in runs):.1f}"
                f" ms over 7 runs; {self.ENTRIES} entries, {self.FACTS} facts,"
                f" {len(got.items)} items ({got.cut} cut)"
            )
            note(summary)
            self.assertLess(median, 50, summary)


if __name__ == "__main__":
    unittest.main()
