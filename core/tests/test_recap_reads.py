"""What the recap writer reads (#2590, RECAP_WRITER_READS): the beat sheet (today) or the session's
cleaned transcript with PlotBot's guide. Made-up lines; a fake AI, nothing is paid for."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar
from unittest.mock import patch

from dmbot.config import ConfigError, load_settings
from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.beats import Beat
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
    def test_every_rule_that_changes_still_matches_the_prompt(self) -> None:
        for old, _new in rd.TRANSCRIPT_RULES:
            self.assertIn(old, rd.PROSE_SYSTEM)

    def test_the_transcript_writer_is_told_to_use_the_guide_and_only_what_was_said(self) -> None:
        text = rd.prose_system(None, reads=rd.TRANSCRIPT)
        self.assertIn("cleaned TRANSCRIPT", text)
        self.assertIn("GUIDE from PlotBot", text)
        self.assertIn("Write only from what the transcript says", text)
        self.assertIn("even one that the transcript has", text)  # a name outside <names>
        self.assertNotIn("numbered BEAT SHEET", text)
        self.assertIn("numbered BEAT SHEET", rd.prose_system(None))

    def test_the_fact_check_counts_the_transcript_as_support(self) -> None:
        text = rd.FACTS_SYSTEM_TRANSCRIPT
        self.assertIn("supported when the beats or the transcript say it", text)
        self.assertIn("counts as stated", text)
        self.assertNotIn("transcript", rd.FACTS_SYSTEM)


class TheMessage(unittest.TestCase):
    BEAT = Beat(1, "Mira drew her blade.", ("Mira",), (1,), "event", note="a turning point")

    def test_the_transcript_and_guide_replace_the_beat_sheet(self) -> None:
        guide = rd.guide_text(
            [self.BEAT], {"Mira": 5.0, "Oskar": 2.0, "Secret One": 9.0}, ["The Silver Lock"],
            allowed_names=["Mira", "Oskar", "The Silver Lock"],
        )  # fmt: skip
        message = rd.prose_message(
            [self.BEAT], "", ["Mira"], None,
            transcript=rd.transcript_block([("DM", "A door."), ("Mira", "I open it.")]),
            guide=guide,
        )  # fmt: skip
        self.assertIn("<guide>", message)
        self.assertIn("<transcript>\nDM: A door.\nMira: I open it.\n</transcript>", message)
        self.assertNotIn("<beats>", message)
        self.assertIn("<ending>", message)
        self.assertIn("Checklist (tell these):", guide)
        self.assertIn("Matters:", guide)  # PlotBot's notes ride in the checklist
        self.assertIn("- The Silver Lock", guide)
        self.assertIn("Mira; Oskar", guide)  # most connected first
        self.assertNotIn("Secret One", guide)  # never a name the writer may not use

    def test_the_default_message_is_unchanged(self) -> None:
        message = rd.prose_message([self.BEAT], "", ["Mira"], None)
        self.assertIn("<beats>", message)
        self.assertNotIn("<transcript>", message)

    def test_the_fact_check_message_carries_the_transcript(self) -> None:
        plain = rd.facts_message([self.BEAT], "Mira drew her blade.")
        heard = rd.facts_message([self.BEAT], "Mira drew her blade.", transcript="Mira: I draw.")
        self.assertNotIn("<transcript>", plain)
        self.assertIn("<transcript>\nMira: I draw.\n</transcript>", heard)


class TheRun(unittest.IsolatedAsyncioTestCase):
    async def run_it(self, reads: str, **kw: Any) -> tuple[Any, fx.Canned, fx.Canned]:
        fast, careful = happy(), fx.Canned()
        out = await run.run_text(
            inputs(lines=with_extras(), **kw), fast, careful, models=("m-fast", "m-careful"),
            reads=reads,
        )  # fmt: skip
        return out, fast, careful

    async def test_the_default_reads_the_beats_only(self) -> None:
        out, fast, _ = await self.run_it("beats")
        self.assertEqual(out.job.state, "ready")
        self.assertTrue(all("<guide>" not in m for m in fast.sent))

    async def test_the_transcript_writer_gets_cleaned_lines_and_nothing_raw_or_private(
        self,
    ) -> None:
        out, fast, careful = await self.run_it("transcript", threads=("The Silver Lock",))
        self.assertEqual(out.job.state, "ready")
        prose = next(m for m in fast.sent if "<ending>" in m)
        self.assertIn("CLEANEDLINE Mira draws", prose)
        self.assertNotIn("RAWHEARD", prose)  # the cleaned words, never the raw ones
        self.assertNotIn("SIDEBARQ", prose)  # never the DM sidebar or a table question
        self.assertNotIn("OFFTOPICCHAT", prose)  # a line left out as off-topic stays out
        self.assertIn("<guide>", prose)
        self.assertIn("DM: ", prose)
        self.assertNotIn("<beats>", prose)
        # the fact check reads the same lines as support
        self.assertTrue(any("<transcript>" in m for m in careful.fact_sent))

    async def test_a_speaker_is_a_character_or_a_number_never_a_name(self) -> None:
        _, fast, _ = await self.run_it("transcript", party={fx.ALICE: ("Mira",)})
        prose = next(m for m in fast.sent if "<ending>" in m)
        self.assertIn("Mira: ", prose)  # Alice plays Mira
        self.assertIn("Player 2: ", prose)  # Bob has no character; he spoke second

    async def test_too_long_for_the_writers_model_falls_back_to_the_beats_once(self) -> None:
        with patch.object(pl, "TRANSCRIPT_SHARE", 0.00001):
            out, fast, careful = await self.run_it("transcript")
        self.assertEqual(out.job.state, "ready")
        self.assertTrue(all("<guide>" not in m for m in fast.sent))
        self.assertTrue(all("<transcript>" not in m for m in careful.fact_sent))

    async def test_a_name_outside_the_names_list_is_still_refused(self) -> None:
        # The writer may not use a secret name even when the transcript holds it: the draft check
        # is the same whatever the writer read.
        out, _, _ = await self.run_it("transcript", secret_names=["Mira"], allowed_names=["Oskar"])
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
