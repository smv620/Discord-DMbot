"""The cue check (#2083): each kind of problem is found and says where, a synonym passes, the
allowed words are the ones the audio uses, and nothing is repaired."""

from __future__ import annotations

import unittest

from dmbot.recaps import cues
from dmbot.recaps.audio.music import SYNONYMS
from dmbot.recaps.audio.script import EMOTION_VOICE, build

GOOD = (
    "《Tense》 ((slow)) Dusk settled over the road and Mira drew her blade.\n\n"
    "《Ominous》 The door swung inward ((long pause)) on a dark stair."
)


def kinds(text: str) -> list[str]:
    return [p.kind for p in cues.cue_problems(text)]


class Clean(unittest.TestCase):
    def test_a_good_text_has_no_problems(self) -> None:
        # ((slow)) is not on the list: it is the one problem here.
        self.assertEqual(kinds(GOOD), ["unknown"])
        self.assertEqual(kinds(GOOD.replace("((slow))", "((pause))")), [])

    def test_a_text_with_no_cues_is_clean(self) -> None:
        self.assertEqual(kinds("Just a plain story.\n\nAnd another."), [])

    def test_a_synonym_passes(self) -> None:
        self.assertEqual(kinds("《Tension》 Mira waited."), [])
        self.assertEqual(kinds("《 Sad 》 Mira waited."), [])  # spaces and case do not matter
        self.assertEqual(kinds("《TENSE》 Mira waited ((Whispered)) for him."), [])

    def test_every_allowed_word_passes_and_the_lists_are_the_audios_own(self) -> None:
        for mood in (*cues.MOODS, *SYNONYMS):
            self.assertEqual(kinds(f"《{mood}》 Words."), [], mood)
        for pace in cues.PACING:
            self.assertEqual(kinds("《calm》 Words ((" + pace + ")) more."), [], pace)
        self.assertEqual(len(cues.MOODS), len(EMOTION_VOICE))
        self.assertEqual(len(cues.MOODS), 18)
        self.assertIn("whispered", cues.PACING)
        # The audio reads exactly what the check allows: no allowed pacing word is ignored.
        script = build("T", 1, "《calm》 One ((whispered)) two.", secret_names=[], allowed_names=[])
        self.assertTrue(any(c.name == "whispered" for c in script.cues))


class Unknown(unittest.TestCase):
    def test_an_unknown_mood_is_flagged_with_the_closest_word(self) -> None:
        (p,) = cues.cue_problems("《Tens》 Mira waited.")
        self.assertEqual(
            (p.kind, p.suggest, p.paragraph, p.cue), ("unknown", "tense", 1, "《Tens》")
        )
        self.assertIn("Did you mean tense?", p.words())

    def test_an_unknown_pacing_word_is_flagged_with_the_closest_word(self) -> None:
        (p,) = cues.cue_problems("《calm》 Mira waited ((pausse)) then went.")
        self.assertEqual((p.kind, p.suggest), ("unknown", "pause"))

    def test_a_word_far_from_any_has_no_suggestion(self) -> None:
        (p,) = cues.cue_problems("《xyzzy》 Mira waited.")
        self.assertEqual((p.kind, p.suggest), ("unknown", None))
        self.assertNotIn("Did you mean", p.words())

    def test_a_mood_word_in_pacing_brackets_is_unknown_there(self) -> None:
        self.assertEqual(kinds("《calm》 Mira ((tense)) waited."), ["unknown"])

    def test_a_too_long_cue_is_flagged(self) -> None:
        self.assertEqual(kinds("《" + "a" * 70 + "》 Mira waited."), ["unknown"])


class Brackets(unittest.TestCase):
    def test_an_unclosed_bracket(self) -> None:
        self.assertEqual(kinds("《Tense Mira waited."), ["unclosed"])
        self.assertEqual(kinds("《calm》 Mira ((pause waited."), ["unclosed"])
        self.assertEqual(kinds("《calm》 Mira waited ((pause"), ["unclosed"])

    def test_a_closer_with_no_opener(self) -> None:
        self.assertEqual(kinds("《calm》 Mira waited》 here."), ["unclosed"])
        self.assertEqual(kinds("《calm》 Mira waited)) here."), ["unclosed"])

    def test_a_mismatched_bracket(self) -> None:
        (p,) = cues.cue_problems("《Tense)) Mira waited.")
        self.assertEqual(p.kind, "mismatched")
        self.assertEqual(kinds("《calm》 Mira ((pause》 waited."), ["mismatched"])

    def test_a_cue_opened_inside_another_is_unclosed(self) -> None:
        self.assertEqual(sorted(kinds("《calm ((pause)) Mira waited.")), ["unclosed"])


class Shape(unittest.TestCase):
    def test_an_empty_cue(self) -> None:
        self.assertEqual(kinds("《》 Mira waited."), ["empty"])
        self.assertEqual(kinds("《calm》 Mira (()) waited."), ["empty"])
        self.assertEqual(kinds("《  》 Mira waited."), ["empty"])

    def test_a_mood_with_no_narration_after_it(self) -> None:
        (p,) = cues.cue_problems("《calm》 《tense》 Mira waited.")
        self.assertEqual((p.kind, p.cue), ("no_words", "《calm》"))
        self.assertEqual(kinds("《calm》 Mira waited. 《tense》"), ["no_words"])
        self.assertEqual(kinds("《calm》 ((pause)) 《tense》 Mira waited."), ["no_words"])

    def test_a_mood_at_the_end_of_one_paragraph_may_lead_the_next(self) -> None:
        self.assertEqual(kinds("《calm》\n\nMira waited."), [])

    def test_a_cue_glued_inside_a_word(self) -> None:
        (p,) = cues.cue_problems("Dus《tense》k settled.")
        self.assertEqual(p.kind, "inside_word")
        self.assertEqual(kinds("The gate((pause))way stood."), ["inside_word"])
        self.assertEqual(kinds("《calm》Mira waited."), [])  # touching only one side is fine


class Where(unittest.TestCase):
    def test_the_paragraph_and_the_words_around_it(self) -> None:
        text = "《calm》 One.\n\nTwo words here ((pausse)) then three more words."
        (p,) = cues.cue_problems(text)
        self.assertEqual(p.paragraph, 2)
        self.assertEqual(p.near, "Two words here then three more")
        self.assertTrue(p.words().startswith("Paragraph 2, near “Two words here then three more”"))

    def test_the_allowed_words_are_what_the_website_shows(self) -> None:
        allowed = cues.allowed_words()
        self.assertEqual(allowed["moods"], list(EMOTION_VOICE))
        self.assertEqual(allowed["synonyms"], dict(SYNONYMS))
        self.assertIn("long pause", allowed["pacing"])

    def test_nothing_is_repaired_and_problems_come_in_order(self) -> None:
        text = "《Tens》 One ((pausse)) two."
        before = text
        problems = cues.cue_problems(text)
        self.assertEqual(text, before)
        self.assertEqual([p.kind for p in problems], ["unknown", "unknown"])
        self.assertEqual(problems[0].as_dict()["kind"], "unknown")
