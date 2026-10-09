"""What the sidebar tells the AI (#934): the rules and names the question mentions, only this
campaign's house rules, and nothing secret. The database test uses the real house-rule store
for two campaigns to prove the second one's rule never reaches the first one's prompt."""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from dmbot.campaigns.store import CampaignStore
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import Relation
from dmbot.rules.house import HouseRuleStore
from dmbot.rules.index import srd
from dmbot.sidebar import context
from dmbot.sidebar.answer import Sidebar
from tests.pg import DatabaseTest
from tests.sidebar_brevity_cases import reply
from tests.test_memory_lookup import BELLEROS, CERRIC, data
from tests.test_sidebar_answer import FakeAI, house

INDEX = srd()


class MentionedRules(unittest.TestCase):
    def names(self, question: str, target: str = "2024", fallback: str = "2014") -> list[str]:
        return [h.entry.name for h in context.mentioned_rules(question, INDEX, target, fallback)]

    def test_a_spell_named_in_the_question(self) -> None:
        self.assertEqual(self.names("do you need line of sight for fireball"), ["Fireball"])

    def test_several_in_the_order_asked_and_never_more_than_three(self) -> None:
        found = self.names("can a goblin that is grappled and prone and stunned cast fireball")
        self.assertLessEqual(len(found), context.ENTRIES_MAX)
        self.assertEqual(found[0], "Goblin Warrior")

    def test_common_words_alone_are_not_looked_up(self) -> None:
        self.assertEqual(self.names("do you need to find it"), [])

    def test_an_older_name_leads_to_the_newer_entry(self) -> None:
        self.assertEqual(self.names("what is a goblin AC"), ["Goblin Warrior"])

    def test_the_fallback_is_used_and_tagged_when_the_target_has_nothing(self) -> None:
        hits = context.mentioned_rules("what is a goblin AC", INDEX, "2014", "none")
        self.assertEqual(hits[0].entry.edition, "2014")
        prompt = context.build(
            "what is a goblin AC",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("Goblin", prompt)


class MentionedNames(unittest.TestCase):
    def test_a_confirmed_name_with_what_dmbot_knows(self) -> None:
        lookup = CampaignLookup.build(data())
        lines = context.mentioned_names("what do I know about Belleros", lookup)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Belleros (npc)"))

    def test_secret_and_unconfirmed_names_are_left_out(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertEqual(context.mentioned_names("who is the hooded stranger", lookup), [])
        self.assertEqual(context.mentioned_names("tell me about Kael", lookup), [])

    def test_confirmed_links_are_named(self) -> None:
        link = Relation(
            "r1", BELLEROS, "serves", CERRIC, "", 1.0, "confirmed", "dm", (), None, None,
            None, None, False, 0,
        )  # fmt: skip
        lookup = CampaignLookup.build(data(relations=(link,)))
        (line,) = context.mentioned_names("what about Belleros", lookup)
        self.assertIn("Linked to: Cerric", line)

    def test_no_lookup_no_names(self) -> None:
        self.assertEqual(context.mentioned_names("what about Belleros", None), [])

    def test_the_names_reach_the_prompt_and_are_a_source(self) -> None:
        lookup = CampaignLookup.build(data())
        built = context.build(
            "what about Belleros",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=lookup,
            scene="",
        )
        self.assertIn("CAMPAIGN NAMES", built.prompt)
        self.assertIn("campaign names", built.sources)
        self.assertNotIn("hooded stranger", built.prompt)


class HouseRulesChosen(unittest.TestCase):
    def test_one_naming_the_entry_comes_before_one_sharing_a_word(self) -> None:
        hits = context.mentioned_rules("how does fireball spread", INDEX, "2024", "2014")
        rules = [
            house(1, "Falling damage is doubled."),
            house(2, "Spread out when casting anything big."),
            house(3, "Fireball ignores cover."),
        ]
        chosen = context.relevant_house_rules(rules, "how does fireball spread", hits)
        self.assertEqual([r.number for r in chosen], [3, 2])

    def test_at_most_five(self) -> None:
        rules = [house(n, f"Fireball rule number {n}.") for n in range(1, 12)]
        hits = context.mentioned_rules("fireball", INDEX, "2024", "2014")
        self.assertEqual(len(context.relevant_house_rules(rules, "fireball", hits)), 5)


class TheScene(unittest.TestCase):
    def test_only_the_tail_is_sent(self) -> None:
        built = context.build(
            "does it matter",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="a" * 5000 + " the end",
        )
        self.assertIn("the end", built.prompt)
        self.assertLess(len(built.prompt), 2000)


class RealStoreIsolation(DatabaseTest):
    async def test_a_second_campaigns_house_rule_never_reaches_the_first_prompt(self) -> None:
        campaigns = CampaignStore(self.db)
        mine = await campaigns.create(111, "Mine", 7)
        other = await campaigns.create(111, "Other", 7)
        rules = HouseRuleStore(self.db)
        await rules.add(111, mine.id, 7, "Fireball needs a clear line of sight.")
        await rules.add(111, other.id, 7, "Fireball hits twice, OTHER-CAMPAIGN-ONLY.")

        async def gate(c: Any, user: int) -> None:
            return None

        async def houses(c: Any) -> Any:
            return await rules.list(c.guild_id, c.id)

        async def names(c: Any) -> None:
            return None

        ai = FakeAI([reply("Yes. House rule 1.", "House rule 1")])
        built = Sidebar(ai, INDEX, gate=gate, houses=houses, names=names)
        await built.answer(replace(mine), "do you need line of sight for fireball")
        self.assertIn("clear line of sight", ai.prompts[0])
        self.assertNotIn("OTHER-CAMPAIGN-ONLY", ai.prompts[0])


if __name__ == "__main__":
    unittest.main()
