"""Saving a house rule that disagrees with another (#2284): the DM is shown both and picks, and
nothing is saved before they do. The check's answer is a fake; the real wording and parsing are
tested in test_rule_conflicts, and the stored pairs in test_rule_wins."""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import Reply
from dmbot.rules import conflicts, house_file_sync
from dmbot.rules.conflicts import Checked, Disagreement, Existing
from dmbot.rules.wins import RuleRef
from dmbot.ui import house_rules as ui
from tests.test_house_rules_ui import DM, PLAYER, UITest


def says(*places: int) -> Any:
    """A check that answers: the one new rule disagrees with the existing rules at `places`."""

    async def complete(system: str, text: str, **_: Any) -> Reply:
        answer = {"checks": [{"new": 1, "disagrees_with": list(places)}]}
        return Reply(json.dumps(answer), cut=False)

    return SimpleNamespace(complete=AsyncMock(side_effect=complete))


class Disagreeing(UITest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store.seed("c1", 2)  # rules 1 and 2; the list is newest first: place 1 is rule 2
        self.wins = SimpleNamespace(record=AsyncMock())
        self.bot.house_wins = self.wins
        self.bot.rule_conflict_ai = says(1)

        async def everything(guild_id: int, cid: str) -> Any:
            return await self.store.list(guild_id, cid)

        self.bot.all_house_rules = everything

    async def add(self, words: str = "Drinking a potion is a bonus action") -> Any:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = words
        return form, await self.submit(form)

    @staticmethod
    def question(it: Any) -> tuple[str, Any]:
        call = it.followup.send.await_args_list[-1]
        return str(call.args[0]), call.kwargs["view"]

    async def test_nothing_is_saved_until_the_dm_picks(self) -> None:
        _, it = await self.add()
        text, view = self.question(it)
        self.assertEqual([c[0] for c in self.store.calls], [])
        self.assertIn("rule 2", text)
        self.assertIn("DMbot never picks", text)
        self.assertEqual(
            self.labels(view),
            ["Replace rule 2", "New rule wins", "Rule 2 wins", "Cancel"],
        )

    async def test_keeping_both_with_the_new_rule_winning_remembers_it(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "New rule wins").callback(self.it())
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        winner, loser = self.wins.record.await_args.args[2:4]
        self.assertEqual((winner, loser), (RuleRef("", 3), RuleRef("", 2)))

    async def test_keeping_both_with_the_old_rule_winning_remembers_that(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "Rule 2 wins").callback(self.it())
        winner, loser = self.wins.record.await_args.args[2:4]
        self.assertEqual((winner, loser), (RuleRef("", 2), RuleRef("", 3)))

    async def test_replace_changes_the_old_rule_and_adds_nothing(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "Replace rule 2").callback(self.it())
        self.assertEqual([c[0] for c in self.store.calls], ["edit"])
        self.assertEqual(self.store.calls[0][3], 2)
        self.wins.record.assert_not_awaited()

    async def test_cancel_saves_nothing(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "Cancel").callback(self.it())
        self.assertEqual(self.store.calls, [])
        self.wins.record.assert_not_awaited()

    async def test_every_label_fits_a_phone(self) -> None:
        followed = SimpleNamespace(number=12, rule="Potions are free", supersedes=None)
        followed.set_name = "The Very Long Pirate Pack Name"

        async def everything(guild_id: int, cid: str) -> Any:
            return [*await self.store.list(guild_id, cid), followed]

        self.bot.all_house_rules = everything
        self.bot.rule_conflict_ai = says(3)
        _, it = await self.add()
        for label in self.labels(self.question(it)[1]):
            self.assertLessEqual(len(label), 25, label)

    async def test_two_presses_at_once_save_the_rule_once(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        button = self.press(view, "New rule wins")
        await asyncio.gather(button.callback(self.it()), button.callback(self.it()))
        self.assertEqual([c[0] for c in self.store.calls], ["add"])

    async def test_a_choice_that_could_not_be_stored_is_not_called_remembered(self) -> None:
        self.wins.record.side_effect = OSError
        _, it = await self.add()
        _, view = self.question(it)
        press = self.it()
        await self.press(view, "New rule wins").callback(press)
        self.assertEqual([c[0] for c in self.store.calls], ["add"])  # still saved
        self.assertNotIn("is remembered", str(press.followup.send.await_args_list))
        self.assertNotIn("is remembered", str(press.edit_original_response.await_args_list))

    async def test_only_a_dm_can_answer(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        self.assertFalse(await view.interaction_check(self.it(PLAYER)))
        self.assertTrue(await view.interaction_check(self.it(DM)))

    async def test_a_rule_that_agrees_is_just_saved(self) -> None:
        self.bot.rule_conflict_ai = says()
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertNotIn("Couldn't check", self.shown(it)[0])

    async def test_a_check_that_fails_saves_the_rule_and_says_so(self) -> None:
        self.bot.rule_conflict_ai = SimpleNamespace(complete=AsyncMock(side_effect=OSError))
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertIn("Couldn't check", self.shown(it)[0])

    async def test_without_the_ai_nothing_is_checked(self) -> None:
        self.bot.rule_conflict_ai = None
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertNotIn("Couldn't check", self.shown(it)[0])

    async def test_editing_a_rule_never_compares_it_with_itself(self) -> None:
        shown: list[list[Existing]] = []
        real = conflicts.check

        async def spy(complete: Any, new: Any, existing: Any) -> Checked:
            shown.append(list(existing))
            return await real(complete, new, existing)

        with patch.object(conflicts, "check", spy):
            form = ui.EditForm(self.campaigns["c1"], (await self.store.list(1, "c1"))[0], 0)
            form.rule._value = "Rule 2, reworded"
            await self.submit(form)
        self.assertEqual([e.number for e in shown[0]], [1])

    async def test_a_followed_sets_rule_is_offered_without_replace(self) -> None:
        followed = SimpleNamespace(number=2, rule="Potions are free", supersedes=None)
        followed.set_name = "Red Table"

        async def everything(guild_id: int, cid: str) -> Any:
            return [*await self.store.list(guild_id, cid), followed]

        self.bot.all_house_rules = everything
        self.bot.rule_conflict_ai = says(3)  # place 3: the followed set's rule
        _, it = await self.add()
        text, view = self.question(it)
        self.assertIn("Red Table rule 2", text)
        self.assertNotIn("Replace rule 2", self.labels(view))
        await self.press(view, "Set rule 2 wins").callback(self.it())
        winner = self.wins.record.await_args.args[2]
        self.assertEqual(winner, RuleRef("Red Table", 2))

    async def test_two_disagreements_are_asked_one_after_the_other(self) -> None:
        self.bot.rule_conflict_ai = says(1, 2)
        _, it = await self.add()
        _, view = self.question(it)
        again = self.it()
        await self.press(view, "New rule wins").callback(again)
        text, view2 = again.response.edited[0]
        self.assertIn("rule 1", text)
        self.assertNotIn("Replace rule 1", self.labels(view2))  # it is saved already
        await self.press(view2, "Rule 1 wins").callback(self.it())
        self.assertEqual(self.wins.record.await_count, 2)
        self.assertEqual([c[0] for c in self.store.calls], ["add"])  # saved once


class FileStore:
    """Just what applying a file uses of the store."""

    def __init__(self) -> None:
        self.added: list[str] = []
        self.changed: list[int] = []

    async def list(self, guild_id: int, cid: str) -> list[Any]:
        return []

    async def add(
        self, guild_id: int, cid: str, user_id: int, rule: str, *a: Any, **kw: Any
    ) -> Any:
        self.added.append(rule)
        return SimpleNamespace(number=kw["wanted"])

    async def edit(
        self, guild_id: int, cid: str, user_id: int, number: int, *a: Any, **kw: Any
    ) -> Any:
        self.changed.append(number)
        return SimpleNamespace(number=number)


class FromTheFile(unittest.IsolatedAsyncioTestCase):
    def item(self, number: int, words: str) -> Any:
        return house_file_sync.Item(house_file_sync.ADD, number, words, None)

    async def test_a_file_rule_that_disagrees_is_held_back_and_the_rest_added(self) -> None:
        store: Any = FileStore()

        async def check(texts: Any, changing: Any = frozenset()) -> Checked:
            self.assertEqual(len(texts), 2)  # one call for the whole file
            return Checked((Disagreement(2, Existing(1, "Rule number 1")),))

        items = [self.item(5, "Crits double"), self.item(6, "Potions are free")]
        done = await house_file_sync.apply(store, 1, "c1", DM, items, check)
        self.assertEqual(len(done.done), 1)
        self.assertEqual(len(done.skipped), 1)
        self.assertIn("rule 1", done.skipped[0])
        self.assertIn("pick a winner", done.skipped[0])
        self.assertEqual(store.added, ["Crits double"])

    async def test_a_file_check_that_fails_still_adds_and_says_so(self) -> None:
        store: Any = FileStore()

        async def check(texts: Any, changing: Any = frozenset()) -> Checked:
            return Checked(unchecked=True)

        done = await house_file_sync.apply(
            store,
            1,
            "c1",
            DM,
            [self.item(5, "Crits double")],
            check,
        )
        self.assertIn(house_file_sync.UNCHECKED, done.skipped)
        self.assertEqual(store.added, ["Crits double"])


class Positions(unittest.IsolatedAsyncioTestCase):
    async def test_a_blank_rule_does_not_shift_the_answers(self) -> None:
        async def complete(system: str, text: str, **_: Any) -> Reply:
            answer = {
                "checks": [{"new": 1, "disagrees_with": []}, {"new": 2, "disagrees_with": [1]}]
            }
            return Reply(json.dumps(answer), cut=False)

        found = await conflicts.check(complete, ["First", "  ", "Third"], [Existing(1, "Old")])
        self.assertEqual([d.new for d in found.disagreements], [3])  # the third, not the second

    async def test_more_rules_than_one_call_takes_are_said_to_be_unchecked(self) -> None:
        async def complete(system: str, text: str, **_: Any) -> Reply:
            checks = [{"new": n, "disagrees_with": []} for n in range(1, conflicts.MAX_NEW + 1)]
            return Reply(json.dumps({"checks": checks}), cut=False)

        rules = [f"Rule {n}" for n in range(conflicts.MAX_NEW + 1)]
        found = await conflicts.check(complete, rules, [Existing(1, "Old")])
        self.assertTrue(found.unchecked)

    async def test_a_call_that_hangs_is_unchecked(self) -> None:
        async def complete(system: str, text: str, **_: Any) -> Reply:
            await asyncio.sleep(10)
            raise AssertionError

        with patch.object(conflicts, "TIMEOUT_S", 0.01):
            found = await conflicts.check(complete, ["New"], [Existing(1, "Old")])
        self.assertTrue(found.unchecked)

    async def test_a_changed_rule_that_disagrees_is_held_back_and_the_rest_applied(self) -> None:
        store: Any = FileStore()
        seen: list[Any] = []

        async def check(texts: Any, changing: Any) -> Checked:
            seen.append((list(texts), changing))
            return Checked((Disagreement(1, Existing(2, "Potions take an item interaction")),))

        items = [
            house_file_sync.Item(house_file_sync.CHANGE, 3, "Potions are a bonus action", None),
            house_file_sync.Item(house_file_sync.CHANGE, 4, "Crits double", None),
        ]
        done = await house_file_sync.apply(store, 1, "c1", DM, items, check)
        self.assertEqual(len(seen), 1)  # one call for the whole file
        self.assertEqual(seen[0][1], frozenset({3, 4}))  # the old words are being replaced
        self.assertEqual(store.changed, [4])
        self.assertEqual(len(done.skipped), 1)
        self.assertIn("wasn't changed", done.skipped[0])


class Limits(unittest.IsolatedAsyncioTestCase):
    async def test_existing_rules_past_the_limit_are_said_to_be_unchecked(self) -> None:
        async def complete(system: str, text: str, **_: Any) -> Reply:
            return Reply(json.dumps({"checks": [{"new": 1, "disagrees_with": []}]}), cut=False)

        many = [Existing(n, f"Rule {n}") for n in range(1, conflicts.MAX_EXISTING + 2)]
        found = await conflicts.check(complete, ["New"], many)
        self.assertTrue(found.unchecked)

    async def test_a_plan_without_the_ai_makes_no_call_and_says_nothing(self) -> None:
        from dmbot.ui import house_conflicts

        ai = says(1)
        bot = SimpleNamespace(
            rule_conflict_ai=ai,
            plan_allows=AsyncMock(return_value=False),
            all_house_rules=AsyncMock(),
        )
        campaign = SimpleNamespace(guild_id=1, id="c1")
        found = await house_conflicts.look(bot, campaign, ["New rule"])  # type: ignore[arg-type]
        self.assertEqual(found, Checked())
        ai.complete.assert_not_awaited()
        bot.all_house_rules.assert_not_awaited()
