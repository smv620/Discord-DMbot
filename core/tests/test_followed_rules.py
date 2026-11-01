"""House rules from a followed shared set (#1190, slice 3): they reach the advisors after the
campaign's own rules, and every use cites the set. Fake stores: no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.rules import followed, rule_card
from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.rules.rule_sets import Follow
from dmbot.sidebar import context


def own(number: int, text: str) -> HouseRule:
    return HouseRule(number, "c1", text, None, None, None, 7, 1, 1)


def follow(updating: bool = True) -> Follow:
    content = {
        "rules": [
            {"number": 2, "rule": "Fireball hits friends too", "instead": None},
            {"number": 1, "rule": "Flanking gives advantage", "instead": "Flanking is optional"},
            {"bad": "row"},
        ],
        "link": None,
    }
    return Follow("s", "house_rules", "Red Table", "Red", 4, content, updating, 5)


class Reading(unittest.IsolatedAsyncioTestCase):
    def test_rules_carry_their_set_and_come_oldest_first(self) -> None:
        rules = followed.from_follow(follow(), "c1")
        self.assertEqual([r.number for r in rules], [1, 2])  # the bad row is skipped
        self.assertEqual(rules[0].cite, "Red Table house rules, v4")
        self.assertEqual(rules[0].supersedes, "Flanking is optional")

    async def test_nothing_followed_or_unreadable_gives_none(self) -> None:
        self.assertEqual(await followed.followed_rules(None, 1, "c1"), [])
        sets = SimpleNamespace(following=AsyncMock(side_effect=RuntimeError("down")))
        self.assertEqual(await followed.followed_rules(sets, 1, "c1"), [])  # type: ignore[arg-type]

    async def test_the_bot_reads_own_rules_first_then_the_followed_set(self) -> None:
        fake = SimpleNamespace(
            house_rules=SimpleNamespace(list=AsyncMock(return_value=[own(9, "Own rule")])),
            rule_sets=SimpleNamespace(following=AsyncMock(return_value=[follow()])),
        )
        rules = await DMBot.all_house_rules(fake, 1, "c1")  # type: ignore[arg-type]
        self.assertEqual(rules[0].rule, "Own rule")
        self.assertEqual(len(rules), 3)
        fake.rule_sets.following.assert_awaited_once_with(1, "c1")

    async def test_following_changes_what_the_sidebar_is_told(self) -> None:
        fake = SimpleNamespace(
            house_rules=SimpleNamespace(list=AsyncMock(return_value=[])),
            rule_sets=SimpleNamespace(following=AsyncMock(return_value=[follow()])),
        )
        question = "Does flanking give advantage here?"
        kw = dict(target="2024", fallback="2014", index=srd(), lookup=None, scene="")
        before = context.build(question, house_rules=[], **kw)  # type: ignore[arg-type]
        rules = await DMBot.all_house_rules(fake, 1, "c1")  # type: ignore[arg-type]
        after = context.build(question, house_rules=rules, **kw)  # type: ignore[arg-type]
        self.assertNotIn("Shared rule", before.prompt)
        self.assertIn(
            "Shared rule 1 (Red Table house rules, v4): Flanking gives advantage", after.prompt
        )
        self.assertIn("Red Table house rules, v4", after.sources)

    def test_a_card_cites_the_set_and_lists_own_rules_first(self) -> None:
        rules = [*followed.from_follow(follow(), "c1"), own(30, "Flanking needs two allies")]
        matches = rule_card.house_matches(rules, ["flanking"])
        self.assertEqual(matches[0].number, 30)  # the campaign's own rule first
        text = "\n".join(rule_card.house_lines(matches))
        self.assertIn("Red Table house rules, v4, rule 1", text)

    def test_a_merged_rule_that_contradicts_an_own_rule_comes_second_and_says_so(self) -> None:
        merged = HouseRule(
            1, "c1", "Flanking gives no advantage", None, None, None, 7, 1, 1,
            from_set="Red Table house rules, v4",
        )  # fmt: skip
        mine = own(30, "Flanking gives advantage")
        # The merged rule has the lower number, so only the ranking puts the own rule first.
        matches = rule_card.house_matches([merged, mine], ["flanking"])
        self.assertEqual([r.number for r in matches], [30, 1])
        lines = rule_card.house_lines(matches)
        self.assertIn("House rule 30:", lines[0])
        self.assertIn(
            "House rule 1 (from Red Table house rules, v4; the campaign's own win):", lines[1]
        )

    def test_the_sidebar_lists_the_own_rule_first_and_labels_the_merged_one(self) -> None:
        merged = HouseRule(
            1, "c1", "Flanking gives no advantage", None, None, None, 7, 1, 1,
            from_set="Red Table house rules, v4",
        )  # fmt: skip
        mine = own(30, "Flanking gives advantage")
        built = context.build(
            "Does flanking give advantage?", house_rules=[merged, mine], target="2024",
            fallback="2014", index=srd(), lookup=None, scene="",
        )  # fmt: skip
        prompt = built.prompt
        own_at = prompt.index("House rule 30:")
        merged_at = prompt.index(
            "House rule 1 (from Red Table house rules, v4; the campaign's own win)"
        )
        self.assertLess(own_at, merged_at)


if __name__ == "__main__":
    unittest.main()
