"""The sidebar's brevity rules, pure (#934): sentence splitting, padding, yes/no, and the cut."""

from __future__ import annotations

import unittest

from dmbot.sidebar import brevity


class Sentences(unittest.TestCase):
    def test_a_dot_after_a_page_or_inside_a_version_does_not_end_one(self) -> None:
        text = "See SRD 5.2.1 p. 131 for it. It is a spell."
        self.assertEqual(
            brevity.sentences(text), ["See SRD 5.2.1 p. 131 for it.", "It is a spell."]
        )

    def test_e_g_does_not_end_one(self) -> None:
        self.assertEqual(len(brevity.sentences("Use a spell, e.g. Fireball. Done.")), 2)

    def test_empty_text_has_none(self) -> None:
        self.assertEqual(brevity.sentences("   "), [])


class HandCounted(unittest.TestCase):
    """Counted by eye, so the counter cannot agree with its own mistakes."""

    def test_words_ending_like_an_abbreviation_still_end_a_sentence(self) -> None:
        self.assertEqual(
            len(brevity.sentences("Yes, you can stop. It costs an action. Then roll.")), 3
        )
        self.assertEqual(len(brevity.sentences("Drop it. Grab the map. Run.")), 3)

    def test_no_at_the_start_is_an_answer_not_an_abbreviation(self) -> None:
        self.assertEqual(len(brevity.sentences("No. It needs sight. It also needs range.")), 3)
        self.assertEqual(len(brevity.sentences("Yes. It needs sight. It also needs range.")), 3)

    def test_no_before_a_number_is_an_abbreviation(self) -> None:
        self.assertEqual(len(brevity.sentences("See No. 5 for it. Done.")), 2)

    def test_three_sentences_starting_with_no_are_too_many(self) -> None:
        problems = brevity.violations(
            "can it", "No. It needs sight. It also needs range.", full_text=False
        )
        self.assertIn("use at most 2 sentences", problems)


class PaddingKeepsMeaning(unittest.TestCase):
    def test_absolutely_not_stays_not(self) -> None:
        self.assertEqual(brevity.strip_padding("Absolutely not."), "Absolutely not.")
        self.assertEqual(
            brevity.strip_padding("Certainly not, it fails."), "Certainly not, it fails."
        )

    def test_sure_with_a_comma_becomes_yes(self) -> None:
        self.assertEqual(brevity.strip_padding("Sure, it can."), "Yes, it can.")

    def test_ordinary_rules_sentences_are_kept(self) -> None:
        text = "Yes. Anything else in the area takes damage. If you want more damage, upcast it."
        self.assertEqual(brevity.strip_padding(text), text)

    def test_a_real_offer_is_removed(self) -> None:
        self.assertEqual(
            brevity.strip_padding(
                "Yes. Would you like me to read the spell? Is there anything else?"
            ),
            "Yes.",
        )


class Choices(unittest.TestCase):
    def test_a_choice_question_is_not_a_yes_no_question(self) -> None:
        self.assertFalse(brevity.is_yes_no_question("is it advantage or disadvantage when prone"))


class FullText(unittest.TestCase):
    def test_the_words_that_ask_for_the_whole_thing(self) -> None:
        for question in (
            "I need the spell description for fireball",
            "read me the whole rule on grappling",
            "give me the full text of Prone",
            "what's the goblin stat block",
            "read me the whole spell",
        ):
            self.assertTrue(brevity.asks_for_full_text(question), question)

    def test_an_ordinary_question_does_not(self) -> None:
        for question in (
            "how much damage does fireball do",
            "does prone give disadvantage",
            "what's the description of the room",
            "does the description say 60 ft",
        ):
            self.assertFalse(brevity.asks_for_full_text(question), question)


class YesNo(unittest.TestCase):
    def test_questions_a_yes_or_no_answers(self) -> None:
        for question in (
            "do you need line of sight for fireball",
            "hold on, I need to find if you need line of sight for fireball",
            "can a goblin be grappled",
            "is the 2014 goblin different",
            "whether prone gives disadvantage",
        ):
            self.assertTrue(brevity.is_yes_no_question(question), question)

    def test_questions_that_are_not(self) -> None:
        for question in ("how much damage does fireball do", "what does grappled do"):
            self.assertFalse(brevity.is_yes_no_question(question), question)

    def test_what_counts_as_starting_with_the_answer(self) -> None:
        for text in ("Yes. It does.", "No.", "Not sure.", "The free rules don't say. Your call."):
            self.assertTrue(brevity.starts_with_the_answer(text), text)
        self.assertFalse(brevity.starts_with_the_answer("Fireball has no sight requirement."))


class Padding(unittest.TestCase):
    def test_a_greeting_and_an_offer_are_removed(self) -> None:
        text = "Great question! No. It needs a point you choose. Let me know if you want more."
        self.assertEqual(brevity.strip_padding(text), "No. It needs a point you choose.")

    def test_a_plain_answer_is_unchanged(self) -> None:
        self.assertEqual(brevity.strip_padding("Yes. It does."), "Yes. It does.")

    def test_sure_thing_openers(self) -> None:
        self.assertEqual(brevity.strip_padding("Sure, 8d6 fire damage."), "8d6 fire damage.")


class Limits(unittest.TestCase):
    def test_three_sentences_is_too_many(self) -> None:
        problems = brevity.violations("how much", "One. Two. Three.", full_text=False)
        self.assertIn("use at most 2 sentences", problems)

    def test_over_200_characters_is_too_long(self) -> None:
        problems = brevity.violations(
            "how much", "A" * 150 + ". " + "B" * 80 + ".", full_text=False
        )
        self.assertIn("use at most 200 characters", problems)

    def test_a_yes_no_question_needs_yes_or_no_first(self) -> None:
        problems = brevity.violations("does it work", "It works fine.", full_text=False)
        self.assertEqual(problems, ['start with "Yes." or "No."'])

    def test_a_good_answer_has_no_problems(self) -> None:
        self.assertEqual(brevity.violations("does it work", "Yes. It works.", full_text=False), [])

    def test_the_full_text_is_not_limited(self) -> None:
        self.assertEqual(brevity.violations("the description", "x. " * 50, full_text=True), [])

    def test_shorten_keeps_the_first_sentences_that_fit(self) -> None:
        text = "Yes. " + "A" * 150 + ". Third sentence here."
        cut = brevity.shorten(text)
        self.assertTrue(brevity.within_limit(cut))
        self.assertTrue(cut.startswith("Yes."))

    def test_shorten_trims_one_huge_sentence_at_a_word(self) -> None:
        cut = brevity.shorten("word " * 100)
        self.assertLessEqual(len(cut), brevity.MAX_CHARS)
        self.assertTrue(cut.endswith("…"))
        self.assertFalse(cut[:-1].endswith(" "))

    def test_shorten_never_sends_a_paragraph(self) -> None:
        for text in ("x. " * 40, "y" * 5000, "Hello there. " * 30):
            self.assertTrue(brevity.within_limit(brevity.shorten(text)), text[:20])


class Source(unittest.TestCase):
    def test_the_source_and_confidence_come_after(self) -> None:
        self.assertEqual(
            brevity.join_source("No.", "SRD 5.2.1 p. 131", "sure"), "No. (SRD 5.2.1 p. 131, sure)"
        )

    def test_with_neither_the_answer_stands_alone(self) -> None:
        self.assertEqual(brevity.join_source("No.", None, None), "No.")

    def test_a_source_alone(self) -> None:
        self.assertEqual(brevity.join_source("No.", "DMbot help", None), "No. (DMbot help)")


if __name__ == "__main__":
    unittest.main()
