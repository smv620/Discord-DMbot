"""IPA to a plain respelling (#2316), and the check on any respelling. Pure; invented names."""

from __future__ import annotations

import unittest

from dmbot.names.respell import MAX_CHARS, clean, respell


class FromSpeechSounds(unittest.TestCase):
    def test_made_up_names_come_out_as_plain_letters_with_the_stress_in_capitals(self) -> None:
        cases = {
            "kɑˈzɛθ": "kah-ZETH",
            "ˈkɑzɛθ": "KAH-zeth",
            "θɔɹˈɡɹɪm": "thawr-GRIM",
            "ˈaɪvɔɹ": "EYE-vawr",
            "mɪˈɹɛlə": "mih-REH-luh",
            "ˈʃɑdoʊ": "SHAH-doh",
            "dʒɑˈɹɛd": "jah-RED",
            "tʃɛˈlɪs": "cheh-LIS",
            "ˈbɹɑnwɪn": "BRAHN-win",
            "zɪˈlɑːɹa": "zih-LAH-rah",
            "ˈɛldɹɪn": "EL-drin",
            "ˈvɛskɹɪt": "VEH-skrit",
            "ˈaʊɹeɪl": "OW-rayl",
            "ʃɑˈnɪʃtɹa": "shah-NISH-trah",
            "ˈhɑɹvɛst": "HAHR-vest",
            "ˈnɔɪbɑ": "NOY-bah",
            "ˈɛlɹɪk": "EL-rik",
            "dɹɑˈkuːn": "drah-KOON",
            "ˈflʌfɪn": "FLUH-fin",
            "ˈtwɪstɚ": "TWIH-ster",
            "ˈɡɹɑkʃɑ": "GRAHK-shah",
            "ʒɑˈnɛt": "zhah-NET",
        }
        for ipa, expected in cases.items():
            with self.subTest(ipa=ipa):
                self.assertEqual(respell(ipa), expected)

    def test_without_stress_marks_a_short_name_is_stressed_first_and_a_long_one_next_to_last(
        self,
    ) -> None:
        self.assertEqual(respell("kazɛθ"), "KAH-zeth")
        self.assertEqual(respell("mɪɹɛlə"), "mih-REH-luh")

    def test_a_one_syllable_name_has_no_capitals_and_a_ng_is_one_sound(self) -> None:
        self.assertEqual(respell("ˈlɪŋɡ"), "ling")
        self.assertEqual(respell("ˈgɹɔmʃ"), "grawmsh")

    def test_words_stay_apart_and_dots_and_length_marks_are_ignored(self) -> None:
        self.assertEqual(respell("ˈnɔɪbɑ ˈfɹɛd"), "NOY-bah fred")
        self.assertEqual(respell("ɑˈɹæs.ta"), "ah-RAS-tah")

    def test_nothing_usable_is_nothing(self) -> None:
        for ipa in ("", "   ", "ʔ", "ˈ", "☃"):
            with self.subTest(ipa=ipa):
                self.assertEqual(respell(ipa), "")

    def test_a_secondary_stress_does_not_move_the_capitals(self) -> None:
        self.assertEqual(respell("ˌɛlɹɪˈkɑ"), "el-rih-KAH")

    def test_too_long_to_save_is_nothing(self) -> None:
        self.assertEqual(respell(" ".join(["ˈkɑzɛθ"] * 30)), "")


class TheCheck(unittest.TestCase):
    def test_letters_hyphens_spaces_and_apostrophes_pass_and_spaces_are_tidied(self) -> None:
        self.assertEqual(clean("  kah-ZETH   o'brien "), "kah-ZETH o'brien")
        self.assertEqual(clean("Ka’zeth"), "Ka'zeth")

    def test_a_sentence_a_symbol_a_number_or_nothing_is_refused(self) -> None:
        for text in ("", "   ", "say it: KAH-zeth", "kah/zeth", "zeth2", "'-'", "kah\nzeth!"):
            with self.subTest(text=text):
                self.assertIsNone(clean(text))

    def test_the_limit_is_eighty_characters(self) -> None:
        self.assertEqual(clean("a" * MAX_CHARS), "a" * MAX_CHARS)
        self.assertIsNone(clean("a" * (MAX_CHARS + 1)))


if __name__ == "__main__":
    unittest.main()
