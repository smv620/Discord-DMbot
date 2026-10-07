"""Transcript downloads (#41, #125) and the unsaved-lines buffer, without Discord or a
database."""

import unittest

from dmbot.transcript import export
from dmbot.transcript.models import MAX_UNSAVED, Line, TranscriptBuffer, TranscriptSession

START = 1_700_000_000  # 2023-11-14 22:13:20 UTC
MIA, DEE = 8, 9


def session(ended: int | None = START + 2 * 3600 + 14 * 60) -> TranscriptSession:
    return TranscriptSession("a" * 32, 1, "c", START, ended, 2, (MIA, DEE), number=7)


def line(seconds: float, user: int, text: str) -> Line:
    return Line(int(START * 1000 + seconds * 1000), user, text, text)


class Render(unittest.TestCase):
    def test_lines_show_time_since_start_and_the_speaker(self) -> None:
        text = export.render(
            "Rime of the Frostmaiden",
            session(),
            [line(5, MIA, "I cast  Shield."), line(2530, DEE, "Run!")],
            {MIA: "Mia", DEE: "Dee"},
        )
        head, body = text.split("\n\n", 1)
        self.assertIn("DMbot transcript: Rime of the Frostmaiden, session 7", head)
        self.assertIn("Started 2023-11-14 22:13 UTC, ran 2 h 14 min", head)
        self.assertIn("before it fixed any names, so some names may be misheard", head)
        self.assertIn("(person) {their character}", head)
        self.assertIn("Only people who agreed were recorded", head)
        self.assertEqual(
            body.splitlines(), ["[0:00:05] (Mia): I cast Shield.", "[0:42:10] (Dee): Run!"]
        )

    def test_each_player_has_their_character(self) -> None:
        text = export.render(
            "X",
            session(),
            [line(5, MIA, "I cast Shield."), line(6, DEE, "Roll for it.")],
            {MIA: "Mia", DEE: "Dee"},
            characters={MIA: "Cerric"},
        )
        self.assertIn("[0:00:05] (Mia) {Cerric}: I cast Shield.\n", text)
        self.assertIn("[0:00:06] (Dee): Roll for it.\n", text)  # the DM plays no one

    def test_the_as_heard_file_has_what_was_heard(self) -> None:
        fixed = Line(START * 1000, MIA, "I saw Beleros", "I saw Belleros")
        text = export.render("X", session(), [fixed], {MIA: "Mia"})
        self.assertIn("(Mia): I saw Beleros\n", text)

    def test_the_cleaned_file_has_the_fixed_names(self) -> None:
        fixed = Line(START * 1000, MIA, "I saw Beleros", "I saw Belleros")
        text = export.render("X", session(), [fixed], {MIA: "Mia"}, version=export.CLEANED)
        self.assertIn("(Mia): I saw Belleros\n", text)
        self.assertIn("Cleaned: DMbot fixed the spelling of names it was sure about", text)
        self.assertNotIn("As heard:", text)
        self.assertEqual(
            export.file_name("X", session(), export.CLEANED), "x-session-7-cleaned.txt"
        )

    def test_an_unknown_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export.render("X", session(), [], {}, version="raw")

    def test_unknown_and_tricky_names_are_safe(self) -> None:
        text = export.render(
            "X", session(), [line(1, MIA, "hi"), line(2, DEE, "yo")], {DEE: "Dee‮\nevil"}
        )
        self.assertIn("[0:00:01] (Someone): hi", text)
        self.assertIn("[0:00:02] (Dee evil): yo", text)

    def test_a_running_session_says_where_it_ends(self) -> None:
        text = export.render("X", session(None), [line(65, MIA, "hi")], {MIA: "Mia"}, running=True)
        self.assertIn("still recording, so this file stops here, at 0:01:05.", text)
        self.assertNotIn(", ran ", text)

    def test_speech_before_the_start_never_shows_a_negative_time(self) -> None:
        self.assertIn(
            "[0:00:00] (Mia): early",
            export.render("X", session(), [line(-3, MIA, "early")], {MIA: "Mia"}),
        )

    def test_file_names_are_safe_everywhere(self) -> None:
        self.assertEqual(
            export.file_name("Rime of the Frostmaiden: Part 2!", session()),
            "rime-of-the-frostmaiden-part-2-session-7-as-heard.txt",
        )
        self.assertEqual(export.file_name("🐉", session()), "campaign-session-7-as-heard.txt")

    def test_durations_and_labels_in_plain_words(self) -> None:
        self.assertEqual(export.duration(30), "under a minute")
        self.assertEqual(export.duration(35 * 60), "35 min")
        self.assertEqual(export.duration(2 * 3600), "2 h")
        # Named by number: a UTC date alone can look like the wrong day.
        self.assertEqual(export.session_label(session()), "Session 7 · 2 h 14 min · Nov 14 (UTC)")
        self.assertIn("stopped early", export.session_label(session(None)))
        self.assertIn("recording now", export.session_label(session(None), running=True))


class Buffer(unittest.TestCase):
    def test_lines_from_someone_who_stopped_are_never_taken(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "keep"))
        buffer.add(line(2, DEE, "drop"))
        self.assertEqual([w.heard for w in buffer.take(lambda uid: uid == MIA)], ["keep"])
        self.assertEqual(len(buffer), 0)  # Dee's line is gone, not kept for later

    def test_drop_speaker(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "keep"))
        buffer.add(line(2, DEE, "drop"))
        buffer.drop_speaker(DEE)
        self.assertEqual([w.heard for w in buffer.take(lambda _: True)], ["keep"])

    def test_a_failed_save_is_retried_in_order(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "first"))
        batch = buffer.take(lambda _: True)
        buffer.add(line(2, MIA, "second"))
        buffer.put_back(batch)
        self.assertEqual([w.heard for w in buffer.take(lambda _: True)], ["first", "second"])

    def test_waiting_lines_are_capped_and_blank_ones_ignored(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(0, MIA, "  "))
        self.assertEqual(len(buffer), 0)
        for i in range(MAX_UNSAVED + 3):
            buffer.add(line(i, MIA, f"line {i}"))
        self.assertEqual((len(buffer), buffer.dropped), (MAX_UNSAVED, 3))


class DownloadButtons(unittest.TestCase):
    """The buttons sent when a session ends (#296): their IDs work after a restart."""

    def test_three_choices_and_old_buttons_still_work(self) -> None:
        from dmbot.ui import transcripts as ui

        view = ui.download_view(1, "a" * 32)
        ids = [item.custom_id for item in view.children]  # type: ignore[attr-defined]
        self.assertEqual(
            ids,
            [f"dmbot:transcript:1:{'a' * 32}:{c}" for c in ("cleaned", "heard", "both")],
        )
        template = ui.DownloadButton.__discord_ui_compiled_template__
        for custom_id in [*ids, f"dmbot:transcript:1:{'a' * 32}"]:
            with self.subTest(custom_id=custom_id):
                self.assertIsNotNone(template.fullmatch(custom_id))
                self.assertLessEqual(len(custom_id), 100)
        old = ui.DownloadButton(1, "a" * 32)  # sent before there was a choice
        self.assertEqual(old.versions, (export.AS_HEARD,))
        both = ui.DownloadButton(1, "a" * 32, "both")
        self.assertEqual(both.versions, (export.CLEANED, export.AS_HEARD))
