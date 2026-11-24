"""Where the story reader's own new names end, for the per-name trace (#5123): each name a session's
claims hold ends with one fixed reason under source `story`, as the lore read's do (#5106).
Postgres, as the linker's tests; invented names."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.devtools import why_missed
from dmbot.memory import name_reader, trace_note
from dmbot.memory import name_trace as nt
from dmbot.memory import trace_note as tn
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import HOW_PLAN, REJECTED
from tests import test_linker
from tests.test_linker import said
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


class TheStoryTrace(ConfirmedMemoryTest):
    # (the linker's helpers, not its tests)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")

    propose = test_linker.Linking.propose
    entries = test_linker.Linking.entries

    async def traced(self, *found: Extracted, **kw: Any) -> list[tuple[str, str, str]]:
        """(name, reason, why) of each note, in order, from a story read."""
        with trace_note.collecting() as rows, trace_note.from_source(trace_note.STORY):
            await self.propose(*found, **kw)
        self.assertTrue(all(src == trace_note.STORY for src, _n, _r, _w in rows))
        return [(n, r, w) for _src, n, r, w in rows]

    def last(self, rows: Sequence[tuple[str, str, str]], name: str) -> str:
        return [r for n, r, _w in rows if n == name][-1]

    async def test_a_new_name_is_stored_for_the_dm(self) -> None:
        rows = await self.traced(said("Bram", "knows", "Oskar Vance"))
        self.assertEqual(rows, [("Oskar Vance", trace_note.STORED, "")])

    async def test_another_spelling_is_folded_into_the_name_with_it(self) -> None:
        rows = await self.traced(
            said("Bram", "knows", "Oskar Vance"), said("Bram", "fears", "Oskar Vence")
        )
        self.assertIn(("Oskar Vance", trace_note.STORED, ""), rows)
        self.assertIn(("Oskar Vence", trace_note.FOLDED, "Oskar Vance"), rows)

    async def test_a_name_past_the_limit_waits(self) -> None:
        rows = await self.traced(
            said("Bram", "knows", "Mother Ilse"), said("Bram", "knows", "Mother Ilse"),
            said("Bram", "fears", "Kesh Varn"), new_names=1,
        )  # fmt: skip
        self.assertEqual(self.last(rows, "Mother Ilse"), trace_note.STORED)
        self.assertEqual(self.last(rows, "Kesh Varn"), trace_note.OVER_CAP)

    async def test_a_pronoun_is_an_ordinary_word(self) -> None:
        rows = await self.traced(said("Bram", "knows", "He"))
        self.assertEqual(rows, [("He", trace_note.ORDINARY, "")])

    async def test_a_person_at_the_table_is_never_a_story_name(self) -> None:
        rows = await self.traced(said("Bram", "knows", "Mia"), skip={"mia"})
        self.assertEqual(rows, [("Mia", trace_note.AT_TABLE, "")])

    async def test_a_name_the_dm_turned_down_says_so(self) -> None:
        await self.propose(said("Bram", "knows", "Nobody Special"))
        nobody = (await self.entries())["Nobody Special"]
        await self.memory.set_entity_status(GUILD_A, self.c, nobody.id, REJECTED, source=DM_SOURCE)
        rows = await self.traced(said("Bram", "owes", "Nobody Special"))
        self.assertEqual(rows, [("Nobody Special", trace_note.DM_SAID_NO, "")])

    async def test_a_kind_of_creature_is_the_rules(self) -> None:
        rows = await self.traced(said("Bram", "fights", "an owlbear"))
        self.assertEqual([r for n, r, _w in rows if "owlbear" in n], [trace_note.RULES])

    async def test_a_name_in_a_dropped_claim_says_which_rule(self) -> None:
        plan = replace(said("Bram", "will visit", "Kesh Varn"), how=HOW_PLAN)
        rows = await self.traced(plan)
        self.assertEqual(len(rows), 1)
        name, reason, why = rows[0]
        self.assertEqual((name, reason), ("Kesh Varn", trace_note.CLAIM_DROPPED))
        self.assertTrue(why)  # (the rule's own words)

    async def test_a_stopped_speakers_claim_leaves_no_name(self) -> None:
        # (#5123: `judge` checks consent first, so their names are never noted)
        rows = await self.traced(said("Bram", "knows", "Kesh Varn", "3"), speakers={"3": 99})
        self.assertEqual(rows, [])

    async def test_nothing_is_noted_unless_a_trace_is_collected(self) -> None:
        got = await self.propose(said("Bram", "knows", "Oskar Vance"))
        self.assertEqual(got.new, ("Oskar Vance",))  # (and no collector: nothing to read)


class WhyMissed(unittest.TestCase):
    def test_a_story_name_says_the_rule_that_dropped_its_claim(self) -> None:
        rows = [
            nt.Row(tn.STORY, "Kesh Varn", tn.CLAIM_DROPPED, "a plan, not something that happened")
        ]
        self.assertEqual(
            why_missed._not_stored_why(["kesh varn"], why_missed._trace_index(rows)),
            "never stored: story ended it as claim_dropped (a plan, not something that happened)",
        )


def _rows(path: Path) -> list[nt.Row]:
    return nt.read(path) if path.exists() else []


class TheWriter(unittest.IsolatedAsyncioTestCase):
    """`DMBot._write_story_trace` (#5123): the notes as rows, or, when anyone stopped being recorded
    while the story reader read, one count row and no name."""

    def bot(self, folder: str, now: set[int]) -> Any:
        settings = SimpleNamespace(name_trace_dir=folder, name_trace_guild=5)
        campaign = SimpleNamespace(guild_id=5, id="c1", name="test of the frostmaiden (sample)")
        return SimpleNamespace(
            settings=settings,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            consent=SimpleNamespace(consenting=AsyncMock(return_value=now)),
        )

    async def written(self, now: set[int]) -> list[nt.Row]:
        noted = [(tn.STORY, "Kesh Varn", tn.STORED, ""), (tn.STORY, "He", tn.ORDINARY, "")]
        with tempfile.TemporaryDirectory() as d:
            bot = self.bot(d, now)
            await DMBot._write_story_trace(bot, 5, "c1", "s1", noted, frozenset({1, 2}))
            return _rows(Path(d, "trace-c1.jsonl"))

    async def test_with_everyone_still_recorded_each_name_is_written(self) -> None:
        rows = await self.written({1, 2})
        self.assertEqual(
            [(r.source, r.name, r.reason) for r in rows],
            [(tn.STORY, "Kesh Varn", tn.STORED), (tn.STORY, "He", tn.ORDINARY)],
        )

    async def test_after_a_stop_only_a_count_is_written(self) -> None:
        rows = await self.written({1})
        self.assertEqual(
            [(r.source, r.name, r.reason, r.times) for r in rows],
            [(tn.STORY, "", name_reader.STOPPED, 2)],
        )
