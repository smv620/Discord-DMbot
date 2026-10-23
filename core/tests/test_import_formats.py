"""The transcript reader on the shapes real tools write (#2302): the name in square brackets, a
byte-order mark, WebVTT voice tags, and the reason a file cannot be read. Every line is invented."""

from __future__ import annotations

import contextlib
import time
import unittest

from dmbot.transcript import importer
from dmbot.web import imports
from tests import speed

BOM = "﻿"


def speakers(text: str) -> list[str]:
    return [u.speaker for u in importer.parse(text).utterances]


class Brackets(unittest.TestCase):
    DOC = (
        BOM
        + "[Rowan]:  We open the door.\n\n"
        + "[Kai]:  Then I listen at it.\n"
        + " and hold my breath.\n\n"
        + "[Rowan]:  Nothing.\n"
        + "[Kai (kai_v)]: I draw my sword.\n"
    )

    def test_a_bracketed_name_with_a_bom_blank_lines_and_a_continuation(self) -> None:
        got = importer.parse(self.DOC)
        self.assertEqual(got.format, importer.PLAIN)
        self.assertEqual(
            [u.speaker for u in got.utterances], ["Rowan", "Kai", "Rowan", "Kai (kai_v)"]
        )
        self.assertEqual(got.utterances[1].text, "Then I listen at it. and hold my breath.")
        self.assertEqual(got.joined, 1)

    def test_a_clock_in_brackets_is_still_a_time_and_a_name_is_not(self) -> None:
        got = importer.parse("[0:42:10] Rowan: We wait.\n[0:42:20] Kai: We go.\n")
        self.assertEqual(
            [(u.speaker, u.at_ms) for u in got.utterances],
            [("Rowan", 2_530_000), ("Kai", 2_540_000)],
        )
        self.assertTrue(got.timed)
        # a bracketed name after a clock
        both = importer.parse("[0:00:05] [Rowan]: We wait.\n[0:00:09] [Kai]: We go.\n")
        self.assertEqual(
            [(u.speaker, u.at_ms) for u in both.utterances], [("Rowan", 5000), ("Kai", 9000)]
        )

    def test_a_bracketed_name_with_no_colon_is_read_only_when_no_line_has_one(self) -> None:
        self.assertEqual(
            speakers("[Rowan] We open the door.\n[Kai] We listen.\n"), ["Rowan", "Kai"]
        )
        # a wrapped line that starts with a note is not a speaker when the file uses colons
        text = (
            "Rowan: We open the door.\n[laughs] and then we go.\nKai: We listen.\n"
            "Rowan: Nothing.\nKai: Then onward.\nRowan: Agreed.\nKai: Good.\nRowan: Quiet now.\n"
        )
        self.assertEqual(speakers(text), ["Rowan", "Kai", "Rowan", "Kai", "Rowan", "Kai", "Rowan"])

    def test_a_bracketed_file_takes_new_turns_only_in_the_bracketed_form(self) -> None:
        text = (
            "[Rowan]: We open the door.\n"
            "[Kai]: Then I listen\n"
            "then he said: stop\n"
            "[Rowan]: Nothing.\n"
        )
        got = importer.parse(text)
        self.assertEqual([u.speaker for u in got.utterances], ["Rowan", "Kai", "Rowan"])
        self.assertEqual(got.utterances[1].text, "Then I listen then he said: stop")
        self.assertEqual(got.joined, 1)

    def test_a_space_before_the_colon_or_none_after_it_is_still_a_bracketed_name(self) -> None:
        got = importer.parse("[Rowan] : We open the door.\n[Kai]:Then I listen.\n")
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances],
            [("Rowan", "We open the door."), ("Kai", "Then I listen.")],
        )

    def test_the_no_colon_form_is_chosen_when_most_lines_are_that_form(self) -> None:
        # one `Name: words` line in five is not most of the file, so the brackets win
        text = (
            "Rowan: We open the door.\n[Kai] We listen.\n[Rowan] Nothing.\n"
            "[Kai] Onward.\n[Rowan] Agreed.\n"
        )
        self.assertEqual(speakers(text), ["Kai", "Rowan", "Kai", "Rowan"])
        self.assertEqual(importer.parse(text).skipped, 1)  # the first line was a header

    def test_dmbots_own_download_is_not_read_as_bracketed_names(self) -> None:
        text = "DMbot transcript: T, session 1\n\n[0:00:05] (Sam) {narrating}: It begins.\n"
        self.assertEqual(importer.parse(text).format, importer.DMBOT)


class Captions(unittest.TestCase):
    def test_voice_tags_name_the_speaker_with_or_without_a_closing_tag_and_a_class(self) -> None:
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n<v Rowan>We open the door.</v>\n\n"
            "00:00:03.500 --> 00:00:05.000\n<v.loud Kai>Then I listen.\n\n"
            "00:00:06.000 --> 00:00:08.000\n<v Rowan>One.</v> <v Kai>Two.</v>\n"
        )
        got = importer.parse(vtt)
        self.assertEqual(got.format, importer.VTT)
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances],
            [
                ("Rowan", "We open the door."),
                ("Kai", "Then I listen."),
                ("Rowan", "One."),
                ("Kai", "Two."),
            ],
        )

    def test_words_before_the_first_voice_tag_carry_on_the_cue_before(self) -> None:
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n<v Rowan>We open the door\n\n"
            "00:00:03.500 --> 00:00:05.000\nand step in. <v Kai>Then I listen.</v>\n"
        )
        got = importer.parse(vtt)
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances],
            [("Rowan", "We open the door and step in."), ("Kai", "Then I listen.")],
        )

    def test_a_stray_unnamed_cue_is_skipped_when_most_cues_name_a_speaker(self) -> None:
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n<v Rowan>We open the door.</v>\n\n"
            "00:00:02.000 --> 00:00:03.000\n[music]\n\n"
            "00:00:03.000 --> 00:00:04.000\n<v Kai>Then I listen.</v>\n"
        )
        with self.assertLogs("dmbot.transcript.importer", "INFO") as logged:
            got = importer.parse(vtt)
        self.assertEqual([u.speaker for u in got.utterances], ["Rowan", "Kai"])
        self.assertIn("1 unnamed cue(s) skipped of 3", logged.output[0])
        self.assertNotIn("music", logged.output[0])  # a count only

    def test_mostly_unnamed_cues_are_still_refused(self) -> None:
        vtt = (
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Rowan>One.</v>\n\n"
            "00:00:02.000 --> 00:00:03.000\n[music]\n\n"
            "00:00:03.000 --> 00:00:04.000\n[applause]\n"
        )
        with self.assertRaises(importer.TranscriptError) as caught:
            importer.parse(vtt)
        self.assertEqual(caught.exception.code, importer.NO_SPEAKER_NAMES)

    def test_a_vtt_with_a_byte_order_mark_is_still_a_vtt(self) -> None:
        vtt = BOM + "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nRowan: Hello there.\n"
        self.assertEqual(importer.parse(vtt).format, importer.VTT)
        self.assertEqual(importer.detect(vtt), importer.VTT)

    def test_a_bom_does_not_hide_the_other_formats_either(self) -> None:
        srt = BOM + "1\n00:00:01,000 --> 00:00:02,000\nRowan: Hello there.\n"
        self.assertEqual(importer.parse(srt).format, importer.SRT)
        dmbot = BOM + "DMbot transcript: T\n\n[0:00:05] (Sam): It begins.\n"
        self.assertEqual(importer.parse(dmbot).format, importer.DMBOT)

    def test_captions_with_no_names_say_so_with_their_own_code(self) -> None:
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nno one is named here\n"
        with self.assertRaises(importer.TranscriptError) as caught:
            importer.parse(vtt)
        self.assertEqual(caught.exception.code, importer.NO_SPEAKER_NAMES)
        self.assertNotIn("no one is named", str(caught.exception))  # never a word of the file

    def test_text_that_is_nothing_known_says_unknown_format_and_lists_the_formats(self) -> None:
        with self.assertRaises(importer.TranscriptError) as caught:
            importer.parse("just some words\nwith no names\n")
        self.assertEqual(caught.exception.code, importer.UNKNOWN_FORMAT)
        self.assertIn("square brackets", str(caught.exception))
        self.assertIn("captions", str(caught.exception))


class Speed(unittest.TestCase):
    """A made-up caption cue must never hold a worker thread: these patterns once backtracked for
    hours on a few dozen characters."""

    def took_under_a_second(self, cue: str) -> None:
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + cue + "\n"
        start = time.perf_counter()
        with contextlib.suppress(importer.TranscriptError):
            importer.parse(vtt)
        self.assertLess(time.perf_counter() - start, speed.allowed(1.0))

    def test_a_voice_tag_of_dots_is_read_in_linear_time(self) -> None:
        self.took_under_a_second("<v" + "." * 5000)
        self.took_under_a_second("<v" + "." * 40)

    def test_a_repeated_partial_voice_tag_is_read_in_linear_time(self) -> None:
        self.took_under_a_second("<v.a" * 500_000)  # about 2 MB

    def test_unclosed_tags_are_stripped_in_linear_time(self) -> None:
        self.took_under_a_second("<a" * 100_000)
        self.took_under_a_second("<v Ann>" + "<a" * 100_000)

    def test_the_voice_tags_that_are_real_still_match(self) -> None:
        for tag, who in (
            ("<v Ann>", "Ann"),
            ("<v.loud Kai>", "Kai"),
            ("<v.a.b Bo Smith>", "Bo Smith"),
        ):
            with self.subTest(tag):
                vtt = f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{tag}Hello there.</v>\n"
                self.assertEqual(speakers(vtt), [who])


class Names(unittest.TestCase):
    def test_txt_vtt_and_srt_names_are_accepted_and_the_format_is_told_by_the_content(self) -> None:
        plain = "Rowan: We open the door.\nKai: We listen.\n"
        for name in ("a.txt", "A.TXT", "talk-en-1.vtt", "talk.vtt.txt", "talk.srt"):
            with self.subTest(name):
                self.assertIsNone(imports.read_file(name, plain).error)

    def test_other_names_are_refused_and_the_reason_codes_reach_the_page(self) -> None:
        self.assertEqual(imports.read_file("notes.md", "Rowan: Hello there.\n").error, "not_txt")
        self.assertEqual(
            imports.read_file("a.txt", "just prose\nwith no names\n").error, "unknown_format"
        )
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nnobody is named\n"
        self.assertEqual(imports.read_file("a.vtt", vtt).error, "no_speaker_names")
        self.assertEqual(imports.read_file("a.txt", "   ").error, "empty")
