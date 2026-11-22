"""The recap cites the session's partial reveals (#4737, `DMBOT_RECAP_REVEALS`, #2997): off, the
writer's sheet is exactly what it was; on, each reveal's sentence goes in unchanged and marked, the
checks accept it and refuse added detail, and a person not recorded now gives no sentence.
Stand-ins, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.models import ABANDONED, GOING, HINTED, OPEN, RESOLVED
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.beats import Beat
from dmbot.recaps.discord import RecapFeatures

SAID = "Okthandom learned something about Vane."
SCENE = "Okthandom and Wulfgar had a private word."


def beat(n: int) -> Beat:
    return Beat(n, "Mira drew her blade.", ("Mira",), (n,), "event")


def worker(reveals: Any = None, colour: Any = None) -> Any:
    w: Any = pl.RecapWorker.__new__(pl.RecapWorker)
    w._colour = colour
    w._reveals = reveals
    w._read_people_sheet = AsyncMock(return_value=("Mira: the party's rogue",))
    return w


JOB: Any = SimpleNamespace(session_at=100)
INPUTS: Any = SimpleNamespace()


class TheSheet(unittest.IsolatedAsyncioTestCase):
    async def test_off_the_input_is_exactly_what_it_was(self) -> None:
        got = await worker(None)._read_sheet(1, "c", JOB, INPUTS, [beat(1)])
        self.assertEqual(got, ("Mira: the party's rogue",))
        text = rd.prose_message([beat(1)], "", set(), None, who_and_what=got)
        self.assertNotIn("Cite this", text)
        self.assertNotIn("one exception", text)

    async def test_on_the_sentences_are_passed_exactly_and_marked(self) -> None:
        reveals = AsyncMock(return_value=[SAID, SCENE])
        got = await worker(reveals)._read_sheet(1, "c", JOB, INPUTS, [beat(1)])
        self.assertEqual(
            got, ("Mira: the party's rogue", pl.REVEAL_MARK + SAID, pl.REVEAL_MARK + SCENE)
        )
        text = rd.prose_message([beat(1)], "", set(), None, who_and_what=got)
        self.assertIn(pl.REVEAL_MARK + SAID, text)
        self.assertIn("that line once, as narration", text)

    async def test_a_reveal_keeps_its_room_when_the_sheet_is_full(self) -> None:
        w = worker(AsyncMock(return_value=[SAID]), AsyncMock(return_value=["Town: hills."]))
        w._read_people_sheet = AsyncMock(return_value=tuple(f"Person {i}" for i in range(60)))
        got = await w._read_sheet(
            1, "c", JOB, INPUTS, [Beat(1, "x", ("a",), (1,), "event", where="Town")]
        )
        self.assertEqual(len(got), rd.SHEET_LINES)
        self.assertIn(pl.REVEAL_MARK + SAID, got)

    async def test_a_failure_costs_the_sentences_and_never_the_recap(self) -> None:
        reveals = AsyncMock(side_effect=RuntimeError("Vane is the traitor"))
        with self.assertLogs("dmbot.recaps.pipeline", level="WARNING") as logged:
            got = await worker(reveals)._read_sheet(1, "c", JOB, INPUTS, [beat(1)])
        self.assertEqual(got, ("Mira: the party's rogue",))
        self.assertNotIn("traitor", logged.output[0])  # (the type, never the text)


def check(narration: str, line: str = pl.REVEAL_MARK + SAID, advisory: Any = None) -> list[str]:
    return rd.reveal_problems(narration, (line,), advisory)


class TheChecks(unittest.TestCase):
    def test_the_marker_in_its_own_words_is_supported(self) -> None:
        self.assertEqual(check("Okthandom learned something about Vane. Then they rode on."), [])
        self.assertEqual(check("Okthandom learned something."), [])
        self.assertEqual(check("Okthandom learned something, and rode on."), [])
        self.assertEqual(check("Okthandom learned to read the map."), [], "no detail of a secret")
        self.assertEqual(
            check(SCENE, pl.REVEAL_MARK + SCENE), [], "a private word needs no detail either"
        )

    def test_an_added_detail_is_flagged(self) -> None:
        for said in (
            "Okthandom learned that Vane was a traitor.",
            "Okthandom learned something about Vane's past.",
            "Okthandom found out how Vane had lied.",
            "Okthandom was told a secret about the Tower.",
            "Okthandom learned something that the king is dead.",
            "Okthandom learned something terrible about the Tower.",
            "Okthandom learned something in the crypt.",
        ):
            with self.subTest(said):
                problems = check(said)
                self.assertEqual(len(problems), 1)
                self.assertNotIn("traitor", problems[0])  # (never the detail, in the retry)

    def test_a_sentence_without_the_person_is_not_read(self) -> None:
        self.assertEqual(check("Mira learned that the bridge was out."), [])

    def test_a_reveal_not_cited_is_counted_never_refused(self) -> None:
        advisory: list[str] = []
        self.assertEqual(check("They rode on.", advisory=advisory), [])
        self.assertEqual(advisory, ["reveal"])

    def test_without_a_marker_nothing_is_checked(self) -> None:
        self.assertEqual(rd.reveal_problems("Okthandom learned that Vane lied.", ()), [])

    def test_reveal_confided_is_fine(self) -> None:
        self.assertEqual(check("Okthandom confided something about Vane."), [])

    def test_reveal_realised_is_fine(self) -> None:
        self.assertEqual(check("Okthandom realised something about Vane."), [])

    def test_reveal_something_then_rode_on_is_fine(self) -> None:
        self.assertEqual(check("Okthandom learned something about Vane, then rode on."), [])

    def test_reveal_whispered_that_is_one_problem_without_the_detail(self) -> None:
        # (a regression guard: the message is a fixed string, so this asserts it names no detail)
        problems = check("Okthandom whispered that Vane lied.")
        self.assertEqual(len(problems), 1)
        self.assertNotIn("lied", problems[0])

    def test_reveal_scene_with_a_long_clause_is_fine(self) -> None:
        line = pl.REVEAL_MARK + "Okthandom and Wulfgar had a private word."
        self.assertEqual(
            check("Okthandom and Wulfgar had a private word, and it was long.", line), []
        )

    def test_reveal_two_subjects_about_something_is_fine(self) -> None:
        line = pl.REVEAL_MARK + "Wulfgar learned something about Vane and the Tower."
        self.assertEqual(check("Wulfgar learned something about Vane and the Tower.", line), [])

    def test_reveal_unmarked_line_is_not_read(self) -> None:
        plain = "Okthandom learned something about Vane."
        self.assertEqual(rd.reveal_problems("Okthandom learned that Vane lied.", (plain,)), [])

    def test_reveal_unknown_sentence_is_not_read(self) -> None:
        # (a narration that would be flagged, so the empty result comes from skipping the line)
        line = pl.REVEAL_MARK + "not a sentence we know"
        self.assertEqual(check("Okthandom learned that Vane lied.", line), [])

    def test_reveal_not_cited_is_advisory_for_a_scene(self) -> None:
        line = pl.REVEAL_MARK + "Okthandom and Wulfgar had a private word."
        advisory: list[str] = []
        self.assertEqual(check("They rode on.", line, advisory), [])
        self.assertEqual(advisory, ["reveal"])

    def test_reveal_a_named_person_counts_as_cited(self) -> None:
        line = pl.REVEAL_MARK + "Okthandom and Wulfgar had a private word."
        advisory: list[str] = []
        self.assertEqual(check("Wulfgar had a quiet moment.", line, advisory), [])
        self.assertEqual(advisory, [])

    def test_reveal_scene_detail_after_the_word_is_flagged(self) -> None:
        line = pl.REVEAL_MARK + "Okthandom and Wulfgar had a private word."
        narration = (
            "Okthandom and Wulfgar had a private word, and Okthandom learned that Vane lied."
        )
        problems = check(narration, line)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("lied", problems[0])

    def test_reveal_one_of_two_subjects_is_fine(self) -> None:
        line = pl.REVEAL_MARK + "Wulfgar learned something about Vane and the Tower."
        self.assertEqual(check("Wulfgar learned something about the Tower.", line), [])

    def test_reveal_confided_that_is_flagged(self) -> None:
        self.assertEqual(len(check("Okthandom confided that Vane lied.")), 1)

    def test_reveal_realised_that_is_flagged(self) -> None:
        self.assertEqual(len(check("Okthandom realised that Vane lied.")), 1)


class TheBotsReader(unittest.IsolatedAsyncioTestCase):
    def bot(self, recorded: set[int]) -> Any:
        mira = SimpleNamespace(id="e1", is_player_character=True, played_by=11)
        gone = SimpleNamespace(id="e2", is_player_character=True, played_by=22)
        npc = SimpleNamespace(id="e3", is_player_character=False, played_by=None)
        seen = SimpleNamespace(how_sure=lambda kind, i: "sure" if i == "t1" else None)
        threads = [
            SimpleNamespace(
                thread=SimpleNamespace(id="t1"),
                state=SimpleNamespace(condition=OPEN),
                involved=[SimpleNamespace(who=SimpleNamespace(id="n1"))],
            ),
            SimpleNamespace(  # (the DM's own: the party doesn't know it)
                thread=SimpleNamespace(id="t2"),
                state=SimpleNamespace(condition=OPEN),
                involved=[SimpleNamespace(who=SimpleNamespace(id="n2"))],
            ),
        ]
        memory = SimpleNamespace(
            entities=AsyncMock(return_value=[mira, gone, npc]),
            # (that session's party and what it knew, one read, #4537 3b and #4877; no members
            # read: every speaker's character counts, as before)
            party_at_session=AsyncMock(return_value=SimpleNamespace(members=(), seen=seen)),
            threads=AsyncMock(return_value=threads),
            recap_reveals=AsyncMock(return_value=[SAID]),
        )
        consent = SimpleNamespace(recorded_in=AsyncMock(return_value=recorded))
        return SimpleNamespace(memory=memory, consent=consent)

    async def test_the_table_is_the_speakers_characters_and_only_the_recorded_count(self) -> None:
        bot = self.bot({11})
        # (the normal path: the party at that session is read, and Mira is in it, #4877)
        bot.memory.party_at_session.return_value = SimpleNamespace(
            members=("e1",), seen=bot.memory.party_at_session.return_value.seen
        )
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        got = await RecapFeatures.recap_reveals(bot, 1, "c", job)
        self.assertEqual(got, [SAID])
        args = bot.memory.recap_reveals.await_args.args
        self.assertEqual(args[:3], (1, "c", 100))
        self.assertEqual(set(args[3]), {"e1"})  # not the NPC, not a player who didn't speak
        self.assertEqual(set(args[4]), {"t1", "n1"})  # only what the party knows is open
        # (what the party AT that session knows, #4537 slice 3b)
        bot.memory.party_at_session.assert_awaited_with(1, "c", 100)
        self.assertEqual(set(args[5]), {11})  # recorded now: the reader drops the rest

    async def test_a_speakers_character_outside_that_sessions_party_is_not_at_its_table(
        self,
    ) -> None:
        bot = self.bot({11, 22})
        bot.memory.party_at_session = AsyncMock(
            return_value=SimpleNamespace(
                members=("e2",), seen=SimpleNamespace(how_sure=lambda kind, i: "sure")
            )
        )
        job: Any = SimpleNamespace(session_at=100, speakers=(11, 22))
        await RecapFeatures.recap_reveals(bot, 1, "c", job)
        self.assertEqual(set(bot.memory.recap_reveals.await_args.args[3]), {"e2"})  # (#4877)

    async def test_nobody_at_the_table_asks_for_nothing(self) -> None:
        bot = self.bot({11})
        job: Any = SimpleNamespace(session_at=100, speakers=(99,))
        self.assertEqual(await RecapFeatures.recap_reveals(bot, 1, "c", job), [])
        bot.memory.recap_reveals.assert_not_awaited()

    def _reader(self, threads: list[Any]) -> Any:
        """A memory whose one player character spoke, with the given threads and a sure party."""
        pc = SimpleNamespace(id="e1", is_player_character=True, played_by=11)
        seen = SimpleNamespace(how_sure=lambda kind, i: "sure")
        memory = SimpleNamespace(
            entities=AsyncMock(return_value=[pc]),
            # (that session's party and what it knew, one read, #4537 3b and #4877; no members
            # read: every speaker's character counts, as before)
            party_at_session=AsyncMock(return_value=SimpleNamespace(members=(), seen=seen)),
            threads=AsyncMock(return_value=threads),
            recap_reveals=AsyncMock(return_value=[SAID]),
        )
        return SimpleNamespace(
            memory=memory, consent=SimpleNamespace(recorded_in=AsyncMock(return_value={11}))
        )

    @staticmethod
    def _thread(tid: str, condition: Any, who: list[str], state: bool = True) -> Any:
        return SimpleNamespace(
            thread=SimpleNamespace(id=tid),
            state=SimpleNamespace(condition=condition) if state else None,
            involved=[SimpleNamespace(who=SimpleNamespace(id=w)) for w in who],
        )

    async def test_recap_reveals_no_memory_asks_for_nothing(self) -> None:
        bot = self._reader([])
        bot.memory = None
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        self.assertEqual(await RecapFeatures.recap_reveals(bot, 1, "c", job), [])

    async def test_recap_reveals_only_open_threads_matter(self) -> None:
        threads = [
            self._thread("t-none", OPEN, ["n-none"], state=False),
            self._thread("t-resolved", RESOLVED, ["n-resolved"]),
            self._thread("t-abandoned", ABANDONED, ["n-abandoned"]),
            self._thread("t-open", OPEN, ["n-open"]),
        ]
        bot = self._reader(threads)
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        await RecapFeatures.recap_reveals(bot, 1, "c", job)
        self.assertEqual(set(bot.memory.recap_reveals.await_args.args[4]), {"t-open", "n-open"})

    async def test_recap_reveals_open_going_and_hinted_all_matter(self) -> None:
        threads = [
            self._thread("t-open", OPEN, ["n-open"]),
            self._thread("t-going", GOING, ["n-going"]),
            self._thread("t-hinted", HINTED, ["n-hinted"]),
        ]
        bot = self._reader(threads)
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        await RecapFeatures.recap_reveals(bot, 1, "c", job)
        self.assertEqual(
            set(bot.memory.recap_reveals.await_args.args[4]),
            {"t-open", "n-open", "t-going", "n-going", "t-hinted", "n-hinted"},
        )

    async def test_recap_reveals_a_character_not_played_asks_for_nothing(self) -> None:
        pc = SimpleNamespace(id="e1", is_player_character=True, played_by=None)
        bot = self._reader([])
        bot.memory.entities = AsyncMock(return_value=[pc])
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        self.assertEqual(await RecapFeatures.recap_reveals(bot, 1, "c", job), [])
        bot.memory.recap_reveals.assert_not_awaited()

    async def test_recap_reveals_a_thread_the_party_does_not_know_does_not_matter(self) -> None:
        threads = [
            self._thread("t-known", OPEN, ["n-known"]),
            self._thread("t-hidden", OPEN, ["n-hidden"]),
        ]
        bot = self._reader(threads)
        bot.memory.party_at_session = AsyncMock(
            return_value=SimpleNamespace(
                members=(),
                seen=SimpleNamespace(how_sure=lambda kind, i: "sure" if i == "t-known" else None),
            )
        )
        job: Any = SimpleNamespace(session_at=100, speakers=(11,))
        await RecapFeatures.recap_reveals(bot, 1, "c", job)
        self.assertEqual(set(bot.memory.recap_reveals.await_args.args[4]), {"t-known", "n-known"})
