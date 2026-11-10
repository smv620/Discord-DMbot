"""Unnamed beings met in play, kept apart from the names (#3172 slice 5, DMBOT_MET_UNNAMED;
Supervisor's option C): the reader's rules (only the DM's own words, one item per encounter, the
cap), the extractor's use of the claims call (no extra call), and the table `memory_met` (one row
per encounter, found again in the same session, a name the campaign has is no unnamed being, and
gone with its session, its transcript and the speaker it cites). Invented names, a canned AI."""

from __future__ import annotations

import json
import unittest
from collections.abc import Collection, Sequence
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import extractor, met_reading
from dmbot.memory.met_reading import Met, gathered, in_words_of, parse_met
from dmbot.memory.met_store import MetAdded, MetRow, MetStore
from dmbot.memory.models import CONFIRMED
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.test_extractor import DM, Kept, dm_session, go, line, session
from tests.test_memory_names import DM as DM_ID
from tests.test_memory_names import GUILD, NamesTest

SAID = {
    "1": "Two barbarians jump out from behind the rocks.",
    "2": "Watch out, there's a yeti near!",
    "3": "Your lantern turns green: a monstrosity is within 300 feet.",
}


def answer(*items: object) -> str:
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
    """A met store that remembers what it was handed."""

    def __init__(self) -> None:
        self.kept: list[list[tuple[str, int, Sequence[str], Sequence[int]]]] = []

    async def add(
        self, guild_id: int, campaign_id: str, session_id: str,
        met: Sequence[tuple[str, int, Sequence[str], Sequence[int]]],
        *, session_started_at: int | None,
    ) -> MetAdded:  # fmt: skip
        self.kept.append(list(met))
        return MetAdded(made=len(met))


DOGS = {"what": "sled dogs", "count": 7, "lines": [1]}


class InTheClaimsCall(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default_the_prompt_is_the_claims_one_and_nothing_is_kept(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        report = await go(ai, Kept(), dm_session(10), met_store=store, session_id="s")
        self.assertEqual(ai.systems, [extractor.SYSTEM])
        self.assertEqual((report.met, store.kept), (0, []))

    async def test_on_it_costs_no_call_and_keeps_what_the_dm_said(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        lines = [line(1, DM, "The sled dogs pull hard."), *dm_session(10)[1:]]
        report = await go(ai, Kept(), lines, met=True, met_store=store, session_id="s")
        self.assertEqual(report.calls, 1)  # (asked in the claims call itself)
        self.assertIn(met_reading.MET_ADDENDUM, ai.systems[0])
        self.assertEqual(store.kept, [[("sled dogs", 7, ("1",), [DM])]])
        self.assertEqual(report.met_made, 1)
        self.assertNotIn("sled", report.line())  # counts only in the log line
        self.assertIn("met: 1 found", report.line())

    async def test_a_players_line_is_refused(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        lines = [line(1, text="The sled dogs pull hard."), *session(10)[1:]]  # (a player's)
        report = await go(ai, Kept(), lines, met=True, met_store=store, session_id="s")
        self.assertEqual((report.met_not_dm, store.kept), (1, []))

    async def test_someone_who_stopped_by_the_end_loses_the_item(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        asked = 0

        async def consenting() -> Collection[int]:
            nonlocal asked
            asked += 1
            return {DM} if asked == 1 else set()

        lines = [line(1, DM, "The sled dogs pull hard."), *dm_session(10)[1:]]
        report = await go(ai, Kept(), lines, consenting, met=True, met_store=store, session_id="s")
        self.assertEqual(store.kept, [])
        self.assertEqual(report.met_made, 0)

    async def test_never_in_the_families_only_mode(self) -> None:
        ai, store = MetAnswer([DOGS]), Keeping()
        await go(ai, Kept(), dm_session(10), met=True, met_store=store, session_id="s",
                 family_only=True)  # fmt: skip
        self.assertEqual(ai.systems, [extractor.SYSTEM])


class Store(NamesTest):
    """The table `memory_met` (Supervisor's option C): apart from the names, so quiet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.met_store = MetStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD, self.sid, [Line(1_700_000_001_000, DM_ID, "Two barbarians jump out.", "x")]
        )
        await self.transcripts.end_session(GUILD, self.sid, 1_700_001_000)

    async def met(self, *items: tuple[str, int], sid: str | None = None) -> MetAdded:
        return await self.met_store.add(
            GUILD, self.campaign.id, sid or self.sid,
            [(w, n, ("1",), (DM_ID,)) for w, n in items], session_started_at=1_700_000_000,
        )  # fmt: skip

    async def rows(self) -> list[MetRow]:
        return await self.met_store.of_session(GUILD, self.campaign.id, self.sid)

    async def test_one_row_per_encounter_and_no_entry_at_all(self) -> None:
        done = await self.met(("barbarians", 2), ("a monstrosity", 1))
        self.assertEqual((done.made, done.again, done.named), (2, 0, 0))
        rows = await self.rows()
        self.assertEqual(
            sorted((r.what, r.count, r.lines) for r in rows),  # (one moment: no order between)
            [("a monstrosity", 1, ("1",)), ("barbarians", 2, ("1",))],
        )
        # quiet by construction: nothing is a name, so no list, count or check can show it
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_entities WHERE guild_id = %s AND unnamed",
                (GUILD,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
        self.assertEqual(await self.memory.unnamed(GUILD, self.campaign.id), [])

    async def test_the_same_words_in_the_session_are_found_again(self) -> None:
        await self.met(("barbarians", 2))
        again = await self.met(("the Barbarians", 2))  # (a restarted read)
        self.assertEqual((again.made, again.again), (0, 1))
        self.assertEqual(len(await self.rows()), 1)

    async def test_a_name_the_campaign_has_is_no_unnamed_being(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, name="Boy", type="character", status=CONFIRMED, source="dm"
        )
        done = await self.met(("Boy", 1))
        self.assertEqual((done.made, done.named), (0, 1))
        self.assertEqual(await self.rows(), [])

    async def test_it_goes_with_the_sessions_transcript(self) -> None:
        await self.met(("barbarians", 2))
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.campaign.id, self.sid, 2_000_000_000, 1
        )
        self.assertEqual(gone, 1)  # (the control: the transcript did go)
        self.assertEqual(await self.rows(), [])

    async def test_it_goes_with_the_session_itself(self) -> None:
        await self.met(("barbarians", 2))
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM transcript_sessions WHERE guild_id = %s AND id = %s", (GUILD, self.sid)
            )
            cur = await conn.execute("SELECT count(*) AS n FROM memory_met WHERE guild_id = %s",
                                     (GUILD,))  # fmt: skip
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_it_goes_when_the_speaker_it_cites_is_removed(self) -> None:
        await self.met(("barbarians", 2))
        await self.transcripts.remove_speaker(GUILD, self.campaign.id, DM_ID)
        self.assertEqual(await self.rows(), [])
