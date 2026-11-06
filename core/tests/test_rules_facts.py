"""An entry's metadata is part of the entry (#1063): one place decides which fields exist, and
every reader (the sidebar's prompt and citation check, its fallback answer, the rule card) goes
through it. The old rule-card line is pinned word for word."""

import re
import unittest
from pathlib import Path
from typing import ClassVar

from dmbot.rules import facts
from dmbot.rules.index import Entry, srd
from dmbot.sidebar import answer as sidebar
from dmbot.ui import rule_card
from tests.sidebar_brevity_cases import reply
from tests.test_sidebar_answer import FakeAI, campaign, engine, run

INDEX = srd()


def entry(name: str, kind: str, edition: str = "2024") -> Entry:
    hit = INDEX.lookup(name, edition, "none", kind=kind)
    assert hit is not None, name
    return hit.entry


class OneFactsFunction(unittest.TestCase):
    # What the rule card showed before there was one function: it must not change.
    PINNED_CARDS: ClassVar[dict[tuple[str, str, str], str]] = {
        ("fireball", "spell", "2024"): (
            "3rd-level Evocation · Casting time: Action · Range: 150 feet · "
            "Components: V, S, M (a ball of bat guano and sulfur) · Duration: Instantaneous"
        ),
        ("prestidigitation", "spell", "2024"): (
            "Transmutation cantrip · Casting time: Action · Range: 10 feet · "
            "Components: V, S · Duration: Up to 1 hour"
        ),
        ("fireball", "spell", "2014"): (
            "3rd-level Evocation · Casting time: 1 action · Range: 150 feet · "
            "Components: V, S, M (a tiny ball of bat guano and sulfur) · "
            "Duration: Instantaneous"
        ),
        ("goblin warrior", "monster", "2024"): (
            "Small Fey (Goblinoid) · AC 15 · HP 10 (3d6) · Speed 30 ft. · CR 1/4"
        ),
        ("goblin", "monster", "2014"): (
            "Small humanoid (goblinoid) · AC 15 · HP 7 (2d6) · Speed 30 ft. · CR 1/4"
        ),
        ("grappled", "condition", "2024"): "",
    }

    def test_the_rule_card_line_is_unchanged(self) -> None:
        for (name, kind, edition), pinned in self.PINNED_CARDS.items():
            with self.subTest(name, edition=edition):
                self.assertEqual(rule_card.facts(entry(name, kind, edition)), pinned)

    def test_the_ai_line_is_plain_and_in_words(self) -> None:
        self.assertEqual(
            entry("fireball", "spell").facts,
            "Level 3 Evocation; Casting time Action; Range 150 feet; "
            "Components Verbal, Somatic, Material (a ball of bat guano and sulfur); "
            "Duration Instantaneous",
        )
        self.assertIn("Senses Darkvision 60 ft.", entry("goblin warrior", "monster").facts)
        self.assertEqual(entry("grappled", "condition").facts, "")

    def test_full_text_is_the_facts_then_the_text(self) -> None:
        e = entry("fireball", "spell")
        self.assertTrue(e.full_text.startswith(e.facts + ". "))
        self.assertTrue(e.full_text.endswith(e.text))
        cond = entry("grappled", "condition")
        self.assertEqual(cond.full_text, cond.text)  # no header, nothing added

    def test_a_field_named_in_a_question_is_found(self) -> None:
        e = entry("fireball", "spell")
        for question, label in (
            ("what's the range of fireball", "Range 150 feet"),
            ("what is the casting time of fireball", "Casting time Action"),
            ("how long does fireball last", "Duration Instantaneous"),
            ("components of fireball?", "Components Verbal"),
        ):
            with self.subTest(question):
                got = facts.asked_field(e, question)
                assert got is not None
                self.assertTrue(got.startswith(label), got)
        self.assertIsNone(facts.asked_field(e, "does fireball set things on fire"))
        # Wrong fields would be stated as fact with an SRD page (reviewer, #1063).
        for question in (
            "what was the last thing fireball hit",
            "can fireball reach the ceiling",
            "what is the range, duration of fireball",  # two fields: the description instead
        ):
            with self.subTest(question):
                self.assertIsNone(facts.asked_field(e, question))
        self.assertEqual(
            facts.asked_field(e, "how long does fireball take to cast"), "Casting time Action"
        )
        self.assertEqual(facts.asked_field(e, "what's the range, fireball?"), "Range 150 feet")
        gob = entry("goblin warrior", "monster")
        self.assertEqual(facts.asked_field(gob, "what's its AC"), "AC 15")
        self.assertIsNone(facts.asked_field(gob, "can the goblin cr"))
        self.assertIsNone(facts.asked_field(entry("grappled", "condition"), "what is the range"))


class TheFallbackAnswersWithTheField(unittest.TestCase):
    def test_a_range_question_that_falls_back_answers_with_the_range(self) -> None:
        # The model says "don't say" twice with the entry in front of it; the entry answers.
        nope = reply("The free rules don't say. Your call.", "SRD 5.2.1 p. 131", "uncertain")
        got = run(engine(FakeAI([nope, nope])).answer(campaign(), "what's the range of fireball"))
        self.assertIn("150 feet", got.text)
        self.assertIn("SRD 5.2.1 p. 131", got.text)

    def test_other_questions_still_fall_back_to_the_description(self) -> None:
        nope = reply("The free rules don't say. Your call.", "SRD 5.2.1 p. 131", "uncertain")
        got = run(
            engine(FakeAI([nope, nope])).answer(
                campaign(), "do you need line of sight for fireball"
            )
        )
        self.assertIn("point you choose", got.text)
        hit = INDEX.lookup("fireball", "2024", "2014")
        assert hit is not None
        self.assertEqual(sidebar.entry_fact(hit).count("150"), 0)  # the description, not a field


class NoReaderSkipsTheMetadata(unittest.TestCase):
    """A new reader of a rules entry's `.text` that forgets the facts is how #1039 happened. Each
    file that reads it is listed with how many times and why, so a new read anywhere fails here
    until someone decides, on purpose, that it needs no facts."""

    # file -> (reads of `.text` on a rules entry, why the text alone is right there)
    READS: ClassVar[dict[str, tuple[int, str]]] = {
        "ui/rule_card.py": (1, "shows the facts on their own line above the text"),
        "sidebar/context.py": (1, "sends the facts in front of the text as FACTS:"),
        "sidebar/answer.py": (1, "the fallback's first sentences; a field question uses facts"),
        "recaps/draft.py": (1, "takes lower-case words for a vocabulary (#2013); shows nothing"),
    }
    # Where the entry is made and its words are indexed or matched.
    # (and the campaign's own homebrew, whose entries have a `.text` but are not rules entries)
    OWN: ClassVar[set[str]] = {
        "rules/index.py",
        "rules/facts.py",
        "rules/homebrew.py",
        "rules/homebrew_file.py",
        "rules/homebrew_packs.py",
        "rules/pack_content.py",
        "ui/homebrew.py",
        "rules/pack_suggestions.py",  # a suggested homebrew entry, not a rules entry
        "dm_screen/pack_suggestion.py",
        "ui/rule_lookup.py",  # Make it my own copies a homebrew entry's text (#1541)
    }

    def test_only_the_known_readers_use_entry_text(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
        # Any `.entry.text`, and `entry.text` / `e.text` on a name bound to a rules entry.
        pattern = re.compile(r"\.entry\.text\b|\bentry\.text\b|\be\.text\b")
        counts: dict[str, int] = {}
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel in self.OWN:
                continue
            source = path.read_text(encoding="utf-8")
            # Memory entries also have a `.text`; only files that use the rules index count.
            if "dmbot.rules" not in source and not rel.startswith("rules/"):
                continue
            if found := len(pattern.findall(source)):
                counts[rel] = found
        self.assertEqual(counts, {f: n for f, (n, _) in self.READS.items()}, "add the facts")


if __name__ == "__main__":
    unittest.main()
