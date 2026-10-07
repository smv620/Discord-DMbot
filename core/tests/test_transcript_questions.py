""" "Did they mean…?" bookkeeping and wording (#296), without Discord or a database."""

import unittest

from dmbot.transcript.cleaner import Question
from dmbot.transcript.questions import (
    QUESTION_TTL_S,
    Begin,
    QuestionBook,
    fixed_text,
    keep_label,
    kept_text,
    option_label,
    question_text,
)

MIA, DEE = 8, 9


def question(heard: str = "Marin") -> Question:
    return Question(5, 5 + len(heard), heard, (("a" * 32, "Maren"), ("b" * 32, "Marron")))


class QuestionBookTest(unittest.TestCase):
    def test_one_open_at_a_time(self) -> None:
        book = QuestionBook()
        asked = book.offer(MIA, (question(),), 0.0)
        assert asked is not None
        self.assertEqual((asked.speaker, asked.heard), (MIA, "Marin"))
        self.assertIsNone(book.offer(DEE, (question("Belaros"),), 1.0))  # one is open
        self.assertIs(book.close(), asked)
        self.assertIsNotNone(book.offer(DEE, (question("Belaros"),), 2.0))

    def test_each_word_once_a_session(self) -> None:
        book = QuestionBook()
        book.offer(MIA, (question(),), 0.0)
        book.close()
        self.assertIsNone(book.offer(DEE, (question("marin"),), 1.0))  # same words, any case

    def test_the_next_new_word_in_a_line(self) -> None:
        book = QuestionBook()
        book.asked_keys.add("marin")
        asked = book.offer(MIA, (question(), question("Belaros")), 0.0)
        assert asked is not None
        self.assertEqual(asked.heard, "Belaros")

    def test_an_unanswered_question_expires(self) -> None:
        book = QuestionBook()
        asked = book.offer(MIA, (question(),), 0.0)
        self.assertIsNone(book.expire(QUESTION_TTL_S - 1))
        self.assertIs(book.expire(QUESTION_TTL_S), asked)
        self.assertIsNotNone(book.offer(MIA, (question("Belaros"),), QUESTION_TTL_S))

    def test_answering_once_and_never_expired_mid_answer(self) -> None:
        book = QuestionBook()
        asked = book.offer(MIA, (question(),), 0.0)
        assert asked is not None
        self.assertIs(book.begin(asked.id), Begin.OK)
        self.assertIs(book.begin(asked.id), Begin.BUSY)  # a second press while saving
        self.assertIsNone(book.expire(QUESTION_TTL_S * 2))
        book.failed(asked.id)  # saving failed: open again for another try
        self.assertIs(book.begin(asked.id), Begin.OK)
        book.close()
        self.assertIs(book.begin(asked.id), Begin.GONE)

    def test_a_speaker_who_stops_loses_their_question_even_mid_answer(self) -> None:
        book = QuestionBook()
        asked = book.offer(MIA, (question(),), 0.0)
        assert asked is not None
        book.begin(asked.id)
        self.assertIsNone(book.drop_speaker(DEE))
        self.assertIs(book.drop_speaker(MIA), asked)
        self.assertFalse(book.is_open(asked.id))
        book.failed(asked.id)  # a late failure doesn't bring it back
        self.assertIsNone(book.open)

    def test_a_wrong_id(self) -> None:
        book = QuestionBook()
        book.offer(MIA, (question(),), 0.0)
        self.assertIs(book.begin("00000000"), Begin.GONE)
        self.assertIsNotNone(book.open)


class WordingTest(unittest.TestCase):
    def test_plain_words(self) -> None:
        text = question_text("Mia", "Marin")
        self.assertIn('DMbot heard Mia say "Marin".', text)
        self.assertIn("Ignore this and it stays as heard", text)
        self.assertIn("is written **Maren** in this campaign", fixed_text("Marin", "Maren"))
        self.assertIn('won\'t change "Marin" in this campaign', kept_text("Marin"))
        self.assertEqual(keep_label("Marin"), 'Keep "Marin"')

    def test_short_enough_for_a_phone(self) -> None:
        self.assertLessEqual(len(option_label("x" * 200)), 25)
        self.assertLessEqual(len(keep_label("x" * 200)), 25)
        self.assertLess(len(question_text("Mia", "x" * 500)), 180)


if __name__ == "__main__":
    unittest.main()
