""" "Did they mean…?" bookkeeping and wording (#296), without Discord or a database."""

import unittest

from dmbot.transcript.cleaner import Question
from dmbot.transcript.questions import QuestionBook, fixed_text, kept_text, question_text

MIA, DEE = 8, 9


def question(heard: str = "Marin") -> Question:
    return Question(5, 5 + len(heard), heard, (("a" * 32, "Maren"), ("b" * 32, "Marron")))


class QuestionBookTest(unittest.TestCase):
    def test_one_open_at_a_time(self) -> None:
        book = QuestionBook()
        asked = book.offer(MIA, (question(),))
        assert asked is not None
        self.assertEqual((asked.speaker, asked.heard), (MIA, "Marin"))
        self.assertIsNone(book.offer(DEE, (question("Belaros"),)))  # one is open
        self.assertIs(book.take(asked.id), asked)
        self.assertIsNone(book.take(asked.id))  # answered once only
        self.assertIsNotNone(book.offer(DEE, (question("Belaros"),)))

    def test_each_word_once_a_session(self) -> None:
        book = QuestionBook()
        first = book.offer(MIA, (question(),))
        assert first is not None
        book.take(first.id)
        self.assertIsNone(book.offer(DEE, (question("marin"),)))  # same words, any case

    def test_the_next_new_word_in_a_line(self) -> None:
        book = QuestionBook()
        book.asked_keys.add("marin")
        asked = book.offer(MIA, (question(), question("Belaros")))
        assert asked is not None
        self.assertEqual(asked.heard, "Belaros")

    def test_a_speaker_who_stops_loses_their_question(self) -> None:
        book = QuestionBook()
        book.offer(MIA, (question(),))
        self.assertIsNone(book.drop_speaker(DEE))
        self.assertIsNotNone(book.drop_speaker(MIA))
        self.assertIsNone(book.open)

    def test_a_wrong_id_takes_nothing(self) -> None:
        book = QuestionBook()
        book.offer(MIA, (question(),))
        self.assertIsNone(book.take("00000000"))
        self.assertIsNotNone(book.open)


class WordingTest(unittest.TestCase):
    def test_plain_words(self) -> None:
        self.assertEqual(question_text("Mia", "Marin"), '❓ **Mia said "Marin"**: did they mean…')
        self.assertIn("written as **Maren**", fixed_text("Marin", "Maren"))
        self.assertIn("stays as heard", kept_text("Marin"))

    def test_long_words_are_shortened(self) -> None:
        self.assertLess(len(question_text("Mia", "x" * 500)), 120)


if __name__ == "__main__":
    unittest.main()
