"""The after-session scan that suggests new names (#126, step 3), without a database."""

import unittest

from dmbot.devtools.stt_bakeoff.data import LINES, NAMES
from dmbot.memory.scan import MAX_SUGGESTIONS, Suggestion, find_new_names


def names(found: list[Suggestion]) -> list[str]:
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
        lines = ["I see Hrothgar now.", "We ask Hrothgar again.", "So Mia laughs.", "I tell Mia."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar", "Mia"])  # without skips
        self.assertEqual(names(find_new_names(lines, skip_keys=["hrothgar", "Mia"])), [])

    def test_possessives_count_as_the_name(self) -> None:
        lines = ["I meet Hrothgar's men.", "We see Hrothgar’s axe.", "I ask Ka'zeth twice."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar"])

    def test_a_name_after_a_capitalized_first_word_is_found(self) -> None:
        lines = ["Ask Hrothgar now.", "Tell Hrothgar now."]
        self.assertEqual(names(find_new_names(lines)), ["Hrothgar"])

    def test_long_runs_are_dropped_not_chopped(self) -> None:
        lines = ["We reach Caer Dineval Ice Fortress.", "Back to Caer Dineval Ice Fortress."]
        self.assertEqual(find_new_names(lines), [])

    def test_names_in_any_script(self) -> None:
        self.assertEqual(
            names(find_new_names(["Wir sehen Łódź heute.", "Dann Łódź wieder."])), ["Łódź"]
        )

    def test_classes_and_species_are_game_words(self) -> None:
        self.assertEqual(find_new_names(["I am a Paladin.", "My Paladin and the Elf."] * 2), [])

    def test_leading_words_are_trimmed_from_a_name(self) -> None:
        lines = ["They serve The Ashen Crown.", "Beware The Ashen Crown."]
        self.assertEqual(names(find_new_names(lines)), ["Ashen Crown"])

    def test_bakeoff_script_finds_its_invented_names(self) -> None:
        found = set(names(find_new_names([line.text for line in LINES], min_times=2)))
        self.assertLessEqual(len(found), MAX_SUGGESTIONS)
        invented = {n.canonical for n in NAMES}
        self.assertTrue(found <= invented | {"Ashen Crown"}, found - invented)  # nothing else
        self.assertIn("Cerric", found)
