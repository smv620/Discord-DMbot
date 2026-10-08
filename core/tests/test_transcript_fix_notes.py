"""Name fixes the DM can undo (#296), without Discord or a database."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen.name_questions import FixUndoButton, fix_notes_view
from dmbot.transcript.cleaner import SOUND, Fix
from dmbot.transcript.fix_notes import ANSWERS_KEPT, SHOWN, Answer, FixNotes, message_text
from dmbot.transcript.models import Line, TranscriptBuffer
from dmbot.transcript.stream import EDIT_WINDOW_S, TranscriptStream

MIA, DEE = 8, 9
HEARD = "then Hrothgarr and Beleros left"


def fixes(*, first_sure: bool = False) -> tuple[Fix, ...]:
    return (
        Fix(5, 14, "Hrothgarr", "Hrothgar", "a" * 32, SOUND, sure=first_sure),
        Fix(19, 26, "Beleros", "Belleros", "b" * 32, SOUND, sure=False),
    )


class DoneTextTest(unittest.TestCase):
    def test_says_when_the_line_itself_stays(self) -> None:
        from dmbot.transcript.fix_notes import done_text

        self.assertNotIn("couldn't", done_text("Hrothgarr", "Hrothgar"))
        kept = done_text("Hrothgarr", "Hrothgar", line_kept=True)
        self.assertTrue(
            kept.endswith("That line couldn't be put back, so it still says **Hrothgar**.")
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

    def test_an_answer_fixes_its_line_alongside_the_fixes(self) -> None:
        # "then Hrothgarr and Beleros left": Hrothgarr was fixed, Beleros was asked about.
        book = FixNotes()
        (first,) = book.add(MIA, 1000, HEARD, fixes()[:1])
        book.answered(Answer(41, MIA, 1000, HEARD, fixes()[:1], 19, 26, "Bellaros"))
        self.assertEqual(book.line_text(first), "then Hrothgar and Bellaros left")
        book.undo(first.id)  # a fix undone later: the answer stays
        self.assertEqual(book.line_text(first), "then Hrothgarr and Bellaros left")
        answer = book.take_back(41)  # the answer's Undo: the fix's Undo stays
        assert answer is not None
        self.assertEqual(
            book.words_now(MIA, 1000, HEARD, answer.fixes), "then Hrothgarr and Beleros left"
        )
        self.assertIsNone(book.take_back(41))  # once only

    def test_still_fixed_leaves_out_undone_fixes(self) -> None:
        book = FixNotes()
        _, second = book.add(MIA, 1000, HEARD, fixes())
        book.undo(second.id)
        self.assertEqual(book.still_fixed(MIA, 1000, fixes()), fixes()[:1])
        self.assertEqual(book.still_fixed(DEE, 1000, fixes()), fixes())  # another line

    def test_answers_and_notes_stay_in_step(self) -> None:
        book = FixNotes()
        (first,) = book.add(MIA, 1000, HEARD, fixes()[:1])
        book.answered(Answer(41, MIA, 1000, HEARD, fixes()[:1], 19, 26, "Bellaros"))
        book.undo(first.id)  # after the answer: the answer no longer carries that fix
        book.notes.clear()  # its notes let go
        answer = book.take_back(41)
        assert answer is not None
        self.assertEqual(book.words_now(MIA, 1000, HEARD, answer.fixes), HEARD)
        # An answer on a line that still has notes outlives the newest-answers limit.
        book = FixNotes()
        (note,) = book.add(MIA, 1000, HEARD, fixes()[:1])
        book.answered(Answer(41, MIA, 1000, HEARD, fixes()[:1], 19, 26, "Bellaros"))
        for batch in range(ANSWERS_KEPT + 1):
            book.answered(Answer(100 + batch, DEE, batch, HEARD, (), 5, 14, "Hrothgar"))
        self.assertEqual(book.line_text(note), "then Hrothgar and Bellaros left")
        book.answered(Answer(None, MIA, 2000, HEARD, (), 5, 14, "Hrothgar"))  # no Undo
        self.assertEqual(book.words_now(MIA, 2000, HEARD, ()), "then Hrothgar and Beleros left")

    def test_answers_are_let_go_and_go_with_their_speaker(self) -> None:
        book = FixNotes()
        for batch in range(ANSWERS_KEPT + 1):
            book.answered(Answer(batch, MIA, batch, HEARD, (), 5, 14, "Hrothgar"))
        self.assertIsNone(book.take_back(0))  # too old to put back
        book.answered(Answer(99, DEE, 0, HEARD, (), 5, 14, "Hrothgar"))
        book.drop_speaker(MIA)
        self.assertEqual([a.batch for a in book.answers], [99])

    def test_the_message_numbers_its_lines_and_shows_what_was_undone(self) -> None:
        book = FixNotes()
        _, second = book.add(MIA, 1000, HEARD, fixes())
        book.undo(second.id)
        text, shown = message_text(book.shown(), {MIA: "Mia"})
        self.assertEqual(len(shown), 2)
        self.assertIn("1. **Hrothgarr** → **Hrothgar** (Mia)", text)
        self.assertIn("2. ~~Beleros → Belleros~~ (Mia): ↩️ undone", text)
        self.assertTrue(text.startswith("✏️ **Name fixes to check**"))
        self.assertIn("Nothing to check right now", message_text([], {})[0])

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
        text, shown = message_text(book.shown(), {MIA: "Mia"}, lambda t: t.replace("_", "\\_"))
        self.assertLessEqual(len(text), 2000)
        # whole lines only: the oldest went, and each kept line is complete
        self.assertLess(len(shown), SHOWN)
        self.assertEqual([n.number for n in shown], [n.number for n in book.shown()][-len(shown) :])
        for line in text.splitlines()[1:]:
            self.assertEqual(line.count("**"), 4)

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
        stream.posted(count, MagicMock(edit=AsyncMock()), now=1.0)
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
        message = MagicMock(edit=AsyncMock())
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
        stream.posted(1, MagicMock(edit=AsyncMock()), now=100.0)
        self.assertIsNone(stream.relabel(MIA, 1000, "I saw Beleros", 100.0 + EDIT_WINDOW_S + 1))

    def test_a_waiting_line_in_the_save_buffer(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(Line(1000, MIA, "I saw Beleros", "I saw Belleros"))
        self.assertTrue(buffer.relabel(MIA, 1000, "I saw Beleros"))
        self.assertFalse(buffer.relabel(MIA, 9999, "x"))
        (line,) = buffer.take(lambda _: True)
        self.assertEqual((line.heard, line.text), ("I saw Beleros", "I saw Beleros"))

    def test_a_waiting_lines_topic(self) -> None:
        # #52: the off-topic filter labels a line still waiting to be saved; the rest of
        # it (the fixed words, its length) is kept.
        buffer = TranscriptBuffer()
        buffer.add(Line(1000, MIA, "I saw Beleros", "I saw Belleros", 2_500))
        self.assertTrue(buffer.set_topic(MIA, 1000, "off_topic"))
        self.assertFalse(buffer.set_topic(MIA, 9999, "off_topic"))
        (line,) = buffer.take(lambda _: True)
        self.assertEqual(
            (line.text, line.duration_ms, line.topic), ("I saw Belleros", 2_500, "off_topic")
        )


if __name__ == "__main__":
    unittest.main()
