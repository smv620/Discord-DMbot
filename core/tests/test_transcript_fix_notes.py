"""Name fixes the DM can undo (#296), without Discord or a database."""

import unittest

from dmbot.dm_screen.name_questions import FixUndoButton, fix_notes_view
from dmbot.transcript.cleaner import SOUND, Fix
from dmbot.transcript.fix_notes import SHOWN, FixNotes, message_text
from dmbot.transcript.models import Line, TranscriptBuffer
from dmbot.transcript.stream import EDIT_WINDOW_S, TranscriptStream

MIA, DEE = 8, 9
HEARD = "then Hrothgarr and Beleros left"


def fixes(*, first_sure: bool = False) -> tuple[Fix, ...]:
    return (
        Fix(5, 14, "Hrothgarr", "Hrothgar", "a" * 32, SOUND, sure=first_sure),
        Fix(19, 26, "Beleros", "Belleros", "b" * 32, SOUND, sure=False),
    )


class FixNotesTest(unittest.TestCase):
    def test_only_unsure_fixes_get_a_note(self) -> None:
        book = FixNotes()
        added = book.add(MIA, 1000, HEARD, fixes(first_sure=True))
        self.assertEqual([n.fix.heard for n in added], ["Beleros"])

    def test_undo_puts_the_heard_words_back_in_that_line_only(self) -> None:
        book = FixNotes()
        first, second = book.add(MIA, 1000, HEARD, fixes())
        self.assertEqual(book.line_text(first), "then Hrothgar and Belleros left")
        self.assertEqual(book.undo(second.id), [second])
        self.assertEqual(book.line_text(first), "then Hrothgar and Beleros left")
        self.assertEqual(book.undo(second.id), [])  # once only
        book.undo(first.id)
        self.assertEqual(book.line_text(first), HEARD)

    def test_the_same_words_twice_in_a_line_are_one_press(self) -> None:
        heard = "Hrothgarr and Hrothgarr roar"
        twice = (
            Fix(0, 9, "Hrothgarr", "Hrothgar", "a" * 32, SOUND, sure=False),
            Fix(14, 23, "Hrothgarr", "Hrothgar", "a" * 32, SOUND, sure=False),
        )
        book = FixNotes()
        first, _ = book.add(MIA, 1000, heard, twice)
        self.assertEqual(len(book.undo(first.id)), 2)
        self.assertEqual(book.line_text(first), heard)

    def test_the_message_numbers_its_lines_and_shows_what_was_undone(self) -> None:
        book = FixNotes()
        _, second = book.add(MIA, 1000, HEARD, fixes())
        book.undo(second.id)
        text = message_text(book.shown(), {MIA: "Mia"})
        self.assertIn("1. **Hrothgarr** → **Hrothgar** (Mia)", text)
        self.assertIn("2. ~~Beleros → Belleros~~ (Mia): ↩️ undone", text)
        self.assertTrue(text.startswith("✏️ **Name fixes to check**"))
        self.assertIn("Nothing to check right now", message_text([], {}))

    def test_numbers_never_change_meaning(self) -> None:
        book = FixNotes()
        for i in range(SHOWN + 3):
            book.add(MIA, i, HEARD, fixes(first_sure=True))
        self.assertEqual([n.number for n in book.shown()], list(range(4, SHOWN + 4)))
        view = fix_notes_view(1234, book.shown())
        assert view is not None
        self.assertEqual(view.children[0].item.label, "Undo 4")  # type: ignore[attr-defined]

    def test_old_notes_are_let_go(self) -> None:
        book = FixNotes()
        notes = [book.add(MIA, i, HEARD, fixes(first_sure=True))[0] for i in range(SHOWN + 2)]
        self.assertEqual(len(book.notes), SHOWN)
        self.assertIsNone(book.find(notes[0].id))
        self.assertIsNotNone(book.find(notes[-1].id))

    def test_a_long_list_still_fits_discord(self) -> None:
        book = FixNotes()
        long = "_" * 200
        for i in range(SHOWN):
            book.add(MIA, i, long, (Fix(0, 200, long, long, "a" * 32, SOUND, sure=False),))
        text = message_text(book.shown(), {MIA: "Mia"}, lambda t: t.replace("_", "\\_"))
        self.assertLessEqual(len(text), 2000)

    def test_a_speaker_who_stops_loses_their_notes(self) -> None:
        book = FixNotes()
        book.add(MIA, 1000, HEARD, fixes())
        book.add(DEE, 2000, HEARD, fixes())
        self.assertTrue(book.drop_speaker(MIA))
        self.assertEqual({n.speaker for n in book.notes}, {DEE})
        self.assertFalse(book.drop_speaker(MIA))

    def test_one_undo_button_per_fix_not_undone(self) -> None:
        book = FixNotes()
        first, second = book.add(MIA, 1000, HEARD, fixes())
        book.undo(first.id)
        view = fix_notes_view(1234, book.shown())
        assert view is not None
        (button,) = view.children
        self.assertEqual(button.item.custom_id, f"dmbot:fixundo:1234:{second.id}")  # type: ignore[attr-defined]
        template = FixUndoButton.__discord_ui_compiled_template__
        self.assertIsNotNone(template.fullmatch(button.item.custom_id))  # type: ignore[attr-defined]
        book.undo(second.id)
        self.assertIsNone(fix_notes_view(1234, book.shown()))


class RelabelTest(unittest.TestCase):
    def test_an_earlier_line_queued_during_a_send_stays_waiting(self) -> None:
        # #296 review: posted() took the first N lines, not the ones sent
        stream = TranscriptStream()
        stream.add(DEE, "Dee", "second line", 2000)
        text, count = stream.next_message(lambda _: True) or ("", 0)
        stream.add(MIA, "Mia", "first line", 1000)  # arrives while the message is sent
        stream.posted(count, object(), now=1.0)
        self.assertIn("second line", text)
        waiting, _ = stream.next_message(lambda _: True) or ("", 0)
        self.assertIn("first line", waiting)  # not lost
        self.assertNotIn("second line", waiting)  # not posted twice
        self.assertIsNone(stream.relabel(MIA, 1000, "first, fixed", 1.0))  # still waiting

    def test_a_waiting_line_goes_out_with_the_new_words(self) -> None:
        stream = TranscriptStream()
        stream.add(MIA, "Mia", "I saw Belleros", 1000)
        self.assertIsNone(stream.relabel(MIA, 1000, "I saw Beleros", 0.0))
        text, _ = stream.next_message(lambda _: True) or ("", 0)
        self.assertIn("I saw Beleros", text)

    def test_a_posted_line_is_edited_within_the_window(self) -> None:
        stream = TranscriptStream()
        stream.add(MIA, "Mia", "I saw Belleros", 1000)
        stream.add(DEE, "Dee", "Me too", 2000)
        _, count = stream.next_message(lambda _: True) or ("", 0)
        message = object()
        stream.posted(count, message, now=100.0)
        edit = stream.relabel(MIA, 1000, "I saw Beleros", 100.0 + EDIT_WINDOW_S - 1)
        assert edit is not None
        self.assertIs(edit[0], message)
        self.assertIn("I saw Beleros", edit[1])
        self.assertIn("Me too", edit[1])  # the rest of the message stays

    def test_too_late_for_the_channel(self) -> None:
        stream = TranscriptStream()
        stream.add(MIA, "Mia", "I saw Belleros", 1000)
        stream.next_message(lambda _: True)
        stream.posted(1, object(), now=100.0)
        self.assertIsNone(stream.relabel(MIA, 1000, "I saw Beleros", 100.0 + EDIT_WINDOW_S + 1))

    def test_a_waiting_line_in_the_save_buffer(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(Line(1000, MIA, "I saw Beleros", "I saw Belleros"))
        self.assertTrue(buffer.relabel(MIA, 1000, "I saw Beleros"))
        self.assertFalse(buffer.relabel(MIA, 9999, "x"))
        (line,) = buffer.take(lambda _: True)
        self.assertEqual((line.heard, line.text), ("I saw Beleros", "I saw Beleros"))


if __name__ == "__main__":
    unittest.main()
