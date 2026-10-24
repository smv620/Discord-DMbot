"""PlotBot's name pass on the cleaned transcript (#2628), the pure part: which words are candidates,
what is asked, and which answers are believed. No database, no AI."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.transcript import name_pass as np
from tests.test_transcript_cleaner import CERRIC, alias, entity

KETH, VANE, DREN = "k" * 32, "v" * 32, "d" * 32


def make(*, secret: bool = True) -> CampaignLookup:
    entities = [
        entity(KETH, "Kethrandil", "player_character", played_by=7),
        entity(DREN, "Drenwick", "npc"),
        entity(CERRIC, "Cerric", "npc"),
    ]
    aliases = [alias(KETH, "Kethrandil"), alias(DREN, "Drenwick"), alias(CERRIC, "Cerric")]
    if secret:
        entities.append(entity(VANE, "Silas Vane", "npc"))
        aliases.append(alias(VANE, "Silas Vane", secret=True))
    return CampaignLookup.build(LookupData(1, tuple(entities), tuple(aliases), (), (), (), ()))


LINES = ["Keirandal drew his bow.", "Then Keirandal said no.", "We followed Keirandal home."]


class Finding(unittest.TestCase):
    def test_a_misheard_list_name_is_a_candidate_with_its_list_name_near(self) -> None:
        got = np.candidates(LINES, make())
        self.assertEqual([c.heard for c in got], ["Keirandal"])
        self.assertEqual((got[0].times, got[0].near[0].name), (3, "Kethrandil"))
        self.assertEqual(got[0].near[0].kind, "player character")
        self.assertTrue(all("Keirandal" in line for line in got[0].lines))

    def test_the_list_name_itself_an_ordinary_word_and_a_far_name_are_not(self) -> None:
        lines = ["Kethrandil rode on.", "Kindly step aside.", "Then Brannoch laughed."]
        self.assertEqual(np.candidates(lines, make()), [])

    def test_a_word_close_to_a_secret_name_is_never_a_candidate(self) -> None:
        lines = ["Silas Vain waved.", "Silas Vain left.", "Then Silas Vain sat."]
        self.assertEqual(np.candidates(lines, make()), [])
        # the same word is one when there is no secret to be near
        near_a_public = ["Drenwik nodded.", "Then Drenwik left.", "Drenwik sat."]
        self.assertEqual([c.heard for c in np.candidates(near_a_public, make())], ["Drenwik"])

    def test_a_word_for_a_person_at_the_table_is_left_alone(self) -> None:
        lines = ["Keirandal rolled.", "Then Keirandal rolled again.", "Keirandal won."]
        self.assertEqual(np.candidates(lines, make(), people=["keirandal"]), [])

    def test_a_kept_as_heard_word_is_left_alone(self) -> None:
        base = make()
        kept = CampaignLookup.__dataclass_fields__  # (a lookup with the word kept: below)
        self.assertTrue(kept)
        import dataclasses

        lookup = dataclasses.replace(base, keep_keys=frozenset({"keirandal"}))
        self.assertEqual(np.candidates(LINES, lookup), [])


class Asking(unittest.TestCase):
    def test_the_question_has_the_word_the_list_names_and_a_short_line_only(self) -> None:
        got = np.candidates(LINES, make())
        text = np.question(got)
        self.assertIn('"Keirandal"', text)
        self.assertIn("Kethrandil (player character)", text)
        self.assertNotIn("Silas Vane", text)  # a secret name is never in a prompt
        self.assertNotIn("Drenwick", text)  # nor a list name that is not near


class Believing(unittest.TestCase):
    def setUp(self) -> None:
        self.cands = np.candidates(LINES, make())

    def answer(self, *items: dict[str, object]) -> str:
        return json.dumps({"answers": list(items)})

    def test_a_sure_answer_naming_an_offered_name_is_a_fix(self) -> None:
        got = np.decide(self.answer({"n": 1, "answer": "is", "name": "Kethrandil"}), self.cands)
        self.assertEqual(
            [(d.heard, d.name, d.entity_id) for d in got], [("Keirandal", "Kethrandil", KETH)]
        )

    def test_new_unsure_and_every_other_answer_changes_nothing(self) -> None:
        for item in (
            {"n": 1, "answer": "new", "name": ""},
            {"n": 1, "answer": "unsure", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Drenwick"},  # not offered for this word
            {"n": 1, "answer": "is", "name": "Silas Vane"},  # never offered
            {"n": 2, "answer": "is", "name": "Kethrandil"},  # no such word
            {"n": "1", "answer": "is", "name": "Kethrandil"},
            "nonsense",
        ):
            self.assertEqual(np.decide(self.answer(item), self.cands), [], item)  # type: ignore[arg-type]
        for raw in ("", "not json", "[]", '{"answers": 5}', '{"x": 1}'):
            self.assertEqual(np.decide(raw, self.cands), [], raw)

    def test_a_word_is_fixed_once(self) -> None:
        twice = self.answer(
            {"n": 1, "answer": "is", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Kethrandil"},
        )
        self.assertEqual(len(np.decide(twice, self.cands)), 1)

    def test_the_note_names_counts_and_spellings_only(self) -> None:
        self.assertEqual(
            np.note([("Kethrandil", "Keirandal", 12)]),
            "Fixed 12 spellings of Kethrandil (heard as Keirandal).",
        )


class TheRecapsRespell(unittest.TestCase):
    """The recap's own spelling safety net takes a spelling only from the list (#2628)."""

    def beat(self, who: str) -> Any:
        from dmbot.recaps import beats as bt

        return bt.Beat(1, "Someone drew a bow at dawn.", (who,), (1,), "event")

    def run_it(self, text: str, who: str, names: list[str]) -> str:
        from dmbot.recaps import draft as rd

        prose, _refused = rd.respelled(
            rd.Prose(text, ()),
            beats=[self.beat(who)],
            allowed_names=names,
            secret_names=[],
        )
        return prose.summary

    def test_a_beats_spelling_is_never_the_one_put_in(self) -> None:
        # the beats carry a misheard spelling: it is not a target, so the list's name stays
        # as the only thing a near miss can become
        got = self.run_it("《Calm》 Kethrandill drew his bow.", "Kethrandyl", ["Kethrandil"])
        self.assertEqual(got, "《Calm》 Kethrandil drew his bow.")

    def test_a_list_name_is_still_put_in_for_a_near_miss(self) -> None:
        got = self.run_it("《Calm》 Kethrandill rode on.", "Someone", ["Kethrandil"])
        self.assertEqual(got, "《Calm》 Kethrandil rode on.")
