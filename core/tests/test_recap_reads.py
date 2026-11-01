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
        worker._forget_reads(key)
        self.assertEqual((worker._heard, worker._given, worker._beats_instead), ({}, {}, set()))

    def test_the_fact_check_message_carries_the_transcript(self) -> None:
        plain = rd.facts_message([self.BEAT], "Mira drew her blade.")
        self.assertNotIn("<transcript>", plain)


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


if __name__ == "__main__":
    unittest.main()
