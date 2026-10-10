"""Bringing the file and DMbot's copy together (#969): the items, the words, and doing the
accepted ones with the store's own checks. No Discord, no database."""

from __future__ import annotations

import unittest

from dmbot.rules import house
from dmbot.rules import house_file as hf
from dmbot.rules import house_file_sync as sync
from dmbot.rules.house import HouseRule, HouseRuleError

DM, GUILD, CID = 7, 1, "c1"


def rule(number: int, text: str, instead: str | None = None, version: int = 1) -> HouseRule:
    return HouseRule(number, CID, text, instead, None, None, DM, 1, 1, version)


class FakeStore:
    """Like `HouseRuleStore`: numbers are never reused, a wanted number is given only if
    it was never used, a change made from an old view is refused, only DMs write."""

    def __init__(self, rules: list[HouseRule], made: int | None = None) -> None:
        self.rules = list(rules)
        self.made = made if made is not None else max((r.number for r in rules), default=0)
        self.scenarios: list[str | None] = []

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        return sorted(self.rules, key=lambda r: -r.number)

    def _check(self, user_id: int) -> None:
        if user_id != DM:
            raise HouseRuleError(house.NOT_DM)

    async def add(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        text: str,
        supersedes: str | None = None,
        *,
        scenario: str | None = None,
        session_id: str | None = None,
        wanted: int | None = None,
    ) -> HouseRule:
        self._check(user_id)
        if len(self.rules) >= house.HOUSE_RULES_MAX:
            raise HouseRuleError(house.FULL)
        window = self.made < (wanted or 0) <= self.made + house.HOUSE_RULES_MAX
        self.made = wanted if wanted is not None and window else self.made + 1
        self.scenarios.append(scenario)
        added = rule(self.made, text, supersedes)
        self.rules.append(added)
        return added

    def _find(self, number: int, since: int | None) -> int:
        for i, r in enumerate(self.rules):
            if r.number == number:
                if since is not None and r.version != since:
                    raise HouseRuleError(house.CHANGED)
                return i
        raise HouseRuleError(house.GONE)

    async def edit(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        number: int,
        text: str,
        instead: str | None = None,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        self._check(user_id)
        i = self._find(number, unchanged_since)
        self.rules[i] = rule(number, text, instead, self.rules[i].version + 1)
        return self.rules[i]

    async def remove(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        number: int,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        self._check(user_id)
        return self.rules.pop(self._find(number, unchanged_since))


def diff_of(mine: list[HouseRule], text: str) -> hf.Diff:
    return hf.compare(mine, hf.parse(text).rules)


class Items(unittest.TestCase):
    def test_new_then_changed_then_removed(self) -> None:
        mine = [rule(1, "Keep"), rule(2, "Old"), rule(3, "Gone")]
        items = sync.items_of(diff_of(mine, "1. Keep\n2. New\n9. Fresh (instead of: Book)\n"))
        self.assertEqual(
            [(i.kind, i.number) for i in items], [("add", 9), ("change", 2), ("remove", 3)]
        )
        self.assertEqual(items[0].instead, "Book")
        self.assertEqual((items[1].was, items[1].rule, items[1].version), ("Old", "New", 1))

    def test_a_rule_with_another_number_is_not_an_item(self) -> None:
        diff = diff_of([rule(4, "Same")], "11. Same\n")
        self.assertEqual(sync.items_of(diff), [])
        self.assertEqual(len(diff.moved), 1)

    def test_nothing_differs_means_nothing_to_do(self) -> None:
        self.assertEqual(sync.items_of(diff_of([rule(1, "A")], "1. A\n")), [])


class Words(unittest.TestCase):
    def test_the_summary_counts_each_kind(self) -> None:
        mine = [rule(1, "Keep"), rule(2, "Old"), rule(3, "Gone")]
        diff = diff_of(mine, "1. Keep\n2. New\n9. Fresh\n10. Another\n")
        self.assertEqual(
            sync.summary(diff),
            "**Your rules file and DMbot's house rules don't match:** "
            "2 to add, 1 to change, 1 to remove.",
        )

    def test_one_of_a_kind_and_the_extra_notes(self) -> None:
        diff = diff_of([rule(4, "Same"), rule(5, "Old")], "11. Same\n5. New\n")
        text = sync.summary(diff, unreadable=3)
        self.assertIn("1 to change", text)
        self.assertIn("1 rule has another number in the file; DMbot keeps its own numbers.", text)
        self.assertIn("3 lines in the file didn't look like rules and were skipped.", text)
        self.assertIn(
            "1 line in the file didn't look like a rule and was skipped.",
            sync.summary(diff, unreadable=1),
        )

    def test_each_item_says_what_accepting_does(self) -> None:
        mine = [rule(2, "Old", "Book"), rule(3, "Gone")]
        add, change, remove = sync.items_of(
            diff_of(mine, "2. New (instead of: Other)\n9. Fresh\n")
        )[0:3]
        self.assertIn("Add as a new rule: 9. Fresh", sync.describe(add))
        self.assertIn("DMbot has: Old (instead of: Book)", sync.describe(change))
        self.assertIn("The file says: New (instead of: Other)", sync.describe(change))
        self.assertIn("Accepting removes it from DMbot.", sync.describe(remove))

    def test_a_long_rule_is_cut_for_the_review(self) -> None:
        item = sync.Item("add", 1, "x" * 500)
        self.assertLess(len(sync.describe(item)), 400)

    def test_the_fingerprint_is_the_rules_not_the_title(self) -> None:
        a = hf.parse("Title one\n1. A\n2. B (instead of: C)\n").rules
        b = hf.parse("# a note\nOther title\n1.   A\n2. B (instead of: C)\n").rules
        c = hf.parse("1. A\n2. B\n").rules
        self.assertEqual(sync.fingerprint(a), sync.fingerprint(b))
        self.assertNotEqual(sync.fingerprint(a), sync.fingerprint(c))
        self.assertEqual(len(sync.fingerprint(a)), 64)
        self.assertEqual(len(sync.fingerprint(())), 64)


class Applying(unittest.IsolatedAsyncioTestCase):
    async def run_all(self, store: FakeStore, text: str, user: int = DM) -> sync.Applied:
        items = sync.items_of(diff_of(store.rules, text))
        return await sync.apply(store, GUILD, CID, user, items)

    async def test_all_three_kinds_are_done(self) -> None:
        store = FakeStore([rule(1, "Keep"), rule(2, "Old"), rule(3, "Gone")])
        applied = await self.run_all(store, "1. Keep\n2. New\n9. Fresh\n")
        self.assertEqual(sorted(r.number for r in store.rules), [1, 2, 9])
        self.assertEqual({r.number: r.rule for r in store.rules}[2], "New")
        self.assertEqual(applied.skipped, [])
        self.assertEqual(
            applied.done, ["Added house rule 9.", "Changed house rule 2.", "Removed house rule 3."]
        )
        self.assertEqual(store.scenarios, [sync.SCENARIO])

    async def test_a_new_rule_with_a_used_number_takes_the_next_free_one_and_says_so(self) -> None:
        store = FakeStore([rule(5, "Five")], made=8)  # 6, 7 and 8 were used before
        applied = await self.run_all(store, "5. Five\n7. Back from the dead\n")
        added = max(store.rules, key=lambda r: r.number)
        self.assertEqual((added.number, added.rule), (9, "Back from the dead"))
        self.assertIn("Added the file's rule 7 as house rule 9", applied.done[0])
        self.assertIn("a number is never used twice", applied.done[0])

    async def test_one_stray_big_number_does_not_use_up_the_numbers(self) -> None:
        store = FakeStore([rule(1, "One")])
        applied = await self.run_all(store, "1. One\n2147483646. Year-ish\n")
        added = max(store.rules, key=lambda r: r.number)
        self.assertEqual((added.number, store.made), (2, 2))
        self.assertIn("Added the file's rule 2147483646 as house rule 2", applied.done[0])

    async def test_a_number_never_used_is_kept_even_if_higher(self) -> None:
        store = FakeStore([rule(1, "One")])
        await self.run_all(store, "1. One\n40. Forty\n")
        self.assertEqual(sorted(r.number for r in store.rules), [1, 40])

    async def test_a_rule_another_dm_changed_meanwhile_is_left_alone(self) -> None:
        store = FakeStore([rule(2, "Old")])
        items = sync.items_of(diff_of(store.rules, "2. New\n"))
        store.rules[0] = rule(2, "Their words", None, version=2)
        applied = await sync.apply(store, GUILD, CID, DM, items)
        self.assertEqual(store.rules[0].rule, "Their words")
        self.assertEqual(applied.done, [])
        self.assertIn("Rule 2 was left as it is: " + house.CHANGED, applied.skipped[0])

    async def test_a_rule_removed_meanwhile_is_said_not_crashed_on(self) -> None:
        store = FakeStore([rule(2, "Old")])
        items = sync.items_of(diff_of(store.rules, "2. New\n"))
        store.rules.clear()
        applied = await sync.apply(store, GUILD, CID, DM, items)
        self.assertIn(house.GONE, applied.skipped[0])

    async def test_someone_who_is_not_a_dm_changes_nothing(self) -> None:
        store = FakeStore([rule(1, "A")])
        applied = await self.run_all(store, "1. B\n2. C\n", user=99)
        self.assertEqual([r.rule for r in store.rules], ["A"])
        self.assertEqual(len(applied.skipped), 2)

    async def test_a_full_list_says_so_and_goes_on(self) -> None:
        full = [rule(n, f"R{n}") for n in range(1, house.HOUSE_RULES_MAX + 1)]
        store = FakeStore(full)
        text = "\n".join(f"{r.number}. {r.rule}" for r in full[:-1]) + "\n201. One more\n"
        applied = await self.run_all(store, text)
        self.assertEqual(len(store.rules), house.HOUSE_RULES_MAX - 1)  # only the removal was done
        self.assertIn(house.FULL, applied.skipped[0])

    async def test_the_result_is_short(self) -> None:
        applied = sync.Applied([f"Added house rule {n}." for n in range(30)], ["x"] * 9)
        lines = sync.result_text(applied).splitlines()
        self.assertLessEqual(len(lines), 1 + 8 + 1 + 5 + 1)
        self.assertEqual(sync.result_text(sync.Applied()), "Nothing to change.")


if __name__ == "__main__":
    unittest.main()
