"""Saving a house rule that disagrees with another (#2284): the DM is shown both and picks, and
nothing is saved before they do. The check's answer is a fake; the real wording and parsing are
tested in test_rule_conflicts, and the stored pairs in test_rule_wins."""

from __future__ import annotations

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
            ["Replace rule 2", "Keep both, this one wins", "Keep both, rule 2 wins", "Cancel"],
        )

    async def test_keeping_both_with_the_new_rule_winning_remembers_it(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "Keep both, this one wins").callback(self.it())
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        winner, loser = self.wins.record.await_args.args[2:4]
        self.assertEqual((winner, loser), (RuleRef("", 3), RuleRef("", 2)))

    async def test_keeping_both_with_the_old_rule_winning_remembers_that(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        await self.press(view, "Keep both, rule 2 wins").callback(self.it())
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

    async def test_only_a_dm_can_answer(self) -> None:
        _, it = await self.add()
        _, view = self.question(it)
        self.assertFalse(await view.interaction_check(self.it(PLAYER)))
        self.assertTrue(await view.interaction_check(self.it(DM)))

    async def test_a_rule_that_agrees_is_just_saved(self) -> None:
        self.bot.rule_conflict_ai = says()
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertNotIn("couldn't check", self.shown(it)[0])

    async def test_a_check_that_fails_saves_the_rule_and_says_so(self) -> None:
        self.bot.rule_conflict_ai = SimpleNamespace(complete=AsyncMock(side_effect=OSError))
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertIn("couldn't check", self.shown(it)[0])

    async def test_without_the_ai_nothing_is_checked(self) -> None:
        self.bot.rule_conflict_ai = None
        _, it = await self.add()
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertNotIn("couldn't check", self.shown(it)[0])

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
        await self.press(view, "Keep both, Red Table rule 2 wins").callback(self.it())
        winner = self.wins.record.await_args.args[2]
        self.assertEqual(winner, RuleRef("Red Table", 2))

    async def test_two_disagreements_are_asked_one_after_the_other(self) -> None:
        self.bot.rule_conflict_ai = says(1, 2)
        _, it = await self.add()
        _, view = self.question(it)
        again = self.it()
        await self.press(view, "Keep both, this one wins").callback(again)
        text, view2 = again.response.edited[0]
        self.assertIn("rule 1", text)
        self.assertNotIn("Replace rule 1", self.labels(view2))  # it is saved already
        await self.press(view2, "Keep both, rule 1 wins").callback(self.it())
        self.assertEqual(self.wins.record.await_count, 2)
        self.assertEqual([c[0] for c in self.store.calls], ["add"])  # saved once


class FileStore:
    """Just what applying a file uses of the store."""

    def __init__(self) -> None:
        self.added: list[str] = []

    async def list(self, guild_id: int, cid: str) -> list[Any]:
        return []

    async def add(
        self, guild_id: int, cid: str, user_id: int, rule: str, *a: Any, **kw: Any
    ) -> Any:
        self.added.append(rule)
        return SimpleNamespace(number=kw["wanted"])


class FromTheFile(unittest.IsolatedAsyncioTestCase):
    def item(self, number: int, words: str) -> Any:
        return house_file_sync.Item(house_file_sync.ADD, number, words, None)

    async def test_a_file_rule_that_disagrees_is_held_back_and_the_rest_added(self) -> None:
        store: Any = FileStore()

        async def check(texts: Any) -> Checked:
            self.assertEqual(len(texts), 2)  # one call for the whole file
            return Checked((Disagreement(2, Existing(1, "Rule number 1")),))

        items = [self.item(5, "Crits double"), self.item(6, "Potions are free")]
        done = await house_file_sync.apply(store, 1, "c1", DM, items, check)
        self.assertEqual(len(done.done), 1)
        self.assertEqual(len(done.skipped), 1)
        self.assertIn("rule 1", done.skipped[0])
        self.assertIn("choose which one wins", done.skipped[0])
        self.assertEqual(store.added, ["Crits double"])

    async def test_a_file_check_that_fails_still_adds_and_says_so(self) -> None:
        store: Any = FileStore()

        async def check(texts: Any) -> Checked:
            return Checked(unchecked=True)

        done = await house_file_sync.apply(
            store,
            1,
            "c1",
            DM,
            [self.item(5, "Crits double")],
            check,
        )
        self.assertIn(house_file_sync.UNCHECKED, done.done)
        self.assertEqual(store.added, ["Crits double"])
