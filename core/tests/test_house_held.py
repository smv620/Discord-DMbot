"""A button on each house rule the file read held back for a clash (#3841): it opens the same
"which one applies" question as a typed rule with the held rule's words in it, only a DM may press
it (read from the campaign on every press), and nothing is saved until the DM picks."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.rules import house_file_sync as sync
from dmbot.rules.conflicts import Checked, Disagreement, Existing
from dmbot.rules.wins import RuleRef
from dmbot.ui import house_conflicts, house_held
from tests.test_house_rules_ui import DM, GUILD, PLAYER, UITest, rule

WORDS = "Drinking a potion is a bonus action"


async def clash_with_rule_2(texts: Any, changing: Any) -> Checked:
    """A check that finds every checked rule clashing with the campaign's rule 2."""
    other = Existing(2, rule(2).rule)
    return Checked(tuple(Disagreement(i + 1, other) for i in range(len(texts))))


class Held(UITest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store.seed("c1", 2)  # rules 1 and 2
        self.wins = SimpleNamespace(record=AsyncMock())
        self.bot.house_wins = self.wins

        self.wanted: list[Any] = []
        plain_add = self.store.add

        async def add_from_file(*args: Any, **kw: Any) -> Any:
            self.wanted.append(kw.pop("wanted", None))
            kw.pop("scenario", None)
            kw.pop("source_name", None)
            return await plain_add(*args, **kw)

        self.store.add = add_from_file  # the file's add also carries the file's number

    async def held(self, kind: str = sync.ADD) -> sync.Held:
        if kind == sync.ADD:
            items = [sync.Item(sync.ADD, 9, WORDS)]
        else:
            current = (await self.store.list(GUILD, "c1"))[0]
            items = [
                sync.Item(
                    sync.CHANGE, current.number, WORDS, None, current.rule, None, current.version
                )
            ]
        applied = await sync.apply(self.store, GUILD, "c1", DM, items, clash_with_rule_2)
        self.assertEqual(self.store.calls, [])  # the file read saved nothing
        (held,) = applied.held
        return held

    def view(self, held: sync.Held) -> Any:
        return house_held.HeldView(
            self.bot, self.campaigns["c1"], [held], source="upload", source_name="rules.txt"
        )

    @staticmethod
    def asked(it: Any) -> tuple[str, Any]:
        text, kw = it.response.sent[-1]
        return str(text), kw["view"]

    async def test_the_file_read_names_each_held_rule_and_what_it_clashes_with(self) -> None:
        held = await self.held()
        self.assertEqual((held.item.number, held.item.rule), (9, WORDS))
        self.assertEqual([o.number for o in held.others], [2])

    async def test_each_held_rule_gets_a_button(self) -> None:
        view = self.view(await self.held())
        self.assertEqual(self.labels(view), ["Add rule 9…"])
        changed = self.view(await self.held(sync.CHANGE))
        self.assertEqual(len(changed.children), 1)
        self.assertTrue(str(changed.children[0].label).startswith("Change rule"))

    async def test_the_button_opens_the_question_with_the_held_rules_words(self) -> None:
        view = self.view(await self.held())
        it = self.it()
        await view.children[0].callback(it)
        text, question = self.asked(it)
        self.assertIn(WORDS, text)
        self.assertIn("rule 2", text)
        self.assertEqual(
            self.labels(question), ["Replace rule 2", "New rule wins", "Rule 2 wins", "Cancel"]
        )
        self.assertEqual(self.store.calls, [])  # nothing is saved until the DM picks

    async def test_a_player_is_refused_and_nothing_opens(self) -> None:
        view = self.view(await self.held())
        it = self.it(PLAYER)
        await view.children[0].callback(it)
        self.assertEqual(it.response.sent[-1][0], house_conflicts.ONLY_DMS)
        self.assertEqual(self.store.calls, [])

    async def test_a_dm_removed_since_the_read_is_refused_on_the_press(self) -> None:
        view = self.view(await self.held())
        gone = replace(self.campaigns["c1"], dm_user_ids=frozenset())
        self.bot.campaigns.get = AsyncMock(return_value=gone)
        it = self.it()
        await view.children[0].callback(it)
        self.assertEqual(it.response.sent[-1][0], house_conflicts.ONLY_DMS)

    async def test_cancel_saves_nothing(self) -> None:
        view = self.view(await self.held())
        it = self.it()
        await view.children[0].callback(it)
        _, question = self.asked(it)
        await self.press(question, "Cancel").callback(self.it())
        self.assertEqual(self.store.calls, [])
        self.wins.record.assert_not_awaited()

    async def test_keeping_both_adds_the_rule_with_the_files_number_and_remembers_the_winner(
        self,
    ) -> None:
        view = self.view(await self.held())
        it = self.it()
        await view.children[0].callback(it)
        _, question = self.asked(it)
        await self.press(question, "New rule wins").callback(self.it())
        self.assertEqual([c[0] for c in self.store.calls], ["add"])
        self.assertEqual(self.wanted, [9])
        winner, loser = self.wins.record.await_args.args[2:4]
        self.assertEqual((winner, loser), (RuleRef("", 3), RuleRef("", 2)))

    async def test_a_held_change_edits_that_rule_when_the_dm_picks(self) -> None:
        held = await self.held(sync.CHANGE)
        view = self.view(held)
        it = self.it()
        await view.children[0].callback(it)
        _, question = self.asked(it)
        self.assertEqual(self.labels(question)[0], "New rule wins")  # no Replace for a change
        await self.press(question, "New rule wins").callback(self.it())
        self.assertEqual([c[0] for c in self.store.calls], ["edit"])
        self.assertEqual(self.store.calls[0][3], held.item.number)

    async def test_more_held_rules_than_buttons_are_left_to_the_typed_way(self) -> None:
        held = await self.held()
        many = [sync.Held(sync.Item(sync.ADD, n, WORDS), held.others) for n in range(30)]
        view = house_held.HeldView(
            self.bot, self.campaigns["c1"], many, source="upload", source_name=None
        )
        self.assertEqual(len(view.children), house_held.MAX_BUTTONS)
