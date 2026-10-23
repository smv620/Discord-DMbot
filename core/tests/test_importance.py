"""How much a name matters to the story (#2411): the score from the connections in The truth
(hidden ones count), the recap keeping a well-connected name's minor mention, and no hidden
connection ever reaching the writer. Invented names only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.devtools.recap_run import __main__ as run
from dmbot.memory import importance as imp
from dmbot.memory.models import CONFIRMED, name_key
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.beats import Beat
from tests import recap_fixtures as fx
from tests.test_memory_store import GUILD_A, MemoryTest

SECRET_WORDS = "poisoned the old king"


class Scoring(unittest.TestCase):
    def test_a_hub_outscores_a_loner_and_two_steps_out_adds_a_little(self) -> None:
        edges = [("hub", f"n{i}") for i in range(5)] + [("n0", "far"), ("a", "b")]
        got = imp.scores(edges)
        self.assertEqual(got["hub"], 5 + imp.TWO_STEP)  # five direct, one two steps out ("far")
        self.assertGreater(got["hub"], got["a"])
        self.assertEqual(got["a"], 1.0)

    def test_one_name_never_outweighs_a_whole_scene(self) -> None:
        got = imp.scores([("hub", f"n{i}") for i in range(100)])
        self.assertEqual(got["hub"], imp.CAP)

    def test_a_connection_to_itself_and_no_connections_count_for_nothing(self) -> None:
        self.assertEqual(imp.scores([("a", "a")]), {})
        self.assertEqual(imp.scores([]), {})


def beat(number: int, who: list[str], lines: int) -> Beat:
    return Beat(number, f"beat {number}", tuple(who), tuple(range(lines)), "event")


class Ranking(unittest.TestCase):
    def test_a_minor_mention_of_a_well_connected_name_is_kept(self) -> None:
        sheet = [
            beat(1, [], 1),  # the opening
            beat(2, ["Garrick"], 4),  # five mentions in all, and nobody ties to him
            beat(3, ["Garrick"], 4),
            beat(4, ["Lucian"], 1),  # the patron at the inn, said once, with many hidden ties
            beat(5, [], 1),  # where the session stopped
        ]
        plain = rd.pick_beats(sheet, limit=3)
        self.assertEqual([b.number for b in plain], [1, 2, 5])  # without it, the long scene wins
        ranked = rd.pick_beats(sheet, limit=3, importance={name_key("Lucian"): 12.0})
        self.assertEqual([b.number for b in ranked], [1, 4, 5])

    def test_what_plotbot_marked_still_comes_first(self) -> None:
        marked = Beat(2, "marked", ("Garrick",), (1,), "event", note="matters", keep=True)
        sheet = [beat(1, [], 1), marked, beat(3, ["Lucian"], 1), beat(4, [], 1)]
        got = rd.pick_beats(sheet, limit=3, importance={name_key("Lucian"): 12.0})
        self.assertEqual([b.number for b in got], [1, 2, 4])

    def test_no_table_changes_nothing(self) -> None:
        sheet = [beat(n, ["X"] * (n % 3), n) for n in range(1, 9)]
        self.assertEqual(
            [b.number for b in rd.pick_beats(sheet, limit=4)],
            [b.number for b in rd.pick_beats(sheet, limit=4, importance={})],
        )


class FromTheStory(MemoryTest):
    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("status", CONFIRMED)  # what the DM confirmed is what the story holds
        return await super().add(name, **kw)

    async def relate(self, a: str, pred: str, b: str, **kw: Any) -> Any:
        kw.setdefault("status", CONFIRMED)
        return await super().relate(a, pred, b, **kw)

    async def hub(self) -> str:
        """Lucian, tied by twelve hidden connections, and a loner."""
        lucian = await self.add("Lucian")
        for n in range(12):
            other = await self.add(f"Person {chr(97 + n)}")
            await self.relate(lucian, "knows", other, secret=True, detail=SECRET_WORDS)
        await self.add("Garrick")
        return lucian

    async def test_hidden_connections_count_and_names_and_other_names_are_scored(self) -> None:
        lucian = await self.hub()
        await self.memory.add_alias(
            GUILD_A, self.c, lucian, "the patron", kind="title", source="dm", status=CONFIRMED
        )
        got = await imp.by_name(self.memory, GUILD_A, self.c)
        self.assertGreaterEqual(got[name_key("Lucian")], 12.0)
        self.assertEqual(got[name_key("the patron")], got[name_key("Lucian")])
        self.assertNotIn(name_key("Garrick"), got)  # nobody ties to him

    async def test_a_connection_that_ended_or_is_false_in_the_truth_does_not_count(self) -> None:
        a, b, c = await self.add("Ann"), await self.add("Bo"), await self.add("Cy")
        ended = (await self.relate(a, "knows", b)).value[0]
        await self.relate(a, "knows", c)
        await self.memory.update_relation(
            GUILD_A, self.c, ended.id, source="dm", to_session_at=self.now
        )
        got = await imp.by_name(self.memory, GUILD_A, self.c)
        self.assertEqual(got[name_key("Ann")], 1.0)
        self.assertNotIn(name_key("Bo"), got)

    async def test_a_merged_name_counts_for_the_one_that_stayed(self) -> None:
        keep, gone, other = await self.add("Lucian"), await self.add("Luc"), await self.add("Bo")
        await self.relate(gone, "knows", other)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        got = await imp.by_name(self.memory, GUILD_A, self.c)
        self.assertEqual(got[name_key("Lucian")], 1.0)

    async def test_a_failing_read_is_remembered_as_no_scores(self) -> None:
        class Broken:
            reads = 0

            def _read(self, *a: Any, **k: Any) -> Any:
                Broken.reads += 1
                raise RuntimeError("down")

        cache = imp.ImportanceCache(Broken())
        with self.assertLogs("dmbot.memory.importance", level="ERROR"):
            self.assertEqual(await cache.get(GUILD_A, self.c), {})
        self.assertEqual(await cache.get(GUILD_A, self.c), {})
        self.assertEqual(Broken.reads, 1)  # not read again for every call

    async def test_another_campaign_has_its_own_scores(self) -> None:
        await self.hub()
        other = (await self.campaigns.create(GUILD_A, "Sunken City", 7)).id
        self.assertEqual(await imp.by_name(self.memory, GUILD_A, other), {})
        cache = imp.ImportanceCache(self.memory)
        self.assertTrue(await cache.get(GUILD_A, self.c))
        self.assertEqual(await cache.get(GUILD_A, other), {})

    async def test_the_cache_is_read_once_and_refreshed_on_asking(self) -> None:
        cache = imp.ImportanceCache(self.memory)
        self.assertEqual(await cache.get(GUILD_A, self.c), {})
        await self.hub()
        self.assertEqual(await cache.get(GUILD_A, self.c), {})  # kept until it is refreshed
        await cache.refresh(GUILD_A, self.c)
        self.assertTrue(await cache.get(GUILD_A, self.c))

    async def test_a_hidden_connection_never_reaches_the_writer(self) -> None:
        # The recap ranks by the numbers and is given none of the words: run it, with the hub's
        # scores in, and read everything the AI was sent.
        await self.hub()
        scores = dict(await imp.by_name(self.memory, GUILD_A, self.c))
        scores[name_key("Oskar")] = 12.0  # the canned session's own name, so ranking acts on it
        fast, careful = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER,
                                  fx.FINISH_ANSWER), fx.Canned()  # fmt: skip
        base: dict[str, Any] = {
            "lines": fx.lines(), "dm_ids": {fx.DM}, "allowed_names": list(fx.NAMES),
            "secret_names": ["Lucian"], "previous": "", "day": 4, "hours": 3.5,
            "importance": scores,
        }  # fmt: skip
        out = await run.run_text(pl.Inputs(**base), fast, careful, models=("m-fast", "m-careful"))
        sent = " ".join([*fast.sent, *careful.sent, *fast.systems, *careful.systems])
        self.assertNotIn(SECRET_WORDS, sent)
        self.assertNotIn("Lucian", sent)
        self.assertEqual(out.job.state, "ready")
