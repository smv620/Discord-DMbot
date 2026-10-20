"""The recap fact check's pure parts (#2064): numbered sentences, what the answer may say, the
revision message, the cut in code, the places on beats, and the report. Invented fixtures only."""

from __future__ import annotations

import json
import unittest
from typing import ClassVar

from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import beats as bt
from dmbot.recaps import calls as cl
from dmbot.recaps import draft as rd
from tests import recap_fixtures as fx
from tests.test_recap_calls import calls_section, inputs

LINES = {1: "Mira gave up her sword at the keep's gate"}
TEXT = (
    "《Tense》 ((slow)) The party reached the old tower at dusk. They went north. "
    "《Calm》 A fire was lit. Everyone gave up a blade."
)


class Sentences(unittest.TestCase):
    def test_they_are_numbered_without_their_cues(self) -> None:
        self.assertEqual(
            rd.sentences_of(TEXT),
            [
                "The party reached the old tower at dusk.",
                "They went north.",
                "A fire was lit.",
                "Everyone gave up a blade.",
            ],
        )

    def test_a_cut_takes_the_sentence_and_keeps_the_mood(self) -> None:
        cut = rd.cut_sentences(rd.Prose(TEXT, ("one", "two")), [2])
        self.assertNotIn("north", cut.summary)
        self.assertIn("《Calm》 A fire was lit.", cut.summary)
        self.assertEqual(cut.music, ("one", "two"))

    def test_a_section_left_with_no_words_goes_with_its_music(self) -> None:
        cut = rd.cut_sentences(rd.Prose(TEXT, ("one", "two")), [3, 4])
        self.assertNotIn("Calm", cut.summary)
        self.assertEqual(cut.music, ("one",))

    def test_a_number_that_names_no_sentence_cuts_nothing(self) -> None:
        self.assertEqual(rd.cut_sentences(rd.Prose(TEXT, ("a", "b")), [0, 9]).summary, TEXT)


class TheSplit(unittest.TestCase):
    def test_a_title_or_a_number_does_not_end_a_sentence(self) -> None:
        text = "《Tense》 Mr. Hale spoke at 3.5 bells. Dr. Vex nodded! Then it ended."
        self.assertEqual(
            rd.sentences_of(text),
            ["Mr. Hale spoke at 3.5 bells.", "Dr. Vex nodded!", "Then it ended."],
        )

    def test_a_stop_inside_a_cue_does_not_split_it_and_a_cut_keeps_it_whole(self) -> None:
        text = "《Tense》 One thing. ((slow. very)) Two things. Three things."
        self.assertEqual(len(rd.sentences_of(text)), 3)
        cut = rd.cut_sentences(rd.Prose(text, ("m",)), [3])
        self.assertIn("((slow. very)) Two things.", cut.summary)
        self.assertNotIn("Three things", cut.summary)

    def test_an_empty_summary_has_no_sentences_and_no_flag_survives(self) -> None:
        self.assertEqual(rd.sentences_of("《Calm》"), [])
        facts = rd.parse_facts(fx.facts_answer([(1, "place")]), 0, {1})
        assert facts is not None
        self.assertTrue(facts.clean)


class TheDirections(unittest.TestCase):
    NAMES = ("North Gate", "Westerly Inn", "Mira", "Northam")

    def found(self, text: str, names: tuple[str, ...] = ()) -> list[int]:
        return rd.stated_directions(f"《Calm》 {text}", names)

    def test_a_compass_direction_is_flagged_by_its_sentence_number(self) -> None:
        self.assertEqual(self.found("They rode north to the ferry. Then they rested."), [1])
        self.assertEqual(self.found("They rested. The wind came from the South."), [2])

    def test_every_form_counts(self) -> None:
        for text in (
            "Eastward lay the hills.",
            "A northern road.",
            "Westerly winds.",
            "They went north-east.",
            "The Southwest gate was shut.",
            "A southbound cart.",
            "They walked westwards.",
        ):
            self.assertEqual(self.found(text), [1], text)

    def test_ordinary_words_and_longer_names_are_not_directions(self) -> None:
        for text in ("They reached Northam at dusk.", "Easter bells rang.", "A wasteland."):
            self.assertEqual(self.found(text), [], text)

    def test_a_direction_in_a_listed_name_is_not_one_but_a_bare_one_still_is(self) -> None:
        self.assertEqual(self.found("They reached the North Gate.", self.NAMES), [])
        self.assertEqual(self.found("They slept at the Westerly Inn.", self.NAMES), [])
        self.assertEqual(self.found("Mira's blade from the North Gate's guard.", self.NAMES), [])
        both = "They left the North Gate and rode north."
        self.assertEqual(self.found(both, self.NAMES), [1])

    def test_a_lone_direction_word_in_the_names_hides_nothing(self) -> None:
        self.assertEqual(self.found("They rode west at dawn.", ("West",)), [1])
        self.assertEqual(self.found("The Westerners rode on."), [1])

    def test_the_code_flags_join_the_fact_check_as_one_flag_per_sentence(self) -> None:
        text = "《Calm》 They rode north. A fire was lit. They went west."
        asked = rd.Facts(((1, "invented"), (2, "place")), (4,))
        merged = rd.with_directions(asked, text, ())
        assert merged is not None
        self.assertEqual(merged.unsupported, ((1, "invented"), (2, "place"), (3, "direction")))
        self.assertEqual(merged.missing, (4,))
        alone = rd.with_directions(None, text, ())
        assert alone is not None
        self.assertEqual(alone.unsupported, ((1, "direction"), (3, "direction")))
        self.assertIsNone(rd.with_directions(None, "《Calm》 A fire was lit.", ()))
        self.assertIs(rd.with_directions(asked, "《Calm》 A fire was lit.", ()), asked)


class TheAnswer(unittest.TestCase):
    def test_it_is_read_and_numbers_that_name_nothing_are_dropped(self) -> None:
        raw = fx.facts_answer([(2, "direction"), (2, "place"), (9, "place")], missing=[1, 7])
        facts = rd.parse_facts(raw, 4, {1, 2, 3})
        assert facts is not None
        self.assertEqual(facts.unsupported, ((2, "direction"),))
        self.assertEqual(facts.missing, (1,))
        self.assertEqual(facts.counts(), {"direction": 1, "missing": 1})
        self.assertEqual(facts.score(), 3)

    def test_a_kind_outside_the_list_or_a_bad_shape_is_not_an_answer(self) -> None:
        bad = json.dumps({"unsupported": [{"sentence": 1, "kind": "vibes"}], "missing": []})
        facts = rd.parse_facts(bad, 4, {1})
        assert facts is not None
        self.assertTrue(facts.clean)
        self.assertIsNone(rd.parse_facts("{", 4, {1}))
        self.assertIsNone(rd.parse_facts('{"unsupported":1,"missing":[]}', 4, {1}))

    def test_nothing_flagged_scores_five_and_many_flags_not_below_one(self) -> None:
        self.assertEqual(rd.Facts().score(), 5)
        many = rd.Facts(tuple((n, "invented") for n in range(1, 9)))
        self.assertEqual(many.score(), 1)

    def test_the_revision_names_sentences_beats_and_kinds(self) -> None:
        facts = rd.Facts(((2, "direction"),), (3,))
        problems = rd.facts_problems(facts, TEXT)
        self.assertEqual(
            problems[0],
            "sentence 2 says something the beats do not say (direction): They went north.",
        )
        self.assertIn("leaves out beat(s) 3", problems[1])
        self.assertEqual(cl.kinds(problems), ["facts"])

    def test_a_flagged_sentence_that_mentions_words_is_still_the_facts_kind(self) -> None:
        facts = rd.Facts(((1, "invented"),))
        text = "The words were many and no quotation marks were used."
        self.assertEqual(cl.kinds(rd.facts_problems(facts, text)), ["facts"])


class ThePlace(unittest.TestCase):
    RAW: ClassVar[dict[str, object]] = {
        "what": "The party was disarmed",
        "who": ["Mira"],
        "lines": [1],
        "kind": "event",
    }

    def check(self, **extra: object) -> list[bt.Beat]:
        raws = [{**self.RAW, **extra}] * 3
        kept, _ = bt.check(
            raws, lines=LINES, speakers={1: fx.DM}, allowed_names=["Mira"],
            secret_names=[],
        )  # fmt: skip
        return kept

    def test_a_beat_carries_its_place_and_the_sheet_shows_it(self) -> None:
        beat = self.check(where="  the keep's   gate ")[0]
        self.assertEqual(beat.where, "the keep's gate")
        self.assertIn("Where: the keep's gate.", bt.sheet([beat]))

    def test_no_place_is_empty_and_not_shown(self) -> None:
        beat = self.check()[0]
        self.assertEqual(beat.where, "")
        self.assertNotIn("Where", bt.sheet([beat]))

    def test_a_long_place_is_cut_and_a_stored_beat_without_one_still_loads(self) -> None:
        self.assertEqual(len(self.check(where="gate " * 100)[0].where), bt.WHERE_MAX)
        old = [{"what": "w", "who": [], "lines": [1], "kind": "event"}]
        self.assertEqual(bt.from_json(old)[0].where, "")
        kept = self.check(where="the gate")
        self.assertEqual(bt.from_json(bt.as_json(kept)), kept)

    def test_a_place_that_is_secret_or_never_said_is_dropped_and_the_beat_stays(self) -> None:
        kept, _ = bt.check(
            [{**self.RAW, "where": "the Hidden Vault"}] * 3,
            lines=LINES, speakers={1: fx.DM}, allowed_names=["Mira"],
            secret_names=["Hidden Vault"],
        )  # fmt: skip
        self.assertEqual([b.where for b in kept], ["", "", ""])
        self.assertEqual(len(kept), 3)
        self.assertEqual(self.check(where="a tavern in the north")[0].where, "")  # never said

    def test_the_beat_prompt_asks_for_the_place_and_the_writer_may_not_move_it(self) -> None:
        self.assertIn('"where"', bt.system())
        self.assertIn("Never move an event to another place", rd.prose_system(None))
        self.assertIn("Never say", rd.prose_system(None))


class TheReport(unittest.IsolatedAsyncioTestCase):
    async def test_the_facts_line_has_counts_and_no_text_of_the_session(self) -> None:
        n = next(i for i, t in enumerate(rd.sentences_of(fx.SUMMARY), 1) if "torch burned" in t)
        careful = fx.Canned(
            fx.PROSE_ANSWER,
            facts=[
                fx.facts_answer([(n, "place"), (n + 1, "direction")], missing=[2]),
                fx.CLEAN_FACTS,
            ],
        )
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        out = await run.run_text(inputs(), fast, careful)
        rows = calls_section(out)
        facts = [r for r in rows if r.strip().startswith("facts try")]
        self.assertEqual(len(facts), 2)
        self.assertIn("flagged: place 1, direction 1, missing 1", facts[0])
        self.assertIn("flagged: nothing", facts[1])
        section = "\n".join(rows)
        for words in ("torch", "Greywater", "Mira", "burned"):
            self.assertNotIn(words, section)  # counts only: no word of the session or the recap

    async def test_the_calls_section_counts_the_directions_found_in_code(self) -> None:
        prose = fx.prose_answer(
            fx.SUMMARY.replace("The torch burned down a little.", "They rode north to the ferry.")
        )
        careful = fx.Canned(prose, facts=[fx.CLEAN_FACTS, fx.CLEAN_FACTS])
        fast = fx.Canned(fx.BEATS_ANSWER, prose, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        out = await run.run_text(inputs(), fast, careful)
        facts = [r for r in calls_section(out) if r.strip().startswith("facts try")]
        self.assertIn("flagged: direction 1", facts[0])
        self.assertNotIn("ferry", "\n".join(calls_section(out)))
