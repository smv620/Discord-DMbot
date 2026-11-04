"""What the recap writer reads (#2590, #2954): the beat sheet, with the cleaned transcript, the
who-and-what sheet, the open threads and the earlier recaps beside it. Made-up lines; a fake AI,
nothing is paid for."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

from dmbot.config import ConfigError, load_settings
from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import critics as cr
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.beats import Beat
from dmbot.recaps.inputs import story_so_far
from dmbot.recaps.text import names_a_secret
from dmbot.transcript.models import SIDEBAR_QUESTION, Line
from tests import recap_fixtures as fx
from tests.test_recap_run import happy, inputs


def with_extras() -> list[Line]:
    """The fixture's lines plus a question to DMbot, off-topic chat and a line heard differently
    from how it was cleaned."""
    base = fx.lines()
    n = len(base) + 1
    return [
        *base,
        Line(n * 1000, fx.DM, "SIDEBARQ who is the traitor", "SIDEBARQ who is the traitor",
             sidebar=SIDEBAR_QUESTION),
        Line((n + 1) * 1000, fx.ALICE, "OFFTOPICCHAT pizza", "OFFTOPICCHAT pizza",
             topic="off_topic"),
        Line((n + 2) * 1000, fx.BOB, "RAWHEARD miraa draws", "CLEANEDLINE Mira draws"),
    ]  # fmt: skip


class TheWriterSystem(unittest.TestCase):
    def test_one_writer_reads_the_beats_and_the_context_and_adds_no_event(self) -> None:
        text = rd.prose_system(None, rd.length_for(3))
        self.assertIn("numbered BEAT SHEET", text)
        self.assertIn("TRANSCRIPT", text)
        self.assertIn("WHO AND WHAT", text)
        self.assertIn("Never use them to add an event", text)
        self.assertIn("Never invent a plan, a goal or a reason", text)
        for tag in ("<transcript>", "<who_and_what>", "<threads>", "<story_so_far>"):
            self.assertIn(tag, text)

    def test_the_fact_check_counts_the_transcript_as_support(self) -> None:
        beat, draft = TheMessage.BEAT, "Mira drew her blade."
        heard = rd.facts_message([beat], draft, transcript="Mira: I draw.")
        self.assertIn("<transcript>\nMira: I draw.\n</transcript>", heard)


class TheMessage(unittest.TestCase):
    BEAT = Beat(1, "Mira drew her blade.", ("Mira",), (1,), "event", note="a turning point")

    def test_the_beats_stay_the_foundation_and_the_context_rides_beside_them(self) -> None:
        message = rd.prose_message(
            [self.BEAT], "", ["Mira"], None,
            transcript=rd.transcript_block([("DM", "A door."), ("Mira", "I open it.")]),
            who_and_what=("Mira: the party's rogue",),
            threads=("Find the lost crown",),
            story=("Earlier, the party met a ferryman.",),
        )  # fmt: skip
        self.assertIn("<beats>", message)
        self.assertIn("<transcript>\nDM: A door.\nMira: I open it.\n</transcript>", message)
        self.assertIn("<ending>", message)
        self.assertIn("Mira: the party's rogue", message)
        self.assertIn("Find the lost crown", message)
        self.assertIn("the party met a ferryman", message)

    def test_a_long_line_reaches_the_writer_whole(self) -> None:
        long = "A long lore line. " * 100  # about 1,800 characters
        block = rd.transcript_block([("DM", long)])
        self.assertIn(long.strip(), block)

    def test_what_a_job_kept_is_dropped_when_it_ends(self) -> None:
        worker = pl.RecapWorker.__new__(pl.RecapWorker)
        key = (1, "c", 100)
        worker._heard = {key: "lines"}
        worker._given = {key: rd.Given()}
        worker._beats_instead = {key}
        worker._sheets = {key: ("a beat",)}
        worker._forget_reads(key)
        self.assertEqual(
            (worker._heard, worker._given, worker._beats_instead, worker._sheets),
            ({}, {}, set(), {}),
        )

    def test_the_fact_check_message_carries_the_transcript(self) -> None:
        plain = rd.facts_message([self.BEAT], "Mira drew her blade.")
        self.assertNotIn("<transcript>", plain)

    def test_the_fact_check_gets_the_bridge_when_no_earlier_recap_is_kept(self) -> None:
        # With the story trimmed to nothing, the bridge recap is what the check can read (#2965).
        bridged = rd.facts_message([self.BEAT], "Mira drew her blade.", previous="Mira met Orin.")
        self.assertIn("<story_so_far>\n<recap>Mira met Orin.</recap>\n</story_so_far>", bridged)

    def test_a_kept_story_is_read_instead_of_the_bridge(self) -> None:
        both = rd.facts_message(
            [self.BEAT], "Mira drew her blade.", story=("Earlier, a ferryman.",),
            previous="Mira met Orin.",
        )  # fmt: skip
        self.assertIn("<recap>Earlier, a ferryman.</recap>", both)
        self.assertNotIn("Mira met Orin.", both)

    def test_no_bridge_and_no_story_adds_no_block(self) -> None:
        bare = rd.facts_message([self.BEAT], "Mira drew her blade.", previous="   ")
        self.assertNotIn("<story_so_far>", bare)


class TheMaterialAfterAWait(unittest.IsolatedAsyncioTestCase):
    """The sheet, the earlier recaps and the transcript are read again after a wait, once, so a
    check after a wait sees what the writer saw (#2965 item 6)."""

    KEY = (1, "c", 100)

    def worker(self) -> pl.RecapWorker:
        w = pl.RecapWorker.__new__(pl.RecapWorker)
        w._given, w._heard, w._beats_instead = {}, {}, {self.KEY}
        w._sheets = {}
        return w

    async def test_the_material_is_read_once_and_again_only_after_a_wait(self) -> None:
        w: Any = self.worker()
        job: Any = SimpleNamespace(beats="[]", speakers=[], session_at=100)
        inputs: Any = SimpleNamespace(hours=None, importance=None)
        given = rd.Given(who_and_what=("Mira: the party's rogue",))
        calls: list[int] = []

        async def context(*_: object) -> tuple[str, rd.Given]:
            calls.append(1)
            return "Mira: I draw.", given

        w._context_for = context
        w._parts = lambda job, said: ([], [], None)
        w._said = lambda inputs, speakers: []
        with patch.object(rd, "session_hours", return_value=1.0):
            first = await w._given_for(1, "c", job, inputs)
            again = await w._given_for(1, "c", job, inputs)
            self.assertEqual(len(calls), 1)  # kept for the job while it runs
            self.assertIs(first, again)
            self.assertEqual(w._heard[self.KEY], "Mira: I draw.")

            w._forget_material(self.KEY)  # what a wait does
            self.assertEqual((w._given, w._heard), ({}, {}))
            self.assertIn(self.KEY, w._beats_instead)  # the job's other reads stay
            back = await w._given_for(1, "c", job, inputs)
            self.assertEqual(len(calls), 2)  # read again, from the stores
            self.assertEqual(back.who_and_what, given.who_and_what)
            self.assertEqual(w._heard[self.KEY], "Mira: I draw.")

    async def test_no_transcript_leaves_none_to_read_after_a_wait(self) -> None:
        w: Any = self.worker()
        job: Any = SimpleNamespace(beats="[]", speakers=[], session_at=100)
        inputs: Any = SimpleNamespace(hours=None, importance=None)

        async def context(*_: object) -> tuple[str, rd.Given]:
            return "", rd.Given()

        w._context_for = context
        w._parts = lambda job, said: ([], [], None)
        w._said = lambda inputs, speakers: []
        w._heard[self.KEY] = "heard before the wait"  # so the pop below has something to remove
        with patch.object(rd, "session_hours", return_value=1.0):
            await w._given_for(1, "c", job, inputs)
        self.assertNotIn(self.KEY, w._heard)

    async def test_forgetting_the_reads_drops_the_sheet_too(self) -> None:
        w: Any = self.worker()
        w._sheets[self.KEY] = ("a beat",)
        w._forget_reads(self.KEY)
        self.assertNotIn(self.KEY, w._sheets)


class TheEarlierCopy(unittest.TestCase):
    """A copy of an earlier recap is counted as advisory and never fails a draft; a copy of the
    table's own words still does (#2965 item 2)."""

    EARLIER = "The dark forest lay silent under a pale and endless moon."
    TRANSCRIPT = "We talked about the weather and the bridge repairs at the mill."

    def test_a_copy_of_an_earlier_recap_is_advisory_only(self) -> None:
        advisory: list[str] = []
        prose = rd.Prose("They walked where the dark forest lay silent under the rain.", ())
        problems = rd.check_prose(
            prose,
            beats=(),
            transcript=self.TRANSCRIPT,
            allowed_names=[],
            secret_names=[],
            story=[self.EARLIER],
            advisory=advisory,
        )
        self.assertFalse(any("word for word" in p for p in problems), problems)
        self.assertEqual(advisory, ["earlier"])

    def test_a_copy_of_the_table_still_fails_the_draft(self) -> None:
        advisory: list[str] = []
        said = "the bridge repairs at the mill were slow"
        prose = rd.Prose(f"They talked about {said} that week.", ())
        problems = rd.check_prose(
            prose,
            beats=(),
            transcript=f"We talked about {said}.",
            allowed_names=[],
            secret_names=[],
            advisory=advisory,
        )
        self.assertTrue(any("word for word" in p for p in problems), problems)
        self.assertEqual(advisory, [])


class TheContext(unittest.TestCase):
    """The pieces the writer is given beside the beats (#2950, #2954)."""

    def test_the_oldest_recaps_are_dropped_whole_to_fit(self) -> None:
        recaps = ["one " * 10, "two " * 10, "three " * 10]
        kept = rd.trim_story(recaps, 100)
        self.assertEqual(kept, [" ".join(r.split()) for r in recaps[1:]])  # the oldest went
        self.assertEqual(rd.trim_story(recaps, 5), [])  # none fits: none, never half of one
        self.assertEqual(len(rd.trim_story(recaps, 10_000)), 3)

    def test_a_thread_naming_a_secret_never_reaches_the_writer(self) -> None:
        got = rd.open_threads(
            ["Find the lost crown", "What Zarathul wants"], ["Mira"], ["Zarathul"]
        )
        self.assertEqual(got, ("Find the lost crown",))

    def test_an_earlier_recap_naming_a_secret_is_not_given(self) -> None:
        self.assertTrue(names_a_secret("Lord Varek rode on.", ["Lord Varek"], ["Mira"]))

    def test_the_prompt_has_no_paragraph_or_per_beat_counts(self) -> None:
        self.assertNotIn("{paragraphs}", rd.PROSE_SYSTEM)
        self.assertNotIn("{per_beat}", rd.PROSE_SYSTEM)

    def test_a_name_only_the_sheet_or_an_earlier_recap_gives_passes_the_finish_check(self) -> None:
        def run(**given: Any) -> list[str]:
            _, problems = rd.check_finish(
                ("Gate of Kel-Marrow", ["[2] Mira reached Kel-Marrow."]),
                beats=[TheMessage.BEAT, Beat(2, "Mira arrived.", ("Mira",), (2,), "event")],
                summary="Mira arrived.",
                allowed_names=["Mira"],
                secret_names=[],
                **given,
            )
            return problems

        self.assertTrue(run())  # no one gave the name: refused as before
        self.assertEqual(run(who_and_what=("Kel-Marrow: a gate the party met",)), [])
        self.assertEqual(run(story=("Earlier the party saw Kel-Marrow.",)), [])

    def test_a_name_close_to_a_secret_is_still_refused(self) -> None:
        prose = rd.Prose("《Tense》 Mira met Silas Vane at the gate.", ())
        _, refused = rd.respelled(
            prose,
            beats=[TheMessage.BEAT],
            allowed_names=["Mira", "Greywater Keep"],
            secret_names=["Silas Vain"],
        )
        self.assertTrue(refused)

    def test_the_sheet_holds_only_what_the_players_know(self) -> None:
        got = rd.sheet_lines(
            [
                ("Oskar: a smith the party met", True),
                ("Zarathul: the hidden lord", True),
                ("Brann: the DM's own note", False),
                ("Oskar: a smith the party met", True),
            ],
            ["Oskar", "Brann"],
            ["Zarathul"],
        )
        self.assertEqual(got, ("Oskar: a smith the party met",))

    def test_plotbot_reads_the_newest_earlier_recaps_that_fit(self) -> None:
        old, new = "The old tale. " * 600, "The newer tale."
        message = cr.plot_message([TheMessage.BEAT], [], "", ["A sentence."], [old, new])
        self.assertIn("<story_so_far>", message)
        self.assertIn(new, message)
        self.assertNotIn("The old tale.", message)  # too long to fit whole: left out


class TheRun(unittest.IsolatedAsyncioTestCase):
    async def run_it(self, **kw: Any) -> tuple[Any, fx.Canned, fx.Canned]:
        fast, careful = happy(), fx.Canned()
        out = await run.run_text(
            inputs(lines=with_extras(), **kw), fast, careful, models=("m-fast", "m-careful"),
        )  # fmt: skip
        return out, fast, careful

    async def test_the_writer_gets_cleaned_lines_and_nothing_raw_or_private(self) -> None:
        out, fast, careful = await self.run_it(threads=("The Silver Lock",))
        self.assertEqual(out.job.state, "ready")
        prose = next(m for m in fast.sent if "<ending>" in m)
        self.assertIn("CLEANEDLINE Mira draws", prose)
        self.assertNotIn("RAWHEARD", prose)  # the cleaned words, never the raw ones
        self.assertNotIn("SIDEBARQ", prose)  # never the DM sidebar or a table question
        self.assertNotIn("OFFTOPICCHAT", prose)  # a line left out as off-topic stays out
        self.assertIn("<beats>", prose)  # the beat sheet is still the foundation
        self.assertIn("The Silver Lock", prose)  # an open thread
        self.assertIn("DM: ", prose)
        # the fact check reads the same lines as support
        self.assertTrue(any("<transcript>" in m for m in careful.fact_sent))

    async def test_a_speaker_is_a_character_or_a_number_never_a_name(self) -> None:
        _, fast, _ = await self.run_it(party={fx.ALICE: ("Mira",)})
        prose = next(m for m in fast.sent if "<ending>" in m)
        self.assertIn("Mira: ", prose)  # Alice plays Mira
        self.assertIn("Player 2: ", prose)  # Bob has no character; he spoke second

    async def test_too_long_for_the_writers_model_is_left_out_and_the_beats_still_go(self) -> None:
        with patch.object(pl, "TRANSCRIPT_SHARE", 0.00001):
            out, fast, careful = await self.run_it()
        self.assertEqual(out.job.state, "ready")
        prose = next(m for m in fast.sent if "<ending>" in m)
        self.assertNotIn("<transcript>", prose)  # (the beat-finding call keeps its own lines)
        self.assertIn("<beats>", prose)
        self.assertTrue(all("<transcript>" not in m for m in careful.fact_sent))

    async def test_the_limit_is_the_smaller_of_the_writers_and_the_checkers_model(self) -> None:
        sizes = {"m-writer": 1_000_000, "m-careful": 10_000}
        with patch.object(pl, "max_input", lambda model: sizes[model]):
            worker = pl.RecapWorker.__new__(pl.RecapWorker)
            worker._creative = SimpleNamespace(model="m-writer")
            worker._careful = SimpleNamespace(model="m-careful")
            self.assertEqual(worker._transcript_limit(), int(10_000 * pl.TRANSCRIPT_SHARE))

    async def test_a_name_outside_the_names_list_is_still_refused(self) -> None:
        # The writer may not use a secret name even when the transcript holds it: the draft check
        # is the same whatever the writer read.
        out, _, _ = await self.run_it(secret_names=["Mira"], allowed_names=["Oskar"])
        self.assertNotEqual(out.job.state, "ready")


class TheSetting(unittest.TestCase):
    BASE: ClassVar[dict[str, str]] = {
        "DISCORD_TOKEN": "x" * 60,
        "EARS_SHARED_SECRET": "s",
        "DATABASE_URL": "postgres://x",
    }

    def test_it_defaults_to_the_beats_and_accepts_the_transcript(self) -> None:
        self.assertEqual(load_settings(self.BASE).recap_writer_reads, "beats")
        got = load_settings({**self.BASE, "RECAP_WRITER_READS": "Transcript"})
        self.assertEqual(got.recap_writer_reads, "transcript")

    def test_anything_else_stops_start_up_naming_the_setting(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_settings({**self.BASE, "RECAP_WRITER_READS": "everything"})
        self.assertIn("RECAP_WRITER_READS", str(caught.exception))


class FakeAccepted:
    """The accepted recaps of a campaign, as `newest` gives them (any session, the caller
    filters by `before`), or an error when the store is down."""

    def __init__(self, rows: list[SimpleNamespace], error: bool = False) -> None:
        self.rows, self.error = rows, error

    async def newest(self, guild_id: int, campaign_id: str, *, before: int, last: int) -> list[Any]:
        if self.error:
            raise RuntimeError("the store is down")
        return self.rows


class FakeNotes:
    """Session notes whose transcript is gone, as `all` gives them."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def all(
        self, guild_id: int, campaign_id: str, *, before: int, last: int, transcript_gone: bool
    ) -> list[Any]:
        return self.rows


class TheStoryAsTold(unittest.IsolatedAsyncioTestCase):
    """The earlier recaps the writer is given (#2965 item 7): only earlier sessions, with their
    cues and fixed lines taken out, and nothing when the store cannot be read (#2954)."""

    NOW = 5000
    EARLIER = (
        "Previously, on the road…\n\n《sad》Mira ((soft)) rode on through the rain. "
        "The bridge held.\n\nAnd so we continue along the road of Mira."
    )

    async def test_only_earlier_sessions_are_told_oldest_first(self) -> None:
        rows = [
            SimpleNamespace(session_at=6000, summary="A later recap."),
            SimpleNamespace(session_at=1000, summary="The first recap."),
            SimpleNamespace(session_at=self.NOW, summary="This session."),
            SimpleNamespace(session_at=3000, summary="The second recap."),
        ]
        told = await story_so_far(FakeAccepted(rows), 1, "c", self.NOW)
        self.assertEqual(told, ("The first recap.", "The second recap."))

    async def test_cues_and_fixed_lines_are_not_given_to_the_writer(self) -> None:
        told = await story_so_far(
            FakeAccepted([SimpleNamespace(session_at=1000, summary=self.EARLIER)]), 1, "c", self.NOW
        )
        self.assertEqual(len(told), 1)
        text = told[0]
        for taken in ("《", "((", "Previously", "And so we continue"):
            self.assertNotIn(taken, text)
        self.assertIn("Mira rode on through the rain. The bridge held.", text)

    async def test_a_store_that_fails_gives_no_story_and_no_error(self) -> None:
        told = await story_so_far(FakeAccepted([], error=True), 1, "c", self.NOW)
        self.assertEqual(told, ())

    async def test_notes_stand_in_for_a_session_with_no_accepted_recap(self) -> None:
        beat = SimpleNamespace(what="Mira found the bridge.")
        notes = FakeNotes([SimpleNamespace(session_at=1000, sheet=lambda: [beat])])
        told = await story_so_far(FakeAccepted([]), 1, "c", self.NOW, notes)
        self.assertEqual(len(told), 1)
        self.assertIn("Mira found the bridge.", told[0])


if __name__ == "__main__":
    unittest.main()


class TheReviseMessage(unittest.TestCase):
    """The revision is written from the same context as the first draft (#2965 item 7): the
    transcript, the sheet, the open threads and the earlier recaps all reach the message."""

    def test_the_message_carries_the_transcript_the_sheet_the_threads_and_the_story(self) -> None:
        message = rd.prose_message(
            [TheMessage.BEAT],
            "",
            [],
            4,
            transcript="Mira: I draw.",
            who_and_what=("Mira: player character",),
            threads=("the bridge toll",),
            story=("The old tale.",),
        )
        for tag in ("<transcript>", "<who_and_what>", "<threads>", "<story_so_far>"):
            self.assertIn(tag, message)
        for text in ("Mira: I draw.", "Mira: player character", "the bridge toll", "The old tale."):
            self.assertIn(text, message)


class ThePlotBoundary(unittest.TestCase):
    """PlotBot's review reads the newest earlier recaps that fit whole, and no story block when
    none fit (#2954)."""

    def test_the_newest_recap_that_fits_is_kept_and_the_older_one_is_not(self) -> None:
        old, new = "A" * 40, "B" * 40
        with patch.object(cr, "STORY_CHARS", 50):
            message = cr.plot_message([TheMessage.BEAT], [], "", ["A sentence."], [old, new])
        self.assertIn(new, message)
        self.assertNotIn(old, message)

    def test_a_recap_too_long_for_the_limit_gives_no_story_block(self) -> None:
        with patch.object(cr, "STORY_CHARS", 10):
            message = cr.plot_message([TheMessage.BEAT], [], "", ["A sentence."], ["B" * 40])
        self.assertNotIn("<story_so_far>", message)

    def test_no_earlier_recap_gives_no_story_block(self) -> None:
        message = cr.plot_message([TheMessage.BEAT], [], "", ["A sentence."], [])
        self.assertNotIn("<story_so_far>", message)


class TheContextBudget(unittest.IsolatedAsyncioTestCase):
    """The writer's context is cut to the model's room (#2954): an over-long transcript is left
    out once for the job, and the earlier recaps are kept whole, newest first, within what is
    left (#2965 item 7)."""

    def worker(self, notes: list[str], limit_tokens: int) -> Any:
        w: Any = pl.RecapWorker.__new__(pl.RecapWorker)
        w._beats_instead = set()
        w._sheets = {}
        w._on_note = notes.append
        w._transcript_limit = lambda: limit_tokens

        async def sheet(*_: object) -> tuple[str, ...]:
            return ("Mira: player character",)  # 21 characters

        w._who_and_what = sheet
        return w

    def inputs(self, story: tuple[str, ...] = ()) -> pl.Inputs:
        return pl.Inputs(
            lines=[],
            dm_ids={1},
            allowed_names=[],
            secret_names=[],
            previous="",
            day=4,
            story=story,
        )

    async def test_an_over_long_transcript_is_left_out_once_and_retries_do_not_repeat_it(
        self,
    ) -> None:
        notes: list[str] = []
        w = self.worker(notes, limit_tokens=10)  # 30 characters of room
        job = SimpleNamespace(session_at=100)
        play = [SimpleNamespace(user_id=2, text="x" * 100)]
        block, _ = await w._context_for(1, "c", job, self.inputs(), play, ())
        self.assertEqual(block, "")
        self.assertIn((1, "c", 100), w._beats_instead)
        self.assertEqual(len(notes), 1)
        again, _ = await w._context_for(1, "c", job, self.inputs(), play, ())
        self.assertEqual(again, "")
        self.assertEqual(len(notes), 1)  # the retry says nothing new to the DM

    async def test_earlier_recaps_that_do_not_fit_the_room_left_are_dropped_whole(self) -> None:
        w = self.worker([], limit_tokens=10)  # 30 characters, 21 used by the sheet: 9 left
        job = SimpleNamespace(session_at=100)
        _, given = await w._context_for(1, "c", job, self.inputs(("A" * 50, "B" * 50)), [], ())
        self.assertEqual(given.story, ())

    async def test_only_the_newest_earlier_recap_that_fits_is_kept(self) -> None:
        w = self.worker([], limit_tokens=20)  # 60 characters, 21 used by the sheet: 39 left
        job = SimpleNamespace(session_at=100)
        _, given = await w._context_for(1, "c", job, self.inputs(("A" * 30, "B" * 30)), [], ())
        self.assertEqual(given.story, ("B" * 30,))
