"""PlotBot's name pass on the cleaned transcript (#2628), the pure part: which words are candidates,
what is asked, and which answers are believed. No database, no AI."""

from __future__ import annotations

import json
import unittest

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
            [(d.heard, d.name, d.entity_id) for d in got.sure],
            [("Keirandal", "Kethrandil", KETH)],
        )
        self.assertEqual((got.unsure, got.new), ([], []))

    def test_new_is_new_and_every_other_answer_is_unsure_and_changes_nothing(self) -> None:
        got = np.decide(self.answer({"n": 1, "answer": "new", "name": ""}), self.cands)
        self.assertEqual((got.sure, len(got.new), got.unsure), ([], 1, []))
        for item in (
            {"n": 1, "answer": "unsure", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Drenwick"},  # not offered for this word
            {"n": 1, "answer": "is", "name": "Silas Vane"},  # never offered
            {"n": 2, "answer": "is", "name": "Kethrandil"},  # no such word: this one has no answer
            {"n": "1", "answer": "is", "name": "Kethrandil"},
            "nonsense",
        ):
            got = np.decide(self.answer(item), self.cands)  # type: ignore[arg-type]
            self.assertEqual((got.sure, got.new, len(got.unsure)), ([], [], 1), item)
        for raw in ("", "not json", "[]", '{"answers": 5}', '{"x": 1}'):
            got = np.decide(raw, self.cands)
            self.assertEqual((got.sure, got.new, len(got.unsure)), ([], [], 1), raw)

    def test_a_word_is_fixed_once(self) -> None:
        twice = self.answer(
            {"n": 1, "answer": "is", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Kethrandil"},
        )
        self.assertEqual(len(np.decide(twice, self.cands).sure), 1)


class Reporting(unittest.TestCase):
    def test_forms_are_counted_without_case_and_shown_most_heard_first(self) -> None:
        got = np.group_forms([("Keirandal", 3), ("keirandal", 2), ("Kerendal", 4)])
        self.assertEqual(got, (("Keirandal", 5), ("Kerendal", 4)))

    def test_the_report_names_each_fix_its_spellings_and_what_was_asked(self) -> None:
        near = np.Near(KETH, "Kethrandil", "player character", 0.7)
        report = np.Report(
            fixed=(np.Fixed("Kethrandil", (("Keith and Dill", 4), ("Keth randil", 3)), 5),),
            unsure=(
                np.Candidate("Kerran", (), 2, (near, np.Near(DREN, "Keirran", "npc", 0.6)), ()),
            ),
            new=1,
            guarded=0,
        )
        self.assertEqual(
            np.report_text(report),
            "**Names fixed this session**\n"
            "Kethrandil: corrected 7 times\n"
            "  (4) Keith and Dill\n"
            "  (3) Keth randil\n"
            "Not sure (asked the DM): 1\n"
            "  (2) Kerran → Kethrandil or Keirran?\n"
            "New names (asked the DM): 1\n"
            "Left as heard (secret-name guard): 0",
        )

    def test_the_counts_line_has_no_spelling_in_it(self) -> None:
        report = np.Report(fixed=(np.Fixed("Kethrandil", (("Keith and Dill", 4),), 5),), new=2)
        line = np.counts_line(report)
        self.assertEqual(line, "1 names, 4 fixes, 0 unsure, 2 new, 0 guarded")
        self.assertNotIn("Keith", line)
        self.assertNotIn("Kethrandil", line)

    def test_a_quiet_report_is_quiet(self) -> None:
        self.assertTrue(np.Report().quiet)
        self.assertFalse(np.Report(guarded=1).quiet)

    def test_batches_differ_by_name_and_session_and_are_stable(self) -> None:
        self.assertEqual(np.batch_of("s1", "a"), np.batch_of("s1", "a"))
        self.assertNotEqual(np.batch_of("s1", "a"), np.batch_of("s1", "b"))
        self.assertNotEqual(np.batch_of("s1", "a"), np.batch_of("s2", "a"))
        self.assertGreaterEqual(np.batch_of("s1", "a"), np.BATCH_BASE)
