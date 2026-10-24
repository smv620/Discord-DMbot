"""The recall service (#1350): what may be said about some names, to whom, at which story time,
with what evidence, in what size. Runs against Postgres (the memory store is its source)."""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import unittest
from collections.abc import Collection, Sequence
from typing import Any
from unittest import mock

from dmbot.memory import beliefs, recall
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
from dmbot.memory.store import _load_ontology
from tests import speed
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

AGREED = {7, 8}  # who is recorded now
MONTH = 30 * 86400
S1 = 1_600_000_000  # sessions: when each started, a month apart
S2, S3 = S1 + MONTH, S1 + 2 * MONTH


def item(id_: str, score: float, text: str = "x" * 20, quotes: tuple[Quote, ...] = ()) -> Item:
    return Item(recall.FACT, id_, text, score, 1, False, quotes)


def row(fid: str, a: str, b: str, at: int | None = S1) -> dict[str, Any]:
    return {
        "id": fid, "subject_id": a, "predicate": "ally_of", "object_id": b, "detail": "",
        "from_session_at": at, "secret": False, "mention_ids": [],
    }  # fmt: skip


def walk_of(*entities: tuple[str, str], seeds: Sequence[str] = (), cost: int = 0) -> Any:
    walk = recall._Walk()
    walk.entities = {name: {"type": kind} for name, kind in entities}
    walk.seeds = list(seeds)
    walk.cost = {s: cost for s in seeds}
    return walk


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

    def test_one_item_too_big_for_the_budget_does_not_empty_the_recall(self) -> None:
        huge = item("huge", 99.0, text="h" * 5000)
        small = [item("a", 2.0), item("b", 1.0)]
        got = recall._cut(DM_SCREEN, AsOf(), (), [huge, *small], 4000)
        self.assertEqual([i.id for i in got.items], ["a", "b"])
        self.assertEqual(got.cut, 1)

    def test_quotes_fill_the_room_left_and_never_take_an_items_place(self) -> None:
        q = [Quote(f"L{n}", "q" * 30, S1, n) for n in range(3)]  # 50 characters each
        items = [item("a", 3.0), item("b", 2.0), item("c", 1.0)]
        filled, used = recall._fill(items, {"a": tuple(q), "b": tuple(q)}, 175)
        self.assertEqual([len(i.quotes) for i in filled], [3, 0, 0])  # a took 150; b's 50 won't fit
        self.assertEqual(used, 150)
        self.assertEqual([i.id for i in filled], ["a", "b", "c"])  # every item still there
        none, zero = recall._fill(items, {"a": tuple(q)}, 0)
        self.assertEqual((none, zero), (tuple(items), 0))

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
        # A game time alone still leaves out what has ended by session.
        self.assertIn("to_session_at IS NULL", recall._true_at(AsOf(game_time=7))[0])

    def test_a_wrong_audience_is_refused(self) -> None:
        async def go() -> None:
            await RecallService(mock.Mock()).recall(1, "c", seeds=[], audience="everyone")

        with self.assertRaises(ValueError):
            asyncio.run(go())

    def test_a_line_must_say_whether_it_is_a_sidebar_line(self) -> None:
        with self.assertRaises(TypeError):
            Line(ref="L1", text="x", speaker_id=7)  # type: ignore[call-arg]

    def test_a_round_gives_the_same_walk_whatever_order_the_rows_came_in(self) -> None:
        rows = [row("f1", "A", "B"), row("f2", "B", "C"), row("f3", "A", "C")]
        results = []
        for ordering in (rows, list(reversed(rows)), [rows[1], rows[2], rows[0]]):
            walk = walk_of(("A", "character"), ("B", "character"), ("C", "character"))
            walk.cost = {"A": 2, "B": 1}  # A is two steps out, B one
            nxt = recall.RecallService._step(walk, ordering, {"A", "B"}, 2)
            results.append(
                ({k: (f.hops, f.near, f.far) for k, f in sorted(walk.facts.items())},
                 dict(walk.cost), nxt)
            )  # fmt: skip
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(results[0][0]["f1"], (2, "B", "A"))  # reached through B, not through A

    def test_an_entry_at_the_last_step_is_not_expanded_unless_it_is_a_group(self) -> None:
        for kind, expanded in (("character", set()), ("faction", {"G"})):
            walk = walk_of(("A", "character"), ("G", kind), seeds=["A"])
            nxt = recall.RecallService._step(walk, [row("f1", "A", "G")], {"A"}, 1)
            self.assertEqual(nxt, expanded, kind)
            self.assertEqual(walk.cost["G"], 1)

    def test_a_group_brings_its_members_at_no_extra_step(self) -> None:
        walk = walk_of(("A", "character"), ("G", "faction"), ("M", "character"), seeds=["A"])
        nxt = recall.RecallService._step(walk, [row("f1", "A", "G")], {"A"}, 1)
        recall.RecallService._step(walk, [row("f2", "M", "G")], nxt, 1)
        self.assertEqual(walk.facts["f2"].hops, 1)  # G is one step out; its member costs no more
        self.assertEqual(walk.cost["M"], 1)

    def test_a_fact_with_an_end_that_is_not_a_live_entry_is_dropped(self) -> None:
        walk = walk_of(("A", "character"), seeds=["A"])
        recall.RecallService._step(walk, [row("f1", "A", "ghost")], {"A"}, 2)
        self.assertEqual(walk.facts, {})


class Fake:
    """A transcript reader for the tests: the lines it knows, by campaign and reference."""

    def __init__(self, campaign: str, *lines: Line, others: dict[str, Line] | None = None) -> None:
        self.campaign = campaign
        self.lines = {line.ref: line for line in lines}
        self.others = others or {}
        self.asked: list[tuple[int, str, Sequence[str]]] = []

    async def __call__(
        self, guild_id: int, campaign_id: str, refs: Sequence[str]
    ) -> dict[str, Line]:
        self.asked.append((guild_id, campaign_id, refs))
        known = self.lines if campaign_id == self.campaign else self.others
        return {r: known[r] for r in refs if r in known}


def said(ref: str, text: str, speaker: int, at: int = 1, **kw: Any) -> Line:
    kw.setdefault("sidebar", False)
    return Line(ref=ref, text=text, speaker_id=speaker, session_started_at=S1, at_ms=at, **kw)


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

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")  # the players only hear of what the DM confirmed
        return await super().add(name, **kw)

    async def fact(self, a: str, pred: str, b: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")
        written = await self.relate(a, pred, b, **kw)
        return str(written.value[0].id)

    def service(self, lines: Any = None) -> RecallService:
        return RecallService(self.memory, lines)

    async def recall(self, audience: str, *seeds: str, **kw: Any) -> Recall:
        kw.setdefault("opted_in", AGREED)
        return await self.service(kw.pop("lines", None)).recall(
            GUILD_A, kw.pop("campaign", self.c), seeds=list(seeds), audience=audience, **kw
        )

    def ids(self, got: Recall) -> set[str]:
        return {i.id for i in got.items}

    def texts(self, got: Recall) -> str:
        return " | ".join(i.text for i in got.items)

    # ---- audiences -------------------------------------------------------------------

    async def test_the_dm_screen_sees_everything_including_secrets_and_notes(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.mira, DEAD, source="dm")
        got = await self.recall(DM_SCREEN, "Mira")
        self.assertIn(self.secret, self.ids(got))
        self.assertIn("Hides a vow", self.texts(got))  # the DM's note on the entry
        self.assertIn("dead", self.texts(got))
        self.assertTrue(next(i for i in got.items if i.id == self.secret).secret)

    async def test_players_read_what_the_party_believes_as_the_flag_flips_both_ways(self) -> None:
        """#2409: the players' recall reads The party's beliefs; they must give exactly what the
        secret flag gave, before and after a reveal and a hide, and for a reworded connection."""
        before = await self.recall(PLAYERS, "Mira", hops=2)
        self.assertNotIn(self.secret, self.ids(before))
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=False)
        shown = await self.recall(PLAYERS, "Mira", hops=2)
        self.assertIn(self.secret, self.ids(shown))
        self.assertIn("Ulfgar", self.texts(shown))
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=True)
        hidden = await self.recall(PLAYERS, "Mira", hops=2)
        self.assertEqual(self.ids(hidden), self.ids(before))
        self.assertNotIn("Ulfgar", self.texts(hidden))
        # a hidden connection reworded stays hidden; a shown one stays shown
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, self.secret, "enemy_of", "", source="dm"
        )
        self.assertNotIn(moved.value.id, self.ids(await self.recall(PLAYERS, "Mira", hops=2)))
        again = await self.memory.change_relation_words(
            GUILD_A, self.c, self.member, "ally_of", "", source="dm"
        )
        self.assertIn(again.value.id, self.ids(await self.recall(PLAYERS, "Mira", hops=2)))
        # the DM still sees the secret one
        self.assertIn(moved.value.id, self.ids(await self.recall(DM_SCREEN, "Mira", hops=2)))

    async def roster_ties(self) -> dict[str, tuple[str, ...]]:
        return {e.name: e.ties for e in await self.service().roster(GUILD_A, self.c)}

    async def test_the_roster_reads_what_the_party_believes_as_the_flag_flips_and_on_reword(
        self,
    ) -> None:
        """#2409: the NPC list and pages tie Mira and Ulfgar only while the players know it."""
        self.assertEqual((await self.roster_ties()).get("Mira", ()), ())
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=False)
        self.assertEqual((await self.roster_ties())["Mira"], ("Ulfgar",))
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=True)
        self.assertEqual((await self.roster_ties()).get("Mira", ()), ())
        hidden = await self.memory.change_relation_words(  # a hidden one reworded stays hidden
            GUILD_A, self.c, self.secret, "enemy_of", "", source="dm"
        )
        self.assertEqual((await self.roster_ties()).get("Mira", ()), ())
        await self.memory.update_relation(
            GUILD_A, self.c, hidden.value.id, source="dm", secret=False
        )
        await self.memory.change_relation_words(  # a shown one reworded stays shown
            GUILD_A, self.c, hidden.value.id, "kin_of", "", source="dm"
        )
        self.assertEqual((await self.roster_ties())["Mira"], ("Ulfgar",))

    async def test_a_family_role_reads_its_own_way_on_each_roster_entry_and_in_recall(self) -> None:
        """#2457: "is the niece of Ulfgar" on the one, "has a niece: Mira" on the other, whichever
        name sorts first; with no role both just name each other."""
        names = {e.name: e.id for e in await self.memory.entities(GUILD_A, self.c)}
        for niece, uncle in (("Mira", "Ulfgar"), ("Ulfgar", "Mira")):  # both id orders
            made = await self.memory.add_relation(
                GUILD_A, self.c, names[niece], "kin_of", names[uncle], source="dm",
                confidence=1.0, status="confirmed", detail="niece", directional=True,
            )  # fmt: skip
            link = made.value[0]
            self.assertEqual((link.subject_id, link.object_id), (names[niece], names[uncle]))
            ties = await self.roster_ties()
            self.assertIn(f"is the niece of {uncle}", ties[niece])
            self.assertIn(f"has a niece: {niece}", ties[uncle])
            text = self.texts(await self.recall(PLAYERS, niece, hops=1))
            self.assertIn(f"{niece} is the niece of {uncle}", text)
            await self.memory.update_relation(
                GUILD_A, self.c, link.id, source="dm", status="rejected"
            )

    async def lookup_ids(self) -> tuple[set[str], set[str]]:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        return {r.id for r in data.relations}, {r.id for r in data.secret_relations}

    async def test_the_lookup_reads_what_the_party_believes_as_the_flag_flips_and_on_reword(
        self,
    ) -> None:
        shown, secret = await self.lookup_ids()
        self.assertNotIn(self.secret, shown)
        self.assertIn(self.secret, secret)
        self.assertIn(self.member, shown)
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=False)
        shown, secret = await self.lookup_ids()
        self.assertIn(self.secret, shown)
        self.assertNotIn(self.secret, secret)
        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=True)
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, self.secret, "enemy_of", "", source="dm"
        )
        again = await self.memory.change_relation_words(
            GUILD_A, self.c, self.member, "ally_of", "", source="dm"
        )
        shown, secret = await self.lookup_ids()
        self.assertNotIn(moved.value.id, shown)
        self.assertIn(moved.value.id, secret)
        self.assertIn(again.value.id, shown)

    async def test_the_two_party_readers_give_the_same_set(self) -> None:
        """`party_knows_by` (recall and the roster, a probe by The party's id) and `party_knows`
        (the lookup, a join) are two spellings of one question."""
        from dmbot.memory.beliefs import party_id, party_knows, party_knows_by

        await self.memory.update_relation(GUILD_A, self.c, self.secret, source="dm", secret=False)
        await self.memory.update_relation(GUILD_A, self.c, self.member, source="dm", secret=True)
        async with self.memory._read(GUILD_A, self.c) as scope:
            party = await party_id(scope)
            ids = []
            for clause in (party_knows(), party_knows_by("memory_relations", party)):
                cur = await scope.conn.execute(
                    "SELECT id FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                    f" AND {clause} ORDER BY id",
                    scope.ids,
                )
                ids.append([r["id"] for r in await cur.fetchall()])
        self.assertEqual(ids[0], ids[1])
        self.assertIn(self.secret, ids[0])
        self.assertNotIn(self.member, ids[0])

    async def test_players_see_no_secret_fact_no_note_and_no_condition(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.mira, DEAD, source="dm")
        got = await self.recall(PLAYERS, "Mira", hops=2)
        self.assertNotIn(self.secret, self.ids(got))
        self.assertIn(self.member, self.ids(got))
        self.assertNotIn("Hides a vow", self.texts(got))
        self.assertNotIn("dead", self.texts(got))
        self.assertNotIn("Ulfgar", self.texts(got))  # known only through the secret fact
        self.assertFalse(any(i.secret for i in got.items))

    async def test_a_secret_name_means_nothing_to_players(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "The Hooded Stranger", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        players = await self.recall(PLAYERS, "The Hooded Stranger")
        self.assertEqual((players.items, players.seeds), ((), ()))
        dm = await self.recall(DM_SCREEN, "the hooded stranger")
        self.assertEqual(dm.seeds, (self.mira,))

    async def test_an_entry_whose_own_name_is_secret_is_hidden_from_players(self) -> None:
        """The DM makes the entry's real name secret: its identity is the secret, so players get
        neither the entry nor its facts, by name, by id or through a neighbour."""
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Mira", kind="full", source="dm", status="confirmed",
            secret=True,
        )  # fmt: skip
        by_name = await self.recall(PLAYERS, "Mira")
        by_id = await self.recall(PLAYERS, self.mira)
        near = await self.recall(PLAYERS, "Ash Cult", hops=2)
        for got in (by_name, by_id, near):
            self.assertNotIn("Mira", self.texts(got))
            self.assertNotIn(self.member, self.ids(got))
        self.assertIn("Mira", self.texts(await self.recall(DM_SCREEN, "Ash Cult", hops=2)))

    async def test_players_only_hear_of_what_the_dm_confirmed(self) -> None:
        guess = await self.add("Stray Wolf", type="character", role="npc", status="proposed")
        players = await self.recall(PLAYERS, "Stray Wolf")
        self.assertEqual((players.items, players.seeds), ((), ()))
        self.assertEqual((await self.recall(PLAYERS, guess)).seeds, ())
        self.assertEqual((await self.recall(DM_SCREEN, "Stray Wolf")).seeds, (guess,))

    async def test_confirmed_only_leaves_a_proposed_entry_out_of_the_dm_screen_view(self) -> None:
        # (#2254) A recap rests on what the DM confirmed; the DM screen's usual view shows more.
        guess = await self.add("Stray Wolf", type="character", role="npc", status="proposed")
        self.assertEqual((await self.recall(DM_SCREEN, "Stray Wolf")).seeds, (guess,))
        only = await self.recall(DM_SCREEN, "Stray Wolf", confirmed_only=True)
        self.assertEqual((only.items, only.seeds), ((), ()))
        self.assertEqual((await self.recall(DM_SCREEN, guess, confirmed_only=True)).seeds, ())
        # still the DM screen's view otherwise: a secret is there
        mira = await self.recall(DM_SCREEN, "Mira", confirmed_only=True)
        self.assertEqual(mira.seeds, (self.mira,))

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
        at_end = await self.recall(DM_SCREEN, "Ulfgar", as_of=AsOf(session_at=S3))
        self.assertNotIn(self.old_ally, self.ids(at_end))  # it ends AT S3: not true then

    async def test_with_no_time_given_only_what_still_holds_is_used(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar")
        self.assertNotIn(self.old_ally, self.ids(got))
        only_game_time = await self.recall(DM_SCREEN, "Ulfgar", as_of=AsOf(game_time=5))
        self.assertNotIn(self.old_ally, self.ids(only_game_time))

    async def test_a_condition_is_the_one_it_was_at_that_time(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=S2
        )
        now = await self.recall(DM_SCREEN, "Ulfgar")
        before = await self.recall(DM_SCREEN, "Ulfgar", as_of=AsOf(session_at=S1 + 10))
        self.assertIn("dead", self.texts(now))
        self.assertNotIn("dead", self.texts(before))

    # ---- the walk --------------------------------------------------------------------

    async def test_one_step_reaches_the_facts_of_the_seed_only(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=1, as_of=AsOf(session_at=S2))
        facts = {i.id for i in got.items if i.kind == recall.FACT}
        self.assertEqual(facts, {self.secret, self.old_ally})

    async def test_two_steps_reach_the_neighbours_facts_and_a_groups_own_facts(self) -> None:
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=2, as_of=AsOf(session_at=S2))
        facts = {i.id for i in got.items if i.kind == recall.FACT}
        # The cult is a group, so its own fact (based at the temple) comes with it at no step.
        self.assertEqual(facts, {self.secret, self.old_ally, self.member, self.holds, self.based})
        self.assertEqual(next(i for i in got.items if i.id == self.holds).hops, 2)

    async def test_a_group_holds_its_members_together_at_no_extra_step(self) -> None:
        friend = await self.add("Brann", type="character", role="npc")
        await self.fact(friend, "member_of", self.cult, from_session_at=S1)
        got = await self.recall(DM_SCREEN, "Mira", hops=1)
        self.assertIn("Brann", self.texts(got))

    async def test_seeds_can_be_ids_or_names_and_unknown_ones_are_ignored(self) -> None:
        got = await self.recall(DM_SCREEN, "ULFGAR", self.mira, "Ulfgar", "Nobody Here", "f" * 32)
        self.assertEqual(got.seeds, (self.ulfgar, self.mira))  # each once, in the order given
        self.assertEqual([i.id for i in got.items[:2]], [self.ulfgar, self.mira])  # seeds first

    async def test_the_more_a_name_is_heard_the_higher_its_facts_rank(self) -> None:
        await self.memory.add_session_heard(GUILD_A, self.c, S2, [Heard(self.cerric, 7, 40)])
        when = AsOf(session_at=S2)
        got = await self.recall(DM_SCREEN, "Ulfgar", hops=1, as_of=when)
        facts = [i.id for i in got.items if i.kind == recall.FACT]
        self.assertEqual(facts[0], self.old_ally)  # Cerric is heard often
        # What someone who is not recorded now said does not count.
        quiet = await self.recall(DM_SCREEN, "Ulfgar", hops=1, as_of=when, opted_in={9})
        before = next(i.score for i in got.items if i.id == self.old_ally)
        self.assertLess(next(i.score for i in quiet.items if i.id == self.old_ally), before)

    async def test_newer_facts_rank_above_older_ones(self) -> None:
        got = await self.recall(DM_SCREEN, "Mira", hops=1)
        scores = {i.id: i.score for i in got.items}
        self.assertGreater(scores[self.secret], scores[self.member])  # S2 over S1

    async def test_a_hub_is_capped_and_says_so(self) -> None:
        for n in range(12):
            other = await self.add(f"Hanger-on {n}", type="character", role="npc")
            await self.fact(self.mira, "knows", other, from_session_at=S1 + n)
        with mock.patch.object(recall, "MAX_FACTS_ROUND", 5):
            got = await self.recall(DM_SCREEN, "Mira", hops=1, budget=50_000)
        self.assertTrue(got.truncated)
        self.assertEqual(len([i for i in got.items if i.kind == recall.FACT]), 5)
        whole = await self.recall(DM_SCREEN, "Mira", hops=1, budget=50_000)
        self.assertFalse(whole.truncated)

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
        return Fake(self.c, *lines), fact

    def quotes(self, got: Recall, fact: str) -> tuple[Quote, ...]:
        return next(i for i in got.items if i.id == fact).quotes

    async def test_a_fact_carries_quotes_from_people_recorded_now(self) -> None:
        lines, fact = await self.evidence(
            said("L1", "Mira is   with the cult", 7, 1_000),
            said("L2", "I never said this", 9, 2_000),  # 9 is not recorded now
            said("L3", "the DM asking DMbot", 7, 3_000, sidebar=True),
        )
        got = await self.recall(PLAYERS, "Mira", lines=lines, hops=1)
        quotes = self.quotes(got, fact)
        self.assertEqual([q.line_ref for q in quotes], ["L1"])
        self.assertEqual(quotes[0].text, "Mira is with the cult")  # spaces tidied
        self.assertEqual((quotes[0].session_started_at, quotes[0].at_ms), (S1, 1_000))
        self.assertIn("L1", got.ids)
        self.assertNotIn("L2", got.ids)
        self.assertNotIn("L3", got.ids)

    async def test_at_most_a_few_quotes_newest_first_and_each_is_short(self) -> None:
        many = [said(f"L{n}", "w" * 400, 7, n) for n in range(6)]
        lines, fact = await self.evidence(*many)
        quotes = self.quotes(await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1), fact)
        self.assertEqual([q.line_ref for q in quotes], ["L5", "L4", "L3"])
        self.assertTrue(all(len(q.text) <= recall.QUOTE_CHARS for q in quotes))

    async def test_a_person_who_stops_being_recorded_leaves_the_next_recall(self) -> None:
        lines, fact = await self.evidence(said("L1", "words", 7))
        before = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        after = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in={8})
        self.assertTrue(self.quotes(before, fact))
        self.assertEqual(self.quotes(after, fact), ())

    async def test_someone_who_stops_while_the_transcript_is_read_is_not_quoted(self) -> None:
        lines, fact = await self.evidence(said("L1", "words", 7))
        answers: list[Collection[int]] = [{7, 8}, {8}]  # asked at the start, then again after

        async def who() -> Collection[int]:
            return answers.pop(0)

        got = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in=who)
        self.assertEqual(self.quotes(got, fact), ())
        self.assertEqual(answers, [])  # it really was asked twice

    async def test_without_a_transcript_or_anyone_recorded_there_are_no_quotes(self) -> None:
        lines, fact = await self.evidence(said("L1", "words", 7))
        none = await self.recall(DM_SCREEN, "Mira", hops=1)
        nobody = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in=())
        for got in (none, nobody):
            self.assertEqual(self.quotes(got, fact), ())
        self.assertEqual(lines.asked, [])  # not even asked

    async def test_a_reader_that_fails_or_hangs_costs_only_the_quotes(self) -> None:
        _, fact = await self.evidence(said("L1", "words", 7))

        async def broken(guild: int, campaign: str, refs: Sequence[str]) -> dict[str, Line]:
            raise OSError("transcript down")

        async def hung(guild: int, campaign: str, refs: Sequence[str]) -> dict[str, Line]:
            await asyncio.sleep(60)
            return {}

        for reader in (broken, hung):
            with mock.patch.object(recall, "LINES_TIMEOUT_S", 0.05):
                got = await self.recall(DM_SCREEN, "Mira", lines=reader, hops=1)
            self.assertIn(fact, self.ids(got))
            self.assertEqual(self.quotes(got, fact), ())

    async def test_the_reader_is_told_which_campaign_so_the_same_ref_never_crosses(self) -> None:
        lines, fact = await self.evidence(said("L1", "this campaign", 7))
        lines.others = {"L1": said("L1", "the OTHER campaign", 7)}
        got = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        self.assertEqual([q.text for q in self.quotes(got, fact)], ["this campaign"])
        self.assertEqual(lines.asked[0][:2], (GUILD_A, self.c))

    async def test_the_lines_of_a_confirmed_claim_are_quoted_only_while_all_its_speakers_are(
        self,
    ) -> None:
        claims = ClaimStore(self.memory)
        await claims.propose(
            GUILD_A,
            self.c,
            [Extracted("Mira", "ally_of", "Cerric", "", ("C1", "C2"), "dm_said")],
            {"C1": 7, "C2": 8},
            opted_in=AGREED,
            prompt_version="p",
            model="m",
            session_started_at=S2,
            dms={7, 8},  # both speak for the NPC here: an NPC's attitude is kept only from a DM
        )  # fmt: skip
        (waiting,) = await claims.waiting(GUILD_A, self.c, opted_in=AGREED)
        done = await claims.confirm(GUILD_A, self.c, waiting.id, opted_in=AGREED)
        fact = done.claim.fact_id
        assert fact is not None
        lines = Fake(self.c, said("C1", "she trusts him", 7, 1), said("C2", "since the fire", 8, 2))
        both = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1)
        self.assertEqual({q.line_ref for q in self.quotes(both, fact)}, {"C1", "C2"})
        one = await self.recall(DM_SCREEN, "Mira", lines=lines, hops=1, opted_in={7})
        self.assertEqual(self.quotes(one, fact), ())

    # ---- budget ----------------------------------------------------------------------

    async def test_a_small_budget_cuts_the_lowest_scores_and_says_so(self) -> None:
        full = await self.recall(DM_SCREEN, "Mira", hops=2)
        small = await self.recall(DM_SCREEN, "Mira", hops=2, budget=full.size // 2)
        self.assertLess(len(small.items), len(full.items))
        self.assertLessEqual(small.size, full.size // 2)
        self.assertEqual(small.cut, len(full.items) - len(small.items))
        self.assertTrue({i.id for i in small.items} <= {i.id for i in full.items})
        self.assertEqual(full.items[0].id, small.items[0].id)  # the best is always kept

    async def test_a_quarter_of_the_budget_is_kept_for_evidence(self) -> None:
        lines, fact = await self.evidence(said("L1", "words", 7))
        full = await self.recall(DM_SCREEN, "Mira", hops=2, lines=lines)
        # A budget just big enough for every item bare: evidence still gets its share, so some
        # lower item is cut rather than the evidence.
        bare = (await self.recall(DM_SCREEN, "Mira", hops=2)).size
        tight = await self.recall(DM_SCREEN, "Mira", hops=2, lines=lines, budget=bare + 20)
        self.assertTrue(self.quotes(full, fact))
        self.assertTrue(self.quotes(tight, fact))
        self.assertLessEqual(tight.size, bare + 20)

    async def test_the_terms_are_read_once_until_the_memory_changes(self) -> None:
        service = self.service()
        with mock.patch("dmbot.memory.recall._load_ontology", wraps=_load_ontology) as load:
            for _ in range(3):
                await service.recall(GUILD_A, self.c, seeds=["Mira"], audience=DM_SCREEN)
            self.assertEqual(load.call_count, 1)
            await self.add("Newcomer", type="character", role="npc")  # any write
            await service.recall(GUILD_A, self.c, seeds=["Mira"], audience=DM_SCREEN)
            self.assertEqual(load.call_count, 2)

    async def test_the_same_question_gets_the_same_answer(self) -> None:
        one = await self.recall(DM_SCREEN, "Mira", hops=2)
        two = await self.recall(DM_SCREEN, "Mira", hops=2)
        self.assertEqual(one, two)

    async def test_an_answer_is_checked_against_what_it_was_given(self) -> None:
        got = await self.recall(PLAYERS, "Mira")
        self.assertEqual(check_citations([self.member], got), ())
        self.assertEqual(check_citations([self.secret], got), (self.secret,))

    # ---- the claims index ------------------------------------------------------------

    async def test_a_fact_finds_its_claim_through_an_index(self) -> None:
        """Migration 0075 (an index only, no row changes, so no row-level-security opening)."""
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()"
                " AND indexname = 'memory_claims_by_fact'"
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertIn("(guild_id, campaign_id, fact_id)", found["indexdef"])

    # ---- isolation -------------------------------------------------------------------

    async def test_another_campaign_and_another_server_never_appear(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other Table", 99)).id
        mira2 = await self.add("Mira", campaign=other, type="character", role="npc")
        spy = await self.add("Spy", campaign=other, type="character", role="npc")
        leak = await self.memory.add_relation(
            GUILD_A, other, mira2, "ally_of", spy, source="dm", confidence=1.0, status="confirmed"
        )
        # The same names on another server, with a fact of their own.
        theirs = (await self.campaigns.create(GUILD_B, "Elsewhere", 98)).id
        mira3 = (
            await self.memory.add_entity(
                GUILD_B, theirs, type="character", name="Mira", source="dm", status="confirmed"
            )
        ).value.id
        mole = (
            await self.memory.add_entity(
                GUILD_B, theirs, type="character", name="Mole", source="dm", status="confirmed"
            )
        ).value.id
        await self.memory.add_relation(
            GUILD_B, theirs, mira3, "ally_of", mole, source="dm", confidence=1.0,
            status="confirmed",
        )  # fmt: skip
        for audience in (DM_SCREEN, PLAYERS):
            got = await self.recall(audience, "Mira", hops=2)
            self.assertNotIn(leak.value[0].id, self.ids(got))
            self.assertNotIn("Spy", self.texts(got))
            self.assertNotIn("Mole", self.texts(got))
        # By id, too: another campaign's or server's entry id means nothing here.
        for foreign in (mira2, mira3):
            by_id = await self.recall(DM_SCREEN, foreign, hops=2)
            self.assertEqual((by_id.items, by_id.seeds), ((), ()))
        # And from the other campaign, nothing of this one.
        there = await self.recall(DM_SCREEN, "Mira", campaign=other, hops=2)
        self.assertIn("Spy", self.texts(there))
        self.assertNotIn("Ash Cult", self.texts(there))
        # Another server asking for this campaign's id gets nothing at all.
        with self.assertRaises(MemoryRuleError):
            await self.service().recall(GUILD_B, self.c, seeds=["Mira"], audience=DM_SCREEN)


def note(line: str) -> None:
    """Put the numbers on the CI run's summary page, where the pull request can quote them."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


CEILING_MS = 100  # in CI; the goal is 50 ms, measured on the server


class Speed(MemoryTest):
    """One recall on a campaign of about 3,000 entries and 10,000 facts, under 100 ms in CI (the
    goal is 50 ms, measured on the server), with claims, mentions, a hub, a big group and the
    transcript reader all in play."""

    ENTRIES, FACTS = 3000, 10000

    async def build(self) -> None:
        # Entry 0 is a group, and every tenth entry is a hub: a third of the facts have one of
        # the first ten entries at one end, so "Name 0" is a group of hundreds of members.
        subject = "CASE WHEN n %% 3 = 0 THEN n %% 10 ELSE n %% %s END"
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                " description, status, source, created_at)"
                " SELECT %s, %s, md5('e' || n), CASE WHEN n %% 40 = 0 THEN 'faction'"
                " ELSE 'character' END, 'Name ' || n, repeat('d', 200), 'confirmed', 'dm', 1"
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
                "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id,"
                " session_started_at, line_ref, span_start, span_end, confidence, method,"
                " created_at) SELECT %s, %s, md5('m' || n), md5('e' || (n %% %s)), 1000,"
                " 'L' || n, 0, 4, 0.9, 'exact', 1 FROM generate_series(0, %s - 1) AS n",
                (GUILD_A, self.c, self.ENTRIES, self.FACTS),
            )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, from_session_at,"
                " secret, created_at)"
                " SELECT %s, %s, md5('r' || n), md5('e' || s), 'ally_of', md5('e' || o), '', 1.0,"
                " 'confirmed', 'dm', ARRAY[md5('m' || n)], 1000 + n, (n %% 50 = 0), 1"
                f" FROM (SELECT n, {subject} AS s, 1 + (n * 7) %% (%s - 1) AS o"
                " FROM generate_series(0, %s - 1) AS n) x WHERE s <> o",
                (GUILD_A, self.c, self.ENTRIES, self.ENTRIES, self.FACTS),
            )
            await beliefs.backfill(conn, GUILD_A, self.c)  # (raw rows: no write path made beliefs)
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, details, how, lines, speaker_ids, source, prompt_version, model,"
                " status, session_started_at, created_at, fact_id)"
                " SELECT %s, %s, md5('c' || id), 's', 'ally_of', 'o', '', 'dm_said',"
                " ARRAY['L' || (row_number() OVER ())], ARRAY[7]::bigint[], 'extractor', 'p',"
                " 'm', 'confirmed', 1000, 1, id FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s",
                (GUILD_A, self.c, GUILD_A, self.c),
            )
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) SELECT %s, %s, md5('e' || n), 1000, 7, 1 + n %% 9"
                " FROM generate_series(0, %s - 1) AS n",
                (GUILD_A, self.c, self.ENTRIES),
            )
            for table in (
                "memory_entities", "memory_aliases", "memory_relations", "memory_mentions",
                "memory_claims", "memory_heard", "memory_beliefs", "memory_perspectives",
            ):  # fmt: skip
                await conn.execute(f"ANALYZE {table}")

    async def test_a_recall_on_a_big_campaign_is_fast(self) -> None:
        await self.build()
        asked: list[int] = []

        async def lines(guild: int, campaign: str, refs: Sequence[str]) -> dict[str, Line]:
            asked.append(len(refs))
            return {r: said(r, "a line of talk about somebody", 7) for r in refs}

        service = RecallService(self.memory, lines)
        seeds = ["Name 0", "Name 3", "Name 1200"]  # a big group, a hub, and an ordinary one

        async def once(audience: str) -> tuple[float, Recall]:
            started = time.perf_counter()
            got = await service.recall(
                GUILD_A, self.c, seeds=seeds, audience=audience, opted_in=AGREED
            )
            return (time.perf_counter() - started) * 1000, got

        reports: list[str] = []
        bests: list[float] = []
        busy = speed.slowdown()  # how slow this machine is right now (#2490)
        for audience in (DM_SCREEN, PLAYERS):
            await once(audience)  # the first call warms the connection and the plans
            asked.clear()
            # A shared runner can be slow for a whole minute (a best of seven over 100 ms was
            # seen once, #1897, against 57-80 ms in every other run), so a measurement that
            # misses the ceiling is made once more and the better best counts. A recall that
            # really is slower misses both times. The ceiling itself is not moved.
            tries: list[list[tuple[float, Recall]]] = []
            for _ in range(2):
                tries.append([await once(audience) for _ in range(7)])
                if min(ms for ms, _ in tries[-1]) < speed.allowed(CEILING_MS, busy):
                    break
            runs = min(tries, key=lambda t: min(ms for ms, _ in t))
            times = [ms for ms, _ in runs]
            got = runs[0][1]
            facts = [i for i in got.items if i.kind == recall.FACT]
            self.assertGreater(len(got.items), 20)  # it really found a neighbourhood
            self.assertTrue(any(i.quotes for i in facts))  # and quoted from it
            # Evidence is looked up only for what is kept, never for the whole neighbourhood.
            self.assertLessEqual(max(asked), 2 * recall.QUOTED_FACTS)
            bests.append(min(times))
            earlier = (
                "".join(
                    f" (try {n}: best {min(ms for ms, _ in t):.1f} ms)"
                    for n, t in enumerate(tries, 1)
                )
                if len(tries) > 1
                else ""
            )
            reports.append(
                f"recall {audience}: best {min(times):.1f} ms, median"
                f" {statistics.median(times):.1f} ms, worst {max(times):.1f} ms over 7 runs"
                f"{earlier};"
                f" {self.ENTRIES} entries, {self.FACTS} facts, with claims and mentions;"
                f" {len(got.items)} items ({got.cut} cut, truncated: {got.truncated}),"
                f" up to {max(asked)} lines asked of the reader"
            )
        for line in reports:
            note(line)
        # The best of seven: shared CI runners run other tests beside this one, which only ever
        # adds time, so the best run is the fair measure of what the service itself costs. The
        # median and the worst are reported beside it. The ceiling here is 100 ms, not the issue's
        # 50 ms: a shared runner (a best of 56.9 ms was seen twice) is slower than the server. The
        # 50 ms goal stands, and dev1 measures it on the server itself.
        # The ceiling follows how busy the machine was (never below 100 ms, never above ten times
        # it): a loaded runner is not a slow recall, a slow query plan still is (#2490).
        ceiling = speed.allowed(CEILING_MS, busy)
        self.assertLess(max(bests), ceiling, f"ceiling {ceiling:.0f} ms || " + " || ".join(reports))


if __name__ == "__main__":
    unittest.main()
