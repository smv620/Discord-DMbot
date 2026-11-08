"""Speech sounds to a plain respelling (#2316): made-up names, the stress rule, the syllable
breaks, and that whatever comes out passes the same check as a spelling the DM types."""

from __future__ import annotations

import unittest

from dmbot.said_as.respell import MAX_LENGTH, respell
from dmbot.web.names import _SPELLING, SAID_AS_MAX

# (the sounds a recogniser might write, the respelling). All names are invented.
NAMES = [
    ("kɑˈzɛθ", "kah-ZETH"),  # the example in the plan
    ("ˈkɑzɛθ", "KAH-zeth"),
    ("kɑzɛθ", "KAH-zeth"),  # no stress marks: the first syllable of two or more
    ("ˈbɹænəwɛn", "BRA-nuh-wen"),
    ("ɑtɹi", "AH-tree"),  # "tr" starts a word, so it stays together
    ("ˈsɛlɪn", "SEH-lin"),
    ("ɛlˈdɹɪtʃ", "el-DRICH"),
    ("ʃɹɪk", "shrik"),  # one syllable: no capitals
    ("maɪ", "my"),
    ("ˈkaɪɹən", "KY-ruhn"),
    ("θɔɹˈvɑld", "thawr-VAHLD"),
    ("ˈmɛɹɪdʒ", "MEH-rij"),
    ("zɑˈniːɹ", "zah-NEER"),
    ("ˈʒɑnə", "ZHAH-nuh"),
    ("ˈvɛlkɹu", "VEL-kroo"),
    ("ˌtɑɹˈsɪs", "tahr-SIS"),
    ("ˈoʊmɹɪn", "OHM-rin"),
    ("ˈdɹeɪvɛn", "DRAY-ven"),
    ("ˈʊlfɡɑɹ", "UULF-gahr"),
    ("ɪˈzɑbɛl", "ih-ZAH-bel"),
    ("ˈhɑkʌn", "HAH-kun"),
    ("nɪˈmæθ", "nih-MATH"),
    ("ˈɡɹɔɪd", "GROYD"),
    ("ˈŋɑɾu", "NGAH-roo"),
]


class Respelling(unittest.TestCase):
    def test_made_up_names(self) -> None:
        for sounds, expected in NAMES:
            with self.subTest(sounds=sounds):
                self.assertEqual(respell(sounds), expected)

    def test_every_answer_passes_the_check_for_a_typed_spelling(self) -> None:
        for sounds, _expected in NAMES:
            with self.subTest(sounds=sounds):
                answer = respell(sounds)
                self.assertTrue(_SPELLING.fullmatch(answer), answer)
                self.assertLessEqual(len(answer), MAX_LENGTH)

    def test_the_stress_is_the_mark_or_else_the_first_of_two_or_more(self) -> None:
        self.assertEqual(respell("ˈkɑzɛθ"), "KAH-zeth")
        self.assertEqual(respell("kɑˈzɛθ"), "kah-ZETH")
        self.assertEqual(respell("kɑzɛθ"), "KAH-zeth")
        self.assertEqual(respell("kɑt"), "kaht")  # a single syllable has nothing to stress
        self.assertEqual(respell("ˌkɑˈzɛθ"), "kah-ZETH")  # secondary stress is not capitalised

    def test_a_clip_with_no_vowel_gives_nothing(self) -> None:
        for sounds in ("", "   ", "stɹ", "ʃ", "ˈ", "???"):
            with self.subTest(sounds=sounds):
                self.assertEqual(respell(sounds), "")

    def test_unknown_signs_are_left_out_not_guessed(self) -> None:
        self.assertEqual(respell("kɑ#zɛ1θ"), "KAH-zeth")
        self.assertEqual(respell("kɑː.zɛθ"), "KAH-zeth")  # a length mark and a dot change nothing

    def test_several_words_are_each_respelled(self) -> None:
        self.assertEqual(respell("ˈkɑzɛθ ðə ˈɡɹeɪ"), "KAH-zeth thuh GRAY")

    def test_the_answer_is_cut_to_the_limit_without_a_dangling_hyphen(self) -> None:
        long = " ".join(["ˈkɑzɛθ"] * 30)
        answer = respell(long)
        self.assertLessEqual(len(answer), MAX_LENGTH)
        self.assertFalse(answer.endswith(("-", " ")))
        self.assertTrue(_SPELLING.fullmatch(answer), answer)

    def test_a_hostile_string_is_harmless(self) -> None:
        self.assertEqual(respell("\x00\x07<#>kɑ</#>"), "kah")
        self.assertTrue(respell("ɑ" * 10_000).startswith("AH-"))

    def test_a_diphthong_that_already_says_r_is_not_doubled(self) -> None:
        self.assertEqual(respell("ˈkɛəɹ"), "KAIR")
        self.assertEqual(respell("ˈbɪəɹ"), "BEER")

    def test_the_tie_bar_joins_ch_and_j(self) -> None:
        self.assertEqual(respell("ɑt͡ʃɑ"), "AH-chah")
        self.assertEqual(respell("ɑd͡ʒɑ"), "AH-jah")

    def test_clusters_split_or_stay_together_as_english_does(self) -> None:
        self.assertEqual(respell("ɑstɹɑ"), "AHS-trah")  # three: "s" stays behind, "tr" goes on
        self.assertEqual(respell("ɑtlɑ"), "AHT-lah")  # "tl" cannot start an English word
        self.assertEqual(respell("ɑdwɑ"), "AHD-wah")
        for sounds, expected in (("ɑsmɑ", "AH-smah"), ("ɑkwɑ", "AH-kwah"), ("ɑslɑ", "AH-slah")):
            self.assertEqual(respell(sounds), expected)

    def test_sounds_foreign_to_english_get_a_near_letter(self) -> None:
        self.assertEqual(respell("ɑʁɑ"), "AH-rah")
        self.assertEqual(respell("ɑɲɑ"), "AH-nyah")

    def test_a_stress_mark_on_one_syllable_and_several_marks(self) -> None:
        self.assertEqual(respell("ˈkɑt"), "KAHT")  # a mark is the model's word; it is capitalised
        self.assertEqual(respell("ˈkɑˈzɛθ"), "KAH-zeth")  # the first mark wins

    def test_the_cut_is_at_a_whole_syllable_and_the_input_is_capped(self) -> None:
        long = " ".join(["ˈkɑzɛθ"] * 30)
        self.assertEqual(respell(long), " ".join(["KAH-zeth"] * 9))  # 80 characters
        huge = "ɑ" * 1_000_000
        answer = respell(huge)
        self.assertLessEqual(len(answer), MAX_LENGTH)
        self.assertTrue(_SPELLING.fullmatch(answer), answer)

    def test_the_limit_matches_the_names_page(self) -> None:
        self.assertEqual(MAX_LENGTH, SAID_AS_MAX)
