"""The DM's question "Who was speaking just now?" (#53), without Discord."""

import unittest

from dmbot.transcript import speaker_question as q
from dmbot.transcript.models import NARRATING


class BookTest(unittest.TestCase):
    def test_one_question_at_a_time_and_a_line_is_asked_once(self) -> None:
        book = q.SpeakerBook()
        asked = book.offer(7, 1000, "Welcome, travellers", ["Hiro", "Auril"], 0.0)
        assert asked is not None
        self.assertEqual(asked.options, ("Hiro", "Auril"))
        self.assertIsNone(book.offer(7, 2000, "another", [], 1.0))  # one is open
        book.close(q.ANSWERED, 10.0)
        self.assertIsNone(book.offer(7, 3000, "later", [], 10.0 + q.COOLDOWN_S - 1))  # a pause
        self.assertIsNone(book.offer(7, 1000, "same line", [], 10.0 + q.COOLDOWN_S + 1))
        self.assertIsNotNone(book.offer(7, 3000, "later", [], 10.0 + q.COOLDOWN_S + 1))

    def test_at_most_three_names_are_offered(self) -> None:
        book = q.SpeakerBook()
        asked = book.offer(7, 1, "x", ["a", "b", "c", "d"], 0.0)
        assert asked is not None
        self.assertEqual(asked.options, ("a", "b", "c"))

    def test_an_unanswered_question_expires_but_not_while_being_saved(self) -> None:
        book = q.SpeakerBook()
        asked = book.offer(7, 1, "x", [], 0.0)
        assert asked is not None
        self.assertIsNone(book.expire(q.QUESTION_TTL_S - 1))
        self.assertEqual(book.begin(asked.id), q.Begin.OK)
        self.assertEqual(book.begin(asked.id), q.Begin.BUSY)
        self.assertIsNone(book.expire(q.QUESTION_TTL_S + 1))  # an answer is being saved
        book.failed(asked.id)
        self.assertIs(book.expire(q.QUESTION_TTL_S + 1), asked)
        self.assertEqual(book.why_closed(asked.id), q.EXPIRED_WHY)
        self.assertEqual(book.begin(asked.id), q.Begin.GONE)

    def test_a_question_that_was_not_posted_starts_no_pause_and_can_be_asked_again(self) -> None:
        book = q.SpeakerBook()
        book.offer(7, 1, "x", [], 0.0)
        book.close(q.NOT_POSTED, 1.0)
        self.assertIsNotNone(book.offer(7, 1, "x", [], 2.0))

    def test_someone_who_stops_loses_their_question(self) -> None:
        book = q.SpeakerBook()
        asked = book.offer(7, 1, "x", [], 0.0)
        self.assertIsNone(book.drop_speaker(8, 1.0))
        self.assertIs(book.drop_speaker(7, 1.0), asked)
        self.assertIsNone(book.open)


class PickTest(unittest.TestCase):
    def test_buttons_stand_for_names(self) -> None:
        book = q.SpeakerBook()
        asked = book.offer(7, 1, "x", ["Hiro", "Auril"], 0.0)
        assert asked is not None
        self.assertEqual(q.pick_to_name(asked, "narrating"), NARRATING)
        self.assertEqual(q.pick_to_name(asked, "1"), "Auril")
        for pick in ("2", "9", "type", "skip", "Silas Vane", ""):
            with self.subTest(pick=pick):
                self.assertIsNone(q.pick_to_name(asked, pick))

    def test_texts_are_short_and_plain(self) -> None:
        text = q.question_text("a" * 200)
        self.assertIn("Who was speaking just now?", text)
        self.assertLess(len(text), 200)
        self.assertEqual(
            q.answered_text("Brother Hiro"), "✅ Got it: that line is marked **Brother Hiro**."
        )
