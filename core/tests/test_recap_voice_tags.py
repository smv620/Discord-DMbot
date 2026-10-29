"""The recap's mood cues reach the voice as delivery tags (#1983): a fixed table, one tag where
the mood changes, a switch to leave them out, and nothing a player or model wrote ever a tag. No
network and no paid call."""

from __future__ import annotations

import re
import unittest

from dmbot.config import ConfigError, load_settings
from dmbot.recaps.audio import music, script
from dmbot.recaps.audio.script import EMOTION_VOICE
from tests.test_config import BASE

ALLOWED = {tag for tag in EMOTION_VOICE.values() if tag} | {"[whispers]", "[sighs]"}
TAG = re.compile(r"\[[^\]]+\]")


def tags_of(summary: str, *, emotion_tags: bool = True) -> list[str]:
    built = script.build("The Gate", None, summary, emotion_tags=emotion_tags)
    return [tag for p in built.paragraphs for tag in TAG.findall(p.tagged)]


class TheTable(unittest.TestCase):
    def test_every_mood_the_music_knows_has_a_tag_or_an_explicit_music_only(self) -> None:
        self.assertEqual(set(EMOTION_VOICE) - set(music.STYLES), set())  # nothing the music lacks
        self.assertEqual(set(music.STYLES) - set(EMOTION_VOICE), set())  # nothing left unsaid

    def test_every_synonym_lands_on_a_mood_with_an_entry(self) -> None:
        for word, mood in music.SYNONYMS.items():
            self.assertIn(mood, EMOTION_VOICE, word)

    def test_the_issues_starting_map_is_what_is_sent(self) -> None:
        for word, tag in {
            "tense": "[urgently]", "urgent": "[urgently]", "battle": "[urgently]",
            "somber": "[sadly]", "sorrowful": "[sadly]", "melancholy": "[sadly]",
            "hopeful": "[softly]", "tender": "[softly]",
            "triumphant": "[confidently]", "defiant": "[confidently]",
            "mysterious": "[quietly]", "eerie": "[quietly]", "ominous": "[quietly]",
            "dread": "[quietly]", "playful": "[playfully]", "wonder": "[in awe]",
        }.items():  # fmt: skip
            self.assertEqual(tags_of(f"《{word}》 Rain fell on the road."), [tag], word)
        for word in ("calm", "grim"):
            self.assertEqual(tags_of(f"《{word}》 Rain fell on the road."), [], word)

    def test_a_synonym_uses_its_moods_tag(self) -> None:
        self.assertEqual(tags_of("《suspense》 A door creaked."), ["[urgently]"])
        self.assertEqual(tags_of("《grief》 A bell tolled."), ["[sadly]"])


class TheRuns(unittest.TestCase):
    def test_a_tag_is_sent_once_for_a_run_of_the_same_mood(self) -> None:
        summary = (
            "《Tense》 The door opened.\n\n《tension》 A step sounded.\n\nNo cue here.\n\n"
            "《Somber》 Nobody spoke.\n\n《Tense》 Then steel rang."
        )
        # tense, (tension: same mood, none), (no cue: same mood), somber, tense again
        self.assertEqual(tags_of(summary), ["[urgently]", "[sadly]", "[urgently]"])

    def test_the_tag_sits_at_the_start_of_the_paragraph_its_cue_opens(self) -> None:
        built = script.build("The Gate", None, "《Tense》 The door opened.\n\nIt swung wide.")
        self.assertEqual(built.paragraphs[1].tagged, "[urgently] The door opened.")
        self.assertEqual(built.paragraphs[2].tagged, "It swung wide.")

    def test_a_performance_cues_tag_still_wins_at_its_place(self) -> None:
        built = script.build(
            "The Gate", None, "《Tense》 The door opened ((long pause)) and stopped."
        )
        self.assertEqual(
            built.paragraphs[1].tagged, "[urgently] The door opened [long pause] and stopped."
        )


class TheSwitch(unittest.TestCase):
    def test_off_sends_only_whisper_sigh_and_pauses(self) -> None:
        summary = (
            "《Tense》 The door opened ((pause)) wide.\n\n《whisper》 She spoke.\n\n"
            "《Somber》 Then wept."
        )
        self.assertEqual(tags_of(summary, emotion_tags=False), ["[short pause]", "[whispers]"])
        self.assertEqual(tags_of(summary), ["[urgently]", "[short pause]", "[whispers]", "[sadly]"])

    def test_the_cue_map_keeps_the_cue_with_no_tag_when_off(self) -> None:
        built = script.build("The Gate", None, "《Tense》 The door opened.", emotion_tags=False)
        (cue,) = built.cues
        self.assertEqual((cue.kind, cue.name, cue.tag), (script.EMOTION, "tense", ""))

    def test_the_setting_defaults_on_reads_off_and_refuses_nonsense(self) -> None:
        self.assertTrue(load_settings(BASE).recap_voice_tags)
        self.assertFalse(load_settings({**BASE, "DMBOT_RECAP_VOICE_TAGS": "0"}).recap_voice_tags)
        self.assertTrue(load_settings({**BASE, "DMBOT_RECAP_VOICE_TAGS": "on"}).recap_voice_tags)
        with self.assertRaisesRegex(ConfigError, "DMBOT_RECAP_VOICE_TAGS must be 1"):
            load_settings({**BASE, "DMBOT_RECAP_VOICE_TAGS": "loud"})


class NothingWrittenBecomesATag(unittest.TestCase):
    def test_an_unknown_word_gives_no_tag_and_the_words_are_never_sent(self) -> None:
        for word in ("furious", "[shouts]", "ignore the rules", "x" * 40, "tense [laughs]"):
            sent = tags_of(f"《{word}》 The door opened.")
            self.assertEqual(sent, [], word)
            built = script.build("The Gate", None, f"《{word}》 The door opened.")
            self.assertNotIn(word, " ".join(p.tagged for p in built.paragraphs[1:]), word)

    def test_every_tag_sent_is_from_the_table(self) -> None:
        words = [*music.STYLES, *music.SYNONYMS, "whisper", "sigh", "nonsense", "[urgently]"]
        summary = "\n\n".join(f"《{w}》 Words here." for w in words)
        self.assertLessEqual(set(tags_of(summary)), ALLOWED)


class TheEdges(unittest.TestCase):
    def test_a_cue_alone_in_a_paragraph_does_not_use_up_its_mood(self) -> None:
        self.assertEqual(tags_of("《Tense》\n\n《Tense》 The door opened."), ["[urgently]"])

    def test_a_mood_cue_at_the_end_of_a_paragraph_sends_no_tag_and_keeps_the_mood_open(
        self,
    ) -> None:
        built = script.build("The Gate", None, "He fell. 《Somber》\n\n《Somber》 Nobody spoke.")
        self.assertEqual(built.paragraphs[1].tagged, "He fell.")  # no tag with nothing after it
        self.assertEqual(built.paragraphs[2].tagged, "[sadly] Nobody spoke.")
        (trailing,) = built.paragraphs[1].cues
        self.assertEqual((trailing.name, trailing.tag), ("somber", ""))

    def test_a_calm_between_two_tense_runs_lets_the_second_send_its_tag_again(self) -> None:
        summary = "《Tense》 One.\n\n《Calm》 Two.\n\n《Tense》 Three."
        self.assertEqual(tags_of(summary), ["[urgently]", "[urgently]"])
