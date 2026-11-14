"""The words of the map question (#4344): who can see the map is said in the question and in each
answer, a no says what stays, and the button labels fit a phone. Pure."""

from __future__ import annotations

import unittest

from dmbot.maps import words


class Words(unittest.TestCase):
    def test_the_question_says_who_can_see_the_map(self) -> None:
        chat, private = words.question(True), words.question(False)
        for text in (chat, private):
            self.assertIn(words.QUESTION, text)
            self.assertIn("goes by your answer", text)
        self.assertIn("Everyone at the table has seen it", chat)
        self.assertIn("Only the DMs of this campaign can see it", private)
        self.assertNotIn("Only the DMs", chat)

    def test_a_players_yes_waits_for_a_dm_and_says_who_can_see_it(self) -> None:
        for from_chat in (True, False):
            text = words.said_yes(False, from_chat)
            self.assertIn("A DM has to say it can be used before DMbot reads it", text)
            self.assertIn("take it back from the picture's page", text)
        self.assertIn("Everyone at the table already saw it", words.said_yes(False, True))
        self.assertTrue(words.said_yes(False, False).startswith("Kept. Only the DMs can see it."))

    def test_a_dms_yes_lets_dmbot_use_it(self) -> None:
        self.assertIn("DMbot can now use it", words.said_yes(True, True))
        self.assertEqual(
            words.said_yes(True, False), "Kept as a map. Only you and the other DMs can see it."
        )

    def test_a_no_says_the_picture_is_still_in_the_chat_when_it_is(self) -> None:
        self.assertIn("still in the chat", words.said_no(True))
        self.assertIn("transcript", words.said_no(True))
        self.assertEqual(words.said_no(False), "OK. DMbot didn't keep it.")

    def test_the_labels_fit_a_button_and_no_technical_word_is_used(self) -> None:
        for label in (words.YES_LABEL, words.NO_LABEL):
            self.assertLessEqual(len(label), 80)
        everything = " ".join(
            (words.question(True), words.question(False), words.FAILED, words.GONE, words.FULL,
             *(words.said_yes(a, b) for a in (True, False) for b in (True, False)))
        )  # fmt: skip
        for word in ("audience", "row", "store ", "database", "permanent"):
            self.assertNotIn(word, everything.lower())

    def test_the_dms_are_told_when_the_question_could_not_be_sent(self) -> None:
        self.assertEqual(
            words.dms_could_not_ask("Mia"), "DMbot couldn't ask Mia. The map is not being used."
        )
