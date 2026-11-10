"""Unnamed beings met in play, kept quietly at rank 0 (#3172 slice 5, DMBOT_MET_UNNAMED): the
reader's rules (only the DM's own words, one item per encounter, the cap), the extractor's use of
the claims call (no extra call), and the store (one entry per encounter, found again in the same
session, a name the campaign has is no unnamed being). Invented names only; the AI is canned."""

from __future__ import annotations

import json
import unittest
from collections.abc import Collection, Sequence
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import extractor, importance_rank, met_reading
from dmbot.memory.met_reading import Met, gathered, in_words_of, parse_met
from dmbot.memory.models import CONFIRMED
from tests.test_extractor import DM, Kept, dm_session, go, line, session
from tests.test_memory_names import GUILD, NamesTest

SAID = {
    "1": "Two barbarians jump out from behind the rocks.",
    "2": "Watch out, there's a yeti near!",
    "3": "Your lantern turns green: a monstrosity is within 300 feet.",
}


def answer(*items: dict[str, Any]) -> str:
    return json.dumps({"claims": [], "met": list(items)})


class Reading(unittest.TestCase):
    def test_a_group_the_dm_described_is_one_item_with_its_count(self) -> None:
        got = parse_met(
            answer({"what": "barbarians", "count": 2, "lines": [1]}), SAID, dm_lines={"1", "3"}
        )
        self.assertEqual(got.met, [Met("barbarians", 2, ("1",))])
        self.assertTrue(got.met[0].group)

    def test_a_players_guess_never_makes_one_nor_names_its_kind(self) -> None:
        """Supervisor (2026-11-10): only the DM's own words; a player's guess never counts."""
        guess = answer({"what": "yeti", "count": 1, "lines": [2]})
        self.assertEqual(parse_met(guess, SAID, dm_lines={"1", "3"}).not_dm, 1)
        # the DM's line beside it, but the kind is only in the player's words: still refused
        named = answer({"what": "yeti", "count": 1, "lines": [2, 3]})
        got = parse_met(named, SAID, dm_lines={"1", "3"})
        self.assertEqual((got.met, got.not_dm), ([], 1))

    def test_a_creature_the_dm_says_is_sensed_counts(self) -> None:
        got = parse_met(
            answer({"what": "a monstrosity", "count": 1, "lines": [3]}), SAID, dm_lines={"1", "3"}
        )
        self.assertEqual([m.what for m in got.met], ["a monstrosity"])
        self.assertFalse(got.met[0].group)

    def test_bad_items_are_refused_and_counted(self) -> None:
        bad = answer(
            {"what": "", "count": 1, "lines": [1]},
            {"what": "barbarians", "count": True, "lines": [1]},
            {"what": "barbarians", "count": 2, "lines": [9]},
            {"what": "barbarians", "count": -1, "lines": [1]},
            {"what": "they", "count": 2, "lines": [1]},
            "barbarians",
        )
        self.assertEqual(parse_met(bad, SAID, dm_lines={"1"}).refused, 6)

    def test_no_number_said_is_several_and_an_answer_with_no_list_counts_nothing(self) -> None:
        got = parse_met(
            answer({"what": "barbarians", "count": None, "lines": [1]}), SAID, dm_lines={"1"}
        )
        self.assertEqual(got.met[0].count, met_reading.SEVERAL)
        nothing = parse_met(json.dumps({"claims": []}), SAID, dm_lines={"1"})
        self.assertEqual((nothing.met, nothing.refused), ([], 0))
        self.assertEqual(parse_met("not json", SAID, dm_lines={"1"}).refused, 0)

    def test_the_words_are_checked_with_plurals(self) -> None:
        self.assertTrue(in_words_of("barbarian", ["Two barbarians jump out."]))
        self.assertTrue(in_words_of("the wolf", ["Three wolves circle the camp."]))
        self.assertFalse(in_words_of("ogre", ["Two barbarians jump out."]))

    def test_the_same_words_are_one_encounter_and_the_cap_holds(self) -> None:
        items, past = gathered(
            [Met("barbarians", 2, ("1",)), Met("the barbarians", 0, ("4",))]
            + [Met(f"thing {n}", 1, ("5",)) for n in range(12)]
        )
        self.assertEqual(items[0], Met("barbarians", 2, ("1", "4")))
        self.assertEqual((len(items), past), (met_reading.MAX_PER_SESSION, 3))


class MetAnswer:
    """A fake AI: the claims call answers with no claims and the `met` it is given."""

    def __init__(self, met: list[dict[str, Any]]) -> None:
        self.systems: list[str] = []
        self.met = met

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.systems.append(system)
        return Reply(json.dumps({"claims": [], "met": self.met}), False, 10, 5, "m-fast")


class Keeping:
    """A store that remembers what `add_met` was handed."""

    def __init__(self) -> None:
        self.kept: list[list[tuple[str, int, Sequence[str]]]] = []

    async def add_met(
        self, guild_id: int, campaign_id: str, met: Sequence[tuple[str, int, Sequence[str]]],
        *, session_started_at: int | None,
    ) -> Any:  # fmt: skip
        self.kept.append(list(met))
        from dmbot.memory.store import MetAdded

        return MetAdded(made=len(met))


DOGS = {"what": "sled dogs", "count": 7, "lines": [1]}


class InTheClaimsCall(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default_the_prompt_is_the_claims_one_and_nothing_is_kept(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        report = await go(ai, Kept(), dm_session(10), met_store=store)
        self.assertEqual(ai.systems, [extractor.SYSTEM])
        self.assertEqual((report.met, store.kept), (0, []))

    async def test_on_it_costs_no_call_and_keeps_what_the_dm_said(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        lines = [line(1, DM, "The sled dogs pull hard."), *dm_session(10)[1:]]
        report = await go(ai, Kept(), lines, met=True, met_store=store)
        self.assertEqual(report.calls, 1)  # (asked in the claims call itself)
        self.assertIn(met_reading.MET_ADDENDUM, ai.systems[0])
        self.assertEqual(store.kept, [[("sled dogs", 7, ("1",))]])
        self.assertEqual(report.met_made, 1)
        self.assertNotIn("sled", report.line())  # counts only in the log line
        self.assertIn("met: 1 found", report.line())

    async def test_a_players_line_is_refused(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        lines = [line(1, text="The sled dogs pull hard."), *session(10)[1:]]  # (a player's)
        report = await go(ai, Kept(), lines, met=True, met_store=store)
        self.assertEqual((report.met_not_dm, store.kept), (1, []))

    async def test_someone_who_stopped_by_the_end_loses_the_item(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        asked = 0

        async def consenting() -> Collection[int]:
            nonlocal asked
            asked += 1
            return {DM} if asked == 1 else set()

        lines = [line(1, DM, "The sled dogs pull hard."), *dm_session(10)[1:]]
        report = await go(ai, Kept(), lines, consenting, met=True, met_store=store)
        self.assertEqual(store.kept, [])
        self.assertEqual(report.met_made, 0)

    async def test_never_in_the_families_only_mode(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        await go(ai, Kept(), dm_session(10), met=True, met_store=store, family_only=True)
        self.assertEqual(ai.systems, [extractor.SYSTEM])


class Store(NamesTest):
    async def met(self, *items: tuple[str, int], at: int = 500) -> Any:
        return await self.memory.add_met(
            GUILD, self.campaign.id, [(w, n, ("4",)) for w, n in items], session_started_at=at
        )

    async def entries(self) -> list[Any]:
        return [e for e, _label in await self.memory.unnamed(GUILD, self.campaign.id)]

    async def test_one_quiet_entry_per_encounter_at_rank_0(self) -> None:
        done = await self.met(("barbarians", 2), ("a monstrosity", 1))
        self.assertEqual((done.made, done.again, done.named), (2, 0, 0))
        made = {e.unnamed_what: e for e in await self.entries()}
        self.assertEqual(made["barbarians"].type, "faction")  # (a group)
        self.assertEqual(made["a monstrosity"].type, "character")
        self.assertEqual(made["barbarians"].description, "2 of them")
        for e in made.values():
            self.assertEqual(e.status, "proposed")
            self.assertTrue(e.unseen_by_players)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT rank, reason, source, session_started_at, lines FROM memory_importance"
                " WHERE guild_id = %s",
                (GUILD,),
            )
            rows = await cur.fetchall()
        self.assertEqual(
            {(r["rank"], r["reason"], r["source"], r["session_started_at"]) for r in rows},
            {(0, importance_rank.MET_IN_PLAY, importance_rank.SOURCE_RULE, 500)},
        )
        self.assertEqual(rows[0]["lines"], [4])

    async def test_the_same_session_finds_it_again_and_another_session_makes_a_new_one(
        self,
    ) -> None:
        await self.met(("barbarians", 2))
        again = await self.met(("the barbarians", 2))  # (a restarted read of the same session)
        self.assertEqual((again.made, again.again), (0, 1))
        later = await self.met(("barbarians", 2), at=900)  # (two ambushes a month apart)
        self.assertEqual(later.made, 1)
        self.assertEqual(len(await self.entries()), 2)

    async def test_a_name_the_campaign_has_is_no_unnamed_being(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, name="Boy", type="character", status=CONFIRMED, source="dm"
        )
        done = await self.met(("Boy", 1))
        self.assertEqual((done.made, done.named), (0, 1))
        self.assertEqual(await self.entries(), [])
