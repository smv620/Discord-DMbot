"""Cost per table-hour (#945): the arithmetic, the prices on file, and the measuring tool on
small made-up sessions. No network and no key."""

import argparse
import asyncio
import io
import math
import tempfile
import unittest
import wave
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from dmbot.devtools.costs import measure, model, prices, report
from dmbot.devtools.replay.script import parse_script
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

HAIKU = "claude-haiku-4-5-20251001"


def tone(seconds: float) -> bytes:
    n = int(SAMPLE_RATE * seconds)
    wave = (np.sin(np.arange(n) * 2 * math.pi * 440 / SAMPLE_RATE) * 12_000).astype(np.int16)
    return wave.tobytes()


def quiet(seconds: float) -> bytes:
    return bytes(int(SAMPLE_RATE * seconds) * BYTES_PER_SAMPLE)


class Prices(unittest.TestCase):
    def test_every_price_says_where_and_when(self) -> None:
        every = [prices.DEEPGRAM_PRERECORDED, prices.DEEPGRAM_STREAMING]
        every += [p for pair in prices.ANTHROPIC_PER_MILLION.values() for p in pair]
        for price in every:
            self.assertGreater(price.dollars, 0)
            self.assertTrue(price.source.startswith("https://"))
            self.assertRegex(price.checked, r"^\d{4}-\d{2}-\d{2}$")

    def test_a_model_nobody_checked_is_refused_not_guessed(self) -> None:
        with self.assertRaises(prices.UnknownPrice) as caught:
            prices.price_of("claude-some-new-model")
        self.assertIn("claude-some-new-model", str(caught.exception))

    def test_the_model_core_uses_has_a_price(self) -> None:
        from dmbot.ai import DEFAULT_MODELS

        prices.price_of(DEFAULT_MODELS.fast)


class Arithmetic(unittest.TestCase):
    def test_speech_to_text_is_the_price_per_minute(self) -> None:
        self.assertAlmostEqual(model.stt_dollars(60), 60 * 0.0043)
        self.assertEqual(model.stt_dollars(0), 0)

    def test_ai_is_priced_per_million_tokens_in_and_out(self) -> None:
        usage = model.Usage(3, 2_000_000, 1_000_000, HAIKU)
        self.assertAlmostEqual(model.ai_dollars(usage), 2 * 1.0 + 1 * 5.0)

    def test_usage_adds_and_scales(self) -> None:
        a, b = model.Usage(1, 100, 10), model.Usage(2, 300, 30)
        total = a + b
        self.assertEqual((total.calls, total.input_tokens, total.output_tokens), (3, 400, 40))
        half = total.scaled(0.5)
        self.assertEqual((half.calls, half.input_tokens), (1.5, 200))

    def test_tokens_are_estimated_by_size_and_rounded_up(self) -> None:
        self.assertEqual(model.estimate_tokens(""), 0)
        self.assertEqual(model.estimate_tokens("a" * 7), 2)
        self.assertEqual(model.estimate_tokens("a" * 8), 3)

    def sessions(self) -> list[model.Session]:
        filter_use = model.Usage(10, 4_000, 300, HAIKU)
        return [
            model.Session("one", 600, 300, 20, {model.FILTER: filter_use}),
            model.Session("two", 600, 300, 20, {model.FILTER: filter_use}),
        ]

    def test_a_table_hour_adds_speech_filter_and_sidebar(self) -> None:
        sidebar = model.Usage(1, 1_000, 50, HAIKU)
        low, typical, high = model.table_hour(self.sessions(), sidebar=sidebar)
        self.assertEqual([c.case for c in (low, typical, high)], ["low", "typical", "high"])
        # 10 speech-minutes measured, 20 filter calls: 2 calls per speech-minute.
        minutes = 36.0
        filter_calls = 2 * minutes
        per_call = model.ai_dollars(model.Usage(1, 400, 30, HAIKU))  # 4,000 and 300 over 10 calls
        self.assertAlmostEqual(low.ai[model.FILTER], filter_calls * per_call)
        self.assertAlmostEqual(low.stt, minutes * 0.0043)
        six = model.ai_dollars(model.Usage(6, 6_000, 300, HAIKU))
        self.assertAlmostEqual(low.ai[model.SIDEBAR], six)
        self.assertAlmostEqual(low.total, low.stt + sum(low.ai.values()))
        self.assertLess(low.total, typical.total)
        self.assertLess(typical.total, high.total)
        self.assertIsNone(low.hosting)

    def test_hosting_is_added_only_when_both_numbers_are_given(self) -> None:
        sidebar = model.Usage(1, 1_000, 50, HAIKU)
        none = model.table_hour(self.sessions(), sidebar=sidebar, hosting_monthly=20)
        self.assertIsNone(none[0].hosting)
        both = model.table_hour(
            self.sessions(), sidebar=sidebar, hosting_monthly=20, table_hours_per_month=100
        )
        self.assertAlmostEqual(both[0].hosting or 0, 0.20)
        self.assertAlmostEqual(both[0].total - both[0].total_without_hosting, 0.20)

    def test_no_speech_cannot_be_priced(self) -> None:
        with self.assertRaises(ValueError):
            model.table_hour([model.Session("empty", 10, 0, 0)], sidebar=model.Usage())


class Tool(unittest.TestCase):
    def test_pieces_are_cut_as_core_cuts_them_and_short_ones_are_not_sent(self) -> None:
        pcm = quiet(0.5) + tone(1.0) + quiet(2.0) + tone(0.1) + quiet(2.0) + tone(0.5) + quiet(1.5)
        heard = measure.utterances(pcm)
        self.assertEqual(len(heard), 3)
        seconds = [round(u.duration_s, 1) for u in heard]
        self.assertEqual(seconds[0], 1.0)
        self.assertLess(seconds[1], 0.25)  # counted, but never sent to speech-to-text
        lines = [measure.Line(u.start_ms / 1000, u.duration_s, "word " * 3) for u in heard]
        self.assertEqual(len(measure.sent_lines(lines)), 2)

    def test_a_long_piece_is_cut_at_fifteen_seconds(self) -> None:
        heard = measure.utterances(quiet(0.2) + tone(20.0) + quiet(2.0))
        self.assertEqual([round(u.duration_s) for u in heard], [15, 5])

    def test_words_are_dealt_out_by_how_long_each_piece_lasts(self) -> None:
        script = parse_script("## Part 1\n\n**[DM]:** one two three four five six seven eight\n")
        lines = measure.words_for(script, [1.0, 3.0])
        self.assertEqual(lines, ["one two", "three four five six seven eight"])

    def test_a_script_nobody_recorded_is_timed_by_its_words(self) -> None:
        script = parse_script("## Part 1\n\n**[DM]:** " + "Hello there friend. " * 5 + "\n")
        session = measure.session_from_script(script)
        self.assertFalse(session.measured_audio)
        self.assertEqual(session.lines, 5)
        # 3 words a sentence at 2.5 words a second, with a second between sentences
        self.assertAlmostEqual(session.speech_s, 5 * 3 / 2.5)
        self.assertAlmostEqual(session.table_s, 5 * 3 / 2.5 + 4 * 1.0)

    def test_the_filter_asks_in_windows_and_skips_plain_game_talk(self) -> None:
        plain = "I roll a d20 for initiative, two attacks"  # dice: plainly the game
        odd = [
            measure.Line(float(i), 1.0, f"did you see the news about the election {i}")
            for i in range(13)
        ]
        use = measure.filter_usage([measure.Line(0.0, 1.0, plain), *odd])
        # 13 lines the filter must ask about: two full windows of six, then one left over
        self.assertEqual(use.calls, 3)
        self.assertGreater(use.input_tokens, 3 * model.estimate_tokens("x"))
        self.assertEqual(use.output_tokens, 13 * measure.OUTPUT_TOKENS_PER_LABEL)

    def test_the_window_also_closes_after_twenty_seconds(self) -> None:
        slow = [measure.Line(i * 30.0, 1.0, f"what are we eating tonight {i}") for i in range(3)]
        self.assertEqual(measure.filter_usage(slow).calls, 3)

    def test_the_stand_in_ai_counts_what_it_is_sent(self) -> None:
        ai = measure.RecordingAI(["1 game"])

        async def go() -> None:
            await ai.complete("system words", "user words", max_tokens=100)
            await ai.complete("system words", "user words", max_tokens=100)

        asyncio.run(go())
        self.assertEqual(ai.calls, 2)
        self.assertEqual(ai.usage().calls, 2)
        self.assertGreater(ai.input_tokens, 0)

    def test_the_sidebar_is_measured_on_its_seventeen_questions(self) -> None:
        usage, questions = asyncio.run(measure.sidebar_usage())
        self.assertEqual(questions, len(measure.CASES_MODULE().CASES))
        self.assertGreaterEqual(questions, 17)
        self.assertGreaterEqual(usage.calls, 1.0)  # some questions needed a second try
        self.assertLess(usage.calls, 2.0)
        self.assertGreater(usage.input_tokens, 300)
        self.assertTrue(usage.estimated)


class FromARecording(unittest.TestCase):
    def test_pieces_too_short_to_send_are_counted_by_core_but_not_paid_for(self) -> None:
        pcm = quiet(0.5) + tone(2.0) + quiet(2.0) + tone(0.1) + quiet(2.0) + tone(1.0) + quiet(1.5)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(BYTES_PER_SAMPLE)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        script = parse_script("## Part 1\n\n**[DM]:** " + "word " * 30 + "\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "made-up.wav"
            path.write_bytes(buf.getvalue())
            session = measure.session_from_recording(path, script)
        self.assertEqual(session.lines, 2)  # the 0.1 s blip is not sent
        self.assertAlmostEqual(session.speech_s, 3.0, delta=0.1)
        self.assertGreater(session.table_s, session.speech_s)
        self.assertTrue(session.measured_audio)

    def test_the_ceiling_for_the_audio_check_is_one_ask_a_minute(self) -> None:
        ceiling = measure.audio_check_ceiling()
        self.assertEqual(ceiling.calls, 60)
        self.assertGreater(ceiling.input_tokens, 0)
        self.assertLess(model.ai_dollars(ceiling), 0.05)


class Report(unittest.TestCase):
    def render(self, **kw: float | None) -> str:
        filter_use = model.Usage(10, 4_000, 300, HAIKU)
        sessions = [model.Session("one", 600, 300, 20, {model.FILTER: filter_use})]
        sidebar = model.Usage(1.2, 860, 50, HAIKU)
        cases = model.table_hour(sessions, sidebar=sidebar, **kw)  # type: ignore[arg-type]
        args = argparse.Namespace()
        return report.render(sessions, sidebar, 17, cases, args, measure.audio_check_ceiling())

    def test_it_says_what_it_measured_and_what_it_assumed(self) -> None:
        text = self.render()
        for needed in (
            "Cost per table-hour",
            "| low | 36 |",
            "| typical | 48 |",
            "| high | 60 |",
            "not included",
            "https://deepgram.com/pricing",
            "2026-10-09",
            "What run 8 should confirm",
            "estimates",
            "No AI at all",
            "Audio check ceiling",
        ):
            self.assertIn(needed, text)

    def test_the_cases_are_plans_assumption_not_a_claim_about_the_twin(self) -> None:
        text = self.render()
        self.assertIn("that PLAN.md assumed before", text)
        self.assertIn("60 is a ceiling", text)

    def test_the_filter_row_is_the_measured_ratio_times_the_case(self) -> None:
        # 10 calls, 4,000 tokens in and 300 out over 5 speech-minutes: 2 calls a minute.
        text = self.render()
        per_minute = model.ai_dollars(model.Usage(10, 4_000, 300, HAIKU)) / 5
        self.assertIn(f"| {report.money(per_minute * 36)} |", text)
        self.assertIn(f"| {report.money(per_minute * 60)} |", text)

    def test_the_report_and_the_tool_count_the_filters_answers_the_same_way(self) -> None:
        self.assertEqual(report.OUTPUT_TOKENS_PER_LABEL, measure.OUTPUT_TOKENS_PER_LABEL)

    def test_usage_of_two_models_is_not_added(self) -> None:
        with self.assertRaises(ValueError):
            model.Usage(1, 1, 1, HAIKU) + model.Usage(1, 1, 1, "another-model")

    def test_hosting_shows_when_given(self) -> None:
        text = self.render(hosting_monthly=30.0, table_hours_per_month=100.0)
        self.assertIn("$0.300", text)
        self.assertNotIn("not included", text)


def _args(argv: Sequence[str]) -> argparse.Namespace:
    return measure.parse_args(argv)


class Command(unittest.TestCase):
    def test_outside_a_checkout_it_says_so(self) -> None:
        from unittest.mock import patch

        with patch.object(measure, "TEST_SCRIPTS", Path("/nonexistent/test-scripts")):
            self.assertEqual(measure.main([]), 2)

    def test_flags(self) -> None:
        args = _args(["--hosting-monthly", "25", "--table-hours-per-month", "80"])
        self.assertEqual((args.hosting_monthly, args.table_hours_per_month), (25.0, 80.0))

    def test_the_repos_own_sessions_measure_without_a_key(self) -> None:
        sessions = measure.measure_sessions()
        self.assertGreaterEqual(len(sessions), 3)
        for session in sessions:
            self.assertGreater(session.speech_s, 0)
            self.assertGreater(session.table_s, session.speech_s * 0.5)


if __name__ == "__main__":
    unittest.main()
