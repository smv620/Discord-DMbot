"""The campaign's own homebrew in the sidebar and in table questions (#1520): an entry comes after
house rules and before the free rules, with its tag and its source, and shadows the book for the
same name; nothing is hidden from players any more ("Homebrew (part 2)"), so a table question is
answered from it too; nothing from another campaign. (An entry the DM turned off never gets here:
the reader leaves it out, see test_homebrew_enabled.)"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from collections.abc import Sequence
from typing import Any

from dmbot.campaigns import Campaign
from dmbot.rules.homebrew import Homebrew
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import context
from dmbot.sidebar.answer import Sidebar
from tests.sidebar_brevity_cases import reply
from tests.test_sidebar_answer import FakeAI, campaign, house

NOW = 1_700_000_000
INDEX = srd()


def brew(name: str, text: str, *, kind: str = "monster", cid: str = "c" * 32) -> Homebrew:
    return Homebrew(1, cid, kind, name, text, None, 7, NOW, NOW)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def engine(ai: Any, entries: Sequence[Homebrew], houses: Sequence[HouseRule] = ()) -> Sidebar:
    async def gate(c: Campaign, user: int) -> str | None:
        return None

    async def houses_of(c: Campaign) -> Sequence[HouseRule]:
        return list(houses)

    async def names(c: Campaign) -> None:
        return None

    async def brews(c: Campaign) -> Sequence[Homebrew]:
        return list(entries)

    return Sidebar(ai, srd(), gate=gate, houses=houses_of, names=names, brews=brews)


def build(question: str, entries: Sequence[Homebrew], **kw: Any) -> context.Context:
    return context.build(
        question,
        target="2024",
        fallback="2014",
        index=INDEX,
        house_rules=kw.pop("house_rules", []),
        lookup=None,
        scene="",
        homebrew=entries,
        campaign_name="Frostmaiden",
        **kw,
    )


class TheContext(unittest.TestCase):
    def test_a_known_entry_is_sent_with_its_tag_and_source_after_house_rules_before_the_book(
        self,
    ) -> None:
        entry = brew("Moonblade", "A silver sword. It sings near undead.", kind="item")
        built = build(
            "how does fireball work with the Moonblade",
            [entry],
            house_rules=[house(1, "Fireball is a bonus action")],
        )
        text = built.prompt
        self.assertLess(text.index("HOUSE RULES"), text.index("HOMEBREW"))
        self.assertLess(text.index("HOMEBREW"), text.index("RULES ENTRIES"))
        self.assertIn("[Homebrew] Moonblade (item)", text)
        self.assertIn("Frostmaiden's homebrew: Moonblade [Homebrew]", built.sources)
        self.assertIn("It sings near undead.", text)

    def test_a_former_hidden_entry_is_in_the_prompt_like_any_other(self) -> None:
        entry = brew("Hollow King", "Its crown is cursed.")
        built = build("how does the Hollow King fight", [entry])
        self.assertIn("cursed", built.prompt)
        self.assertIn("HOMEBREW", built.prompt)
        self.assertEqual(built.homebrew, (entry,))

    def test_a_homebrew_name_shadows_the_book(self) -> None:
        built = build("what is a goblin warrior's AC", [brew("Goblin Warrior", "Mine.")])
        self.assertEqual(built.hits, ())
        self.assertNotIn("RULES ENTRIES", built.prompt)

    def test_a_shared_books_entry_ranks_in_its_editions_place(self) -> None:
        """(#1569) Against the free rules' Fireball (2024 is the target): a 2024 book answers
        first; a 2014 book and an add-on step aside; the free rules answer then."""
        for edition, answers in (("2024", True), ("2014", False), ("other", False), (None, False)):
            with self.subTest(edition):
                entry = dataclasses.replace(
                    brew("Fireball", "The book's version.", kind="spell"),
                    from_book=True,
                    book_edition=edition,
                )
                built = build("how does fireball work", [entry])
                self.assertEqual("HOMEBREW" in built.prompt, answers)
                self.assertEqual("[Homebrew] Fireball" in built.prompt, answers)
                if not answers:
                    self.assertIn("RULES ENTRIES", built.prompt)

    def test_a_2014_book_answers_where_only_the_older_rules_have_the_name(self) -> None:
        entry = dataclasses.replace(
            brew("Orc", "The book's orc."),
            from_book=True,
            book_edition="2014",
        )
        built = build("what is an orc", [entry])
        self.assertIn("[Homebrew] [Legacy 2014] Orc", built.prompt)

    def test_a_book_entry_that_ranks_behind_does_not_shadow_the_free_rules(self) -> None:
        entry = dataclasses.replace(
            brew("Fireball", "Secret.", kind="spell"),
            from_book=True,
            book_edition="2014",
        )
        built = build("how does fireball work", [entry])
        self.assertEqual(built.homebrew, ())
        self.assertIn("RULES ENTRIES", built.prompt)

    def test_ordinary_homebrew_still_beats_the_free_rules_whatever(self) -> None:
        built = build("how does fireball work", [brew("Fireball", "Mine.", kind="spell")])
        self.assertIn("[Homebrew] Fireball", built.prompt)

    def test_no_entry_means_no_change(self) -> None:
        plain = build("range of fireball", [])
        self.assertNotIn("HOMEBREW", plain.prompt)
        self.assertEqual(plain.homebrew, ())
        self.assertTrue(plain.hits)

    def test_at_most_three_each_cut_to_600_characters(self) -> None:
        entries = [brew(f"Orb {n}", "x" * 900, kind="item") for n in range(5)]
        built = build("tell me about orb 0, orb 1, orb 2, orb 3 and orb 4", entries)
        self.assertEqual(len(built.homebrew), 3)
        self.assertLessEqual(max(len(line) for line in built.prompt.splitlines()), 700)


class TheEngine(unittest.TestCase):
    def test_a_known_entry_answers_with_its_source_and_the_rule_is_added(self) -> None:
        entry = brew("Moonblade", "A silver sword. It sings near undead.", kind="item")
        ai = FakeAI(
            [reply("It sings near undead.", "Frostmaiden's homebrew: Moonblade", "confident")]
        )
        got = run(engine(ai, [entry]).answer(campaign(), "what does the Moonblade do", asker_id=7))
        self.assertIn("13. Under HOMEBREW", ai.systems[0])
        self.assertIn("Frostmaiden's homebrew: Moonblade [Homebrew]", got.text)
        self.assertIn("sings near undead", got.text)

    def test_with_no_homebrew_the_system_text_is_today_s(self) -> None:
        ai = FakeAI([reply("Yes.", "none")])
        run(engine(ai, []).answer(campaign(), "does grappling need a free hand", asker_id=7))
        self.assertNotIn("HOMEBREW", ai.systems[0])

    def test_a_table_question_about_an_entry_is_answered_from_it(self) -> None:
        entry = brew("Moonblade", "A silver sword.", kind="item")
        ai = FakeAI([reply("A silver sword.", "Frostmaiden's homebrew: Moonblade")])
        got = run(
            engine(ai, [entry]).answer(campaign(), "what is the moonblade", asker_id=1, table=True)
        )
        self.assertTrue(ai.prompts[0].startswith("AUDIENCE: THE TABLE"))
        self.assertIn("[Homebrew] Moonblade", ai.prompts[0])
        self.assertIn("silver sword", got.text)

    def test_a_homebrew_store_that_fails_costs_only_the_homebrew(self) -> None:
        async def gate(c: Campaign, u: int) -> str | None:
            return None

        async def none(c: Campaign) -> Sequence[Any]:
            return []

        async def no_names(c: Campaign) -> None:
            return None

        async def broken(c: Campaign) -> Sequence[Homebrew]:
            raise RuntimeError("down")

        ai = FakeAI([reply("Yes.", "none")])
        built = Sidebar(ai, srd(), gate=gate, houses=none, names=no_names, brews=broken)
        got = run(built.answer(campaign(), "does grappling need a free hand", asker_id=7))
        self.assertIn("Yes", got.text)
