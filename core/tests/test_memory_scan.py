"""The after-session scan that suggests new names (#126, step 3), without a database."""

import unittest

from dmbot.devtools.stt_bakeoff.data import LINES, NAMES
from dmbot.memory.scan import MAX_SUGGESTIONS, find_new_names


def names(found: list) -> list[str]:  # type: ignore[type-arg]
    return [s.name for s in found]


class Scan(unittest.TestCase):
    def test_names_said_mid_sentence_twice_are_suggested(self) -> None:
        found = find_new_names(
            ["We ride to Bryn Shander at dawn.", "Bryn Shander is cold.", "I like Bryn Shander."]
        )
        self.assertEqual([(s.name, s.times) for s in found], [("Bryn Shander", 3)])

    def test_once_is_not_enough(self) -> None:
        self.assertEqual(find_new_names(["We ride to Targos at dawn."]), [])

    def test_sentence_starts_and_common_words_are_not_names(self) -> None:
        lines = [
            "Then we go. Then we rest.",
            "Okay, I roll. Okay, done.",
            "Roll for Insight. Roll it.",
        ]
        self.assertEqual(find_new_names(lines), [])

    def test_a_word_also_said_in_lower_case_is_not_a_name(self) -> None:
        lines = ["I hit the Wall. The Wall falls.", "Behind the wall is a door."]
        self.assertEqual(find_new_names(lines), [])

    def test_known_answers_are_never_suggested_again(self) -> None:
        lines = ["I see Hrothgar now.", "Ask Hrothgar again.", "Then Mia laughs.", "Ask Mia."]
        self.assertEqual(names(find_new_names(lines, skip_keys=["hrothgar", "Mia"])), [])

    def test_leading_words_are_trimmed_from_a_name(self) -> None:
        lines = ["They serve The Ashen Crown.", "Beware The Ashen Crown."]
        self.assertEqual(names(find_new_names(lines)), ["Ashen Crown"])

    def test_bakeoff_script_finds_its_invented_names(self) -> None:
        found = set(names(find_new_names([line.text for line in LINES], min_times=2)))
        self.assertLessEqual(len(found), MAX_SUGGESTIONS)
        invented = {n.canonical for n in NAMES}
        self.assertTrue(found <= invented | {"Ashen Crown"}, found - invented)  # nothing else
        self.assertIn("Cerric", found)
