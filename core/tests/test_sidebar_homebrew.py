"""The campaign's own homebrew in the sidebar and in table questions (#1520): an entry players may
know comes after house rules and before the free rules, with its tag and its source, and shadows
the book for the same name; an entry only the DM may know is never sent, never used for a table
answer, and still shadows the book (the answer points the DM to /dmbot rule, or tells the table
"That's one for your DM."); nothing from another campaign."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence
from typing import Any

from dmbot.campaigns import Campaign
from dmbot.rules.homebrew import Homebrew
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import context
from dmbot.sidebar.answer import ONE_FOR_DM, Sidebar
from tests.sidebar_brevity_cases import reply
from tests.test_sidebar_answer import FakeAI, campaign, house

NOW = 1_700_000_000
INDEX = srd()


def brew(
    name: str, text: str, *, known: bool, kind: str = "monster", cid: str = "c" * 32
) -> Homebrew:
    return Homebrew(1, cid, kind, name, text, None, known, 7, NOW, NOW)


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
        entry = brew("Moonblade", "A silver sword. It sings near undead.", known=True, kind="item")
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

    def test_a_hidden_entry_is_never_in_the_prompt_but_its_name_is_noted(self) -> None:
        hidden = brew("Hollow King", "Secret: its crown is cursed.", known=False)
        built = build("how does the Hollow King fight", [hidden])
        self.assertNotIn("cursed", built.prompt)
        self.assertNotIn("HOMEBREW", built.prompt)
        self.assertEqual(built.hidden_homebrew, ("Hollow King",))
        self.assertEqual(built.homebrew, ())

    def test_a_homebrew_name_shadows_the_book_hidden_or_not(self) -> None:
        for known in (True, False):
            built = build(
                "what is a goblin warrior's AC", [brew("Goblin Warrior", "Mine.", known=known)]
            )
            self.assertEqual(built.hits, ())
            self.assertNotIn("RULES ENTRIES", built.prompt)

    def test_no_entry_means_no_change(self) -> None:
        plain = build("range of fireball", [])
        self.assertNotIn("HOMEBREW", plain.prompt)
        self.assertEqual(plain.hidden_homebrew, ())
        self.assertTrue(plain.hits)

    def test_at_most_three_each_cut_to_600_characters(self) -> None:
        entries = [brew(f"Orb {n}", "x" * 900, known=True, kind="item") for n in range(5)]
        built = build("tell me about orb 0, orb 1, orb 2, orb 3 and orb 4", entries)
        self.assertEqual(len(built.homebrew), 3)
        self.assertLessEqual(max(len(line) for line in built.prompt.splitlines()), 700)


class TheEngine(unittest.TestCase):
    def test_a_known_entry_answers_with_its_source_and_the_rule_is_added(self) -> None:
        entry = brew("Moonblade", "A silver sword. It sings near undead.", known=True, kind="item")
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

    def test_a_table_question_about_a_hidden_entry_is_one_for_the_dm_with_no_ai_call(self) -> None:
        hidden = brew("Hollow King", "Secret.", known=False)
        ai = FakeAI([])  # any call would raise
        got = run(
            engine(ai, [hidden]).answer(
                campaign(), "how does the hollow king fight", asker_id=1, table=True
            )
        )
        self.assertEqual((got.text, got.one_for_dm), (ONE_FOR_DM, True))
        self.assertNotIn("Secret", got.text)

    def test_a_dms_sidebar_question_about_a_hidden_entry_points_to_the_rule_lookup(self) -> None:
        hidden = brew("Hollow King", "Secret.", known=False)
        ai = FakeAI([])
        got = run(
            engine(ai, [hidden]).answer(campaign(), "how does the Hollow King fight", asker_id=7)
        )
        self.assertEqual(got.text, "That's in your homebrew. Use /dmbot rule Hollow King.")
        self.assertNotIn("Secret", got.text)
        self.assertFalse(got.one_for_dm)

    def test_a_table_question_about_a_known_entry_is_answered_from_it(self) -> None:
        entry = brew("Moonblade", "A silver sword.", known=True, kind="item")
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
