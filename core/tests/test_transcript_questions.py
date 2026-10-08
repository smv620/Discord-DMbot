""" "Did they mean…?" bookkeeping and wording (#296), without Discord or a database."""

import unittest

from dmbot.transcript.cleaner import SOUND, Fix, Question
from dmbot.transcript.questions import (
    ANSWERED,
    COOLDOWN_S,
    NOT_POSTED,
    QUESTION_TTL_S,
    STOPPED,
    TYPED_MAX,
    Begin,
    QuestionBook,
    fixed_text,
    keep_label,
    kept_text,
    not_answered_text,
    option_label,
    question_text,
    undone_text,
)

MIA, DEE = 8, 9
MAREN, MARRON = "a" * 32, "b" * 32
SCENE = {MAREN}  # Maren was said lately: a question about her matters


def question(heard: str = "Marin") -> Question:
    return Question(5, 5 + len(heard), heard, ((MAREN, "Maren"), (MARRON, "Marron")), "then Marin")


def asked(book: QuestionBook, heard: str = "Marin", now: float = 0.0, speaker: int = MIA):  # type: ignore[no-untyped-def]
    return book.offer(speaker, (question(heard),), now, SCENE)


class QuestionBookTest(unittest.TestCase):
    def test_one_open_at_a_time(self) -> None:
        book = QuestionBook()
        first = asked(book)
        assert first is not None
        self.assertEqual((first.speaker, first.heard, first.context), (MIA, "Marin", "then Marin"))
        self.assertIsNone(asked(book, "Belaros", 1.0, DEE))  # one is open

    def test_a_cooldown_after_any_question_closes(self) -> None:
        book = QuestionBook()
        asked(book)
        book.close(ANSWERED, 10.0)
        self.assertIsNone(asked(book, "Belaros", 10.0 + COOLDOWN_S - 1))
        self.assertIsNotNone(asked(book, "Belaros", 10.0 + COOLDOWN_S))

    def test_a_question_never_posted_starts_no_cooldown_and_is_asked_again(self) -> None:
        book = QuestionBook()
        asked(book)
        book.close(NOT_POSTED, 10.0)
        self.assertIsNotNone(asked(book, "Marin", 11.0))

    def test_only_words_heard_twice_or_in_the_scene(self) -> None:
        book = QuestionBook()
        self.assertIsNone(book.offer(MIA, (question(),), 0.0, ()))  # once, nothing in scene
        self.assertIsNotNone(book.offer(MIA, (question(),), 1.0, ()))  # heard a second time

    def test_words_count_even_while_one_is_open(self) -> None:
        book = QuestionBook()
        asked(book)
        book.offer(DEE, (question("Belaros"),), 1.0, ())  # counted, not asked
        book.close(ANSWERED, 2.0)
        later = 2.0 + COOLDOWN_S
        self.assertIsNotNone(book.offer(DEE, (question("Belaros"),), later, ()))

    def test_each_word_once_a_session(self) -> None:
        book = QuestionBook()
        asked(book)
        book.close(ANSWERED, 0.0)
        self.assertIsNone(asked(book, "marin", COOLDOWN_S))  # same words, any case

    def test_the_next_new_word_in_a_line(self) -> None:
        book = QuestionBook()
        book.asked_keys.add("marin")
        first = book.offer(MIA, (question(), question("Belaros")), 0.0, SCENE)
        assert first is not None
        self.assertEqual(first.heard, "Belaros")

    def test_an_unanswered_question_expires(self) -> None:
        book = QuestionBook()
        first = asked(book)
        self.assertIsNone(book.expire(QUESTION_TTL_S - 1))
        self.assertIs(book.expire(QUESTION_TTL_S), first)
        self.assertEqual(book.closed_at, QUESTION_TTL_S)  # and the cooldown starts

    def test_answering_once_and_never_expired_mid_answer(self) -> None:
        book = QuestionBook()
        first = asked(book)
        assert first is not None
        self.assertIs(book.begin(first.id), Begin.OK)
        self.assertIs(book.begin(first.id), Begin.BUSY)  # a second press while saving
        self.assertIsNone(book.expire(QUESTION_TTL_S * 2))
        book.failed(first.id)  # saving failed: open again for another try
        self.assertIs(book.begin(first.id), Begin.OK)
        book.close(ANSWERED, 1.0)
        self.assertIs(book.begin(first.id), Begin.GONE)
        self.assertEqual(book.why_closed(first.id), ANSWERED)

    def test_a_speaker_who_stops_loses_their_question_even_mid_answer(self) -> None:
        book = QuestionBook()
        first = asked(book)
        assert first is not None
        book.begin(first.id)
        self.assertIsNone(book.drop_speaker(DEE, 1.0))
        self.assertIs(book.drop_speaker(MIA, 1.0), first)
        self.assertEqual(book.why_closed(first.id), STOPPED)
        book.failed(first.id)  # a late failure doesn't bring it back
        self.assertIsNone(book.open)

    def test_a_wrong_id(self) -> None:
        book = QuestionBook()
        asked(book)
        self.assertIs(book.begin("00000000"), Begin.GONE)
        self.assertIsNotNone(book.open)

    def test_the_question_knows_its_line(self) -> None:
        fix = Fix(0, 4, "then", "Then", "c" * 32, SOUND)
        got = QuestionBook().offer(
            MIA, (question(),), 0.0, SCENE, started_ms=1500, line="then Marin", fixes=(fix,)
        )
        assert got is not None
        self.assertEqual((got.started_ms, got.line, got.fixes), (1500, "then Marin", (fix,)))
        self.assertEqual(got.line[got.start : got.end], "Marin")


class WordingTest(unittest.TestCase):
    def test_plain_words(self) -> None:
        text = question_text("Mia", "Marin", "then Marin speaks")
        self.assertIn('DMbot heard Mia say "Marin"** ("…then Marin speaks…")', text)
        self.assertIn("Ignore this and it stays as heard", text)
        fixed = fixed_text("Marin", "Maren")
        self.assertIn("is written **Maren** in this campaign", fixed)
        self.assertIn("Earlier lines stay as heard.", fixed)
        that_line = fixed_text("Marin", "Maren", line_fixed=True)
        self.assertIn("**Maren**, in that line and from now on", that_line)
        self.assertIn("Earlier lines stay as heard.", that_line)
        new = fixed_text("Marin", "Maerin", line_fixed=True, new=True)
        self.assertIn("**Maerin** is new: it waits in 📝 Check new names.", new)
        self.assertIn('won\'t change "Marin" in this campaign', kept_text("Marin"))
        self.assertEqual(not_answered_text("Marin"), '⌛ Not answered: "Marin" stays as heard.')
        self.assertIn('"Marin" stays as heard again', undone_text("Marin"))
        self.assertEqual(keep_label("Marin"), 'Keep "Marin"')

    def test_type_it_wording(self) -> None:
        from dmbot.transcript import questions

        self.assertEqual(TYPED_MAX, 60)
        self.assertLessEqual(len(questions.TYPE_LABEL), 25)
        self.assertLessEqual(len(questions.FORM_TITLE), 45)
        for typed in ["Mae | rin", "x" * 61, "   ", "a b c d e f g h i", "www.x.com", "Ma\x07rin"]:
            with self.subTest(typed=typed):
                self.assertIn("**Type it…** again", questions.typed_problem(typed) or "")
        self.assertIsNone(questions.typed_problem("Hrothgar the Bold"))
        self.assertIn("can read the transcript", questions.TYPED_SECRET)
        late = questions.too_late_text("Maerin")
        self.assertIn("**Maerin**", late)
        self.assertIn("/dmbot names", late)

    def test_short_enough_for_a_phone(self) -> None:
        self.assertLessEqual(len(option_label("x" * 200)), 25)
        self.assertLessEqual(len(keep_label("x" * 200)), 25)
        self.assertLess(len(question_text("Mia", "x" * 500, "y" * 500)), 260)


if __name__ == "__main__":
    unittest.main()
