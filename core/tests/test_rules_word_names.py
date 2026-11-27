"""A rules word used as a name (#5214 part 2a): offered only when the name reader says the lines use
it as a name (`sense`), heard only in the lines it cited with no roll after them, and at most
`UNSURE_CAP` unsure ones a session, after every other suggestion. The list only ever refuses; the
reader's word only decides whether the DM is asked. Pure, on invented lines
(`rules_word_cases.txt`): the reader's answers are written here, no AI is called."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, cast

from dmbot.memory import name_groups as ng
from dmbot.memory import name_reader as nr

RULES = ("2024", "2014")  # (the campaign's target and fallback rulesets: the default ones)
CASES = Path(__file__).parent / "fixtures" / "story_graph" / "rules_word_cases.txt"


def lines() -> dict[str, str]:
    said = [x for x in CASES.read_text().splitlines() if not x.startswith("#")]
    return {str(i): t for i, t in enumerate(said, 1)}


def item(name: str, kind: str, why: str, at: list[int], sense: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "kind": kind, "why": why, "lines": at}
    if sense is not None:
        out["sense"] = sense
    return out


def read(*items: dict[str, Any]) -> tuple[list[nr.Found], dict[str, str]]:
    """The reader's answer over the whole fixture, checked and tallied as the session step does."""
    said = lines()
    parsed = nr.parse(json.dumps({"names": list(items)}), said, {}, rules=RULES)
    kept, _dropped = nr.tally(parsed.heard, said)
    reasons = {o.name: o.reason for o in parsed.outcomes}
    return kept, reasons


class TheFiveCases(unittest.TestCase):
    def test_a_ship_introduced_as_the_insight_then_sailed_on_is_offered(self) -> None:
        kept, _ = read(item("insight", "item", "introduced", [1, 2, 3], "name"))
        self.assertEqual([(f.name, f.times, f.sense) for f in kept], [("insight", 3, "name")])

    def test_a_guide_addressed_as_arcana_is_offered(self) -> None:
        kept, _ = read(item("arcana", "person", "addressed", [4, 5], "name"))
        self.assertEqual([f.name for f in kept], ["arcana"])

    def test_perception_said_ten_times_as_a_skill_is_refused(self) -> None:
        # (the reader leaves it out, or lists it with no sense: both end as a rules word)
        kept, why = read(item("perception", "idea", "spoken_of", [6, 7, 8, 9, 10]))
        self.assertEqual(kept, [])
        self.assertEqual(why, {"perception": nr.RULES_WORD})

    def test_a_cat_called_stealth_is_offered_once_and_the_skill_lines_are_not_hearings(
        self,
    ) -> None:
        kept, _ = read(item("stealth", "person", "introduced", [16, 18, 19, 20], "name"))
        (cat,) = kept
        # (16 and 20 have a roll after it; 17, "your stealth is good", was never cited)
        self.assertEqual((cat.name, cat.lines), ("stealth", ("18", "19")))

    def test_owlbear_as_a_kind_of_monster_is_refused(self) -> None:
        kept, why = read(item("owlbear", "person", "spoken_of", [21, 22]))
        self.assertEqual(kept, [])
        self.assertEqual(why, {"owlbear": nr.RULES_WORD})


class TheRules(unittest.TestCase):
    def test_a_sense_that_is_not_one_of_the_two_words_is_refused(self) -> None:
        kept, why = read(item("insight", "item", "introduced", [1, 2], "maybe"))
        self.assertEqual((kept, why), ([], {"insight": nr.RULES_WORD}))

    def test_a_sense_on_an_ordinary_name_changes_nothing(self) -> None:
        kept, _ = read(item("reef", "place", "place", [3], "name"))
        self.assertEqual(kept, [])  # (one line: a place needs two, sense or not)

    def test_only_roll_lines_cited_is_a_rules_word(self) -> None:
        kept, why = read(item("stealth", "person", "spoken_of", [16, 20], "name"))
        self.assertEqual((kept, why), ([], {"stealth": nr.RULES_WORD}))

    def test_unsure_needs_two_lines_even_when_introduced(self) -> None:
        kept, _ = read(item("insight", "item", "introduced", [1], "unsure"))
        self.assertEqual(kept, [])
        kept, _ = read(item("insight", "item", "introduced", [1, 2], "unsure"))
        self.assertEqual([(f.name, f.sense) for f in kept], [("insight", nr.UNSURE)])

    def test_a_roll_after_a_word_is_never_a_hearing_of_it(self) -> None:
        self.assertFalse(nr.holds_as_name("make a stealth check to sneak past", "stealth"))
        self.assertFalse(nr.holds_as_name("two stealth checks", "stealth"))
        self.assertTrue(nr.holds_as_name("stealth the cat sleeps", "stealth"))
        self.assertTrue(nr.holds_as_name("we sail on the insight", "insight"))

    def test_a_roll_phrase_needs_its_whole_words(self) -> None:
        # (only check, roll, save and saving throw, plurals too: "saving the crew" is no roll)
        self.assertTrue(nr.holds_as_name("the insight saving the crew", "insight"))
        self.assertFalse(nr.holds_as_name("an insight saving throw", "insight"))
        self.assertFalse(nr.holds_as_name("two insight saving throws", "insight"))

    def test_unsure_perception_cited_only_on_its_roll_lines_is_refused(self) -> None:
        kept, why = read(item("perception", "idea", "spoken_of", [6, 12, 14], "unsure"))
        self.assertEqual((kept, why), ([], {"perception": nr.RULES_WORD}))

    def test_the_instructions_ask_for_the_sense(self) -> None:
        self.assertEqual(nr.PROMPT_VERSION, "names-3")
        self.assertIn('"sense": "name"', nr.SYSTEM)
        self.assertIn('"sense": "unsure"', nr.SYSTEM)


class TheCap(unittest.TestCase):
    def test_at_most_two_unsure_after_every_other_suggestion_the_rest_traced(self) -> None:
        spellings = [
            ng.Spelling(w, 3, why="spoken_of", unsure=True) for w in ("Insight", "Arcana", "Nature")
        ] + [ng.Spelling("Hrothgar", 2, why="spoken_of")]
        out = ng.settle(spellings)
        self.assertEqual([g.name for g in out.offered], ["Hrothgar", "Arcana", "Insight"])
        self.assertIn(("Nature", ng.UNSURE_OVER, 3), out.refused)

    def test_an_unsure_one_needs_two_hearings_whatever_its_reason(self) -> None:
        out = ng.settle([ng.Spelling("Insight", 1, why="introduced", unsure=True)])
        self.assertEqual((out.offered, out.refused[0][1]), ([], ng.TOO_FEW))

    def test_unsure_is_the_readers_word_alone(self) -> None:
        # (the step hands on no rules word the scan found; were one there, its capitals would be
        # no evidence of a name, so the group stays unsure)
        out = ng.settle(
            [ng.Spelling("Insight", 2, why="spoken_of", unsure=True), ng.Spelling("Insight", 2)]
        )
        self.assertEqual([g.unsure for g in out.offered], [True])

    def test_an_unsure_one_is_never_sure_even_when_introduced(self) -> None:
        out = ng.settle(
            [ng.Spelling("Insight", 2, why="introduced", unsure=True),
             ng.Spelling("Vorna", 2, why="spoken_of")]
        )  # fmt: skip
        self.assertEqual([(g.name, g.sure) for g in out.offered], [("Vorna", False),
                                                                   ("Insight", False)])  # fmt: skip


class TheCard(unittest.TestCase):
    def test_the_card_says_why_it_asks(self) -> None:
        from dmbot.memory.models import PROPOSED, Entity
        from dmbot.ui.names import RULES_WORD_LINE, suggestion_text

        entity = Entity("w" * 32, "item", "Insight", "", PROPOSED, None, "scan", 0)
        self.assertEqual(
            RULES_WORD_LINE,
            "This is also a rules word. DMbot is asking because the session used it like a name.",
        )
        self.assertIn(RULES_WORD_LINE, suggestion_text(entity, 1, rules_word=True))
        self.assertNotIn(RULES_WORD_LINE, suggestion_text(entity, 1))

    def test_the_narrow_set(self) -> None:
        for name, kind in (("Insight", "item"), ("Animal Handling", "concept"), ("Arcana", "npc")):
            with self.subTest(name=name):
                self.assertTrue(nr.rules_word(name, kind, None))
        # (the card passes `entity.type`, the lore kind word: a waiting character named for a
        # kind of monster gets the line; the Expert's check)
        self.assertTrue(nr.rules_word("Owlbear", "npc", RULES))
        self.assertTrue(nr.rules_word_any("Owlbear", RULES))
        self.assertFalse(nr.rules_word("Owlbear", "place", RULES))
        for name in ("Captain Stealth", "Hrothgar", "Dex"):
            with self.subTest(name=name):
                self.assertFalse(nr.rules_word(name, "npc", None))


class TheStep(unittest.TestCase):
    """The session step itself (switch on), on `test_scan_loose`'s stand-in bot."""

    def run_step(self, bot: Any, texts: list[str]) -> list[str]:
        from tests.test_scan_loose import TheBotStep

        return TheBotStep.run_step(cast(Any, TheBotStep()), bot, texts)

    def bot(self, folder: str, found: list[nr.Found], texts: list[str]) -> Any:
        from types import SimpleNamespace

        from tests.test_scan_loose import stand_in

        bot = stand_in(folder, {}, texts=texts)

        async def read(*_a: Any, **_k: Any) -> Any:
            return found, [], 0

        async def campaign(*_a: Any) -> Any:
            return SimpleNamespace(
                name="sample", target_ruleset=RULES[0], fallback_ruleset=RULES[1]
            )

        bot._read_session_names = read
        bot.campaigns = SimpleNamespace(get=campaign)
        return bot

    def test_an_owlbear_the_scan_found_by_its_capitals_is_never_a_plain_new_name(self) -> None:
        import tempfile

        from dmbot.memory.scan import find_new_names

        texts = ["The Owlbear attacks Vorna.", "Then the Owlbear roars at Vorna."]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_step(self.bot(d, [], texts), texts), ["Vorna"])
        # (without the campaign's rulesets the scan alone would offer it: the guard is real)
        found = [s.name for s in find_new_names(texts, min_times=2)]
        self.assertIn("Owlbear", found)

    def test_two_unsure_offered_after_the_others_the_third_traced(self) -> None:
        import tempfile

        texts = ["we sail on", "and on"]
        found = [nr.Found(n, "item", "spoken_of", 2, ("1", "2"), nr.UNSURE)
                 for n in ("insight", "arcana", "nature")]  # fmt: skip
        found.append(nr.Found("hrothgar", "person", "spoken_of", 2, ("1", "2")))
        with tempfile.TemporaryDirectory() as d:
            made = self.run_step(self.bot(d, found, texts), texts)
        # (the sure name first, then two unsure ones; the third is dropped: `TheCap` traces it)
        self.assertEqual(made[0], "Hrothgar")
        self.assertEqual(len(made), 3)
