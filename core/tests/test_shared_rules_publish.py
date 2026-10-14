"""Publishing a new version and naming editors (#1191): the diff shown first, the checks on
every press, and that nothing is published without a press. Fake stores, no Discord."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRule
from dmbot.ui import shared_rules_publish as pub
from tests.test_shared_rules_ui import bot_with, campaign, interaction

SET = rule_sets.SharedSet("s", "Red Table rules", True, 4)
BEFORE = {
    "rules": [{"number": 1, "rule": "Flanking gives advantage", "instead": None}],
    "link": None,
}


def rule(number: int, text: str) -> HouseRule:
    return HouseRule(number, "c1", text, None, None, None, 7, 1, 1)


def store(**kw: Any) -> Any:
    s = MagicMock()
    s.current_content = AsyncMock(return_value=(SET, BEFORE))
    s.published = AsyncMock(return_value=SET)
    s.version_log = AsyncMock(return_value=[])
    s.editors = AsyncMock(return_value=[])
    for name, value in kw.items():
        setattr(s, name, value)
    return s


class Publishing(unittest.IsolatedAsyncioTestCase):
    async def test_the_changes_are_shown_first_and_nothing_is_published(self) -> None:
        s = store()
        bot = bot_with(s)
        bot.house_rules.list = AsyncMock(
            return_value=[rule(1, "Flanking gives advantage"), rule(2, "Crits double the dice")]
        )
        it = interaction(7, bot)
        await pub.show_changes(it, campaign())
        text, view = it.sent[0]
        self.assertIn("version 5", text)
        self.assertIn("1 rule changed", text)
        self.assertIn("New: rule 2", text)
        self.assertIsInstance(view, pub.ConfirmPublish)
        s.publish_changes.assert_not_called()

    async def test_nothing_new_says_so(self) -> None:
        s = store()
        bot = bot_with(s)
        bot.house_rules.list = AsyncMock(return_value=[rule(1, "Flanking gives advantage")])
        it = interaction(7, bot)
        await pub.show_changes(it, campaign())
        self.assertEqual(it.sent[0][0], pub.NOTHING_NEW)

    async def test_a_player_is_refused_and_a_set_not_shared_is_said(self) -> None:
        s = store()
        it = interaction(99, bot_with(s))
        await pub.show_changes(it, campaign())
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        none = store(current_content=AsyncMock(return_value=None))
        it2 = interaction(7, bot_with(none))
        await pub.show_changes(it2, campaign())
        self.assertEqual(it2.sent[0][0], pub.NOT_SHARED_HERE)

    async def test_publish_checks_the_plan_and_the_version_the_dm_saw(self) -> None:
        made = rule_sets.SharedSet("s", "Red Table rules", True, 5)
        s = store(publish_changes=AsyncMock(return_value=made))
        bot = bot_with(s, refusal="Sharing house rules needs a paid plan.")
        it = interaction(7, bot)
        await pub.ConfirmPublish(campaign(), 4)._yes(it)
        self.assertEqual(it.sent[0][0], "Sharing house rules needs a paid plan.")
        s.publish_changes.assert_not_called()
        # Someone else published meanwhile: the DM looks again, nothing is published.
        s2 = store(
            current_content=AsyncMock(
                return_value=(rule_sets.SharedSet("s", "R", True, 5), BEFORE)
            ),
            publish_changes=AsyncMock(return_value=made),
        )
        it2 = interaction(7, bot_with(s2))
        await pub.ConfirmPublish(campaign(), 4)._yes(it2)
        self.assertEqual(it2.sent[0][0], rule_sets.CHANGED_AGAIN)
        s2.publish_changes.assert_not_called()
        # The normal press publishes.
        s3 = store(publish_changes=AsyncMock(return_value=made))
        it3 = interaction(7, bot_with(s3))
        await pub.ConfirmPublish(campaign(), 4)._yes(it3)
        self.assertIn("version 5", it3.sent[0][0])
        s3.publish_changes.assert_awaited_once()


class Limits(unittest.IsolatedAsyncioTestCase):
    def test_a_long_diff_stays_under_the_message_limit(self) -> None:
        from dmbot.rules import set_diff

        changes = [
            set_diff.Change("added", n, None, {"number": n, "rule": "x" * 400, "instead": None})
            for n in range(1, 40)
        ]
        self.assertLess(len(pub.changes_text(changes)), pub.BODY_MAX + 40)

    async def test_bots_and_a_full_list_are_refused(self) -> None:
        s = store(add_editor=AsyncMock(), editors=AsyncMock(return_value=list(range(25))))
        bot = bot_with(s)
        bot.name_of = lambda guild, user: "Mia"
        it = interaction(7, bot)
        await pub.EditorsMenu(campaign(), [])._add(it, SimpleNamespace(id=8, bot=True))
        self.assertEqual(it.sent[0][0], "Bots can't edit rules.")
        it2 = interaction(7, bot)
        await pub.EditorsMenu(campaign(), [])._add(it2, SimpleNamespace(id=8, bot=False))
        self.assertIn("most co-editors", it2.sent[0][0])
        s.add_editor.assert_not_called()

    def test_the_remove_menu_shows_names_not_ids(self) -> None:
        menu = pub.EditorsMenu(campaign(), [8], {8: "Mia"})
        select = next(c for c in menu.children if isinstance(c, discord.ui.Select))
        self.assertEqual(select.options[0].label, "Mia")


class History(unittest.IsolatedAsyncioTestCase):
    async def test_the_log_and_the_editors_are_shown_by_name(self) -> None:
        log = [
            rule_sets.VersionLog(2, 7, 1_700_000_000, "suggestion"),
            rule_sets.VersionLog(1, 7, 1_699_000_000, "publisher"),
        ]
        s = store(version_log=AsyncMock(return_value=log), editors=AsyncMock(return_value=[8]))
        bot = bot_with(s)
        bot.name_of = lambda guild, user: {7: "Sam", 8: "Mia"}[user]
        it = interaction(7, bot)
        await pub.show_history(it, campaign())
        text, view = it.sent[0]
        self.assertIn("Version 2, by Sam", text)
        self.assertIn("from a player's suggestion", text)
        self.assertIn("Co-editors: Mia", text)
        self.assertIsInstance(view, pub.EditorsMenu)

    async def test_naming_an_editor_can_be_undone(self) -> None:
        s = store(add_editor=AsyncMock(), remove_editor=AsyncMock(return_value=True))
        bot = bot_with(s)
        bot.name_of = lambda guild, user: "Mia"
        it = interaction(7, bot)
        menu = pub.EditorsMenu(campaign(), [])
        await menu._add(it, SimpleNamespace(id=8))
        s.add_editor.assert_awaited_once_with(1, "c1", 7, 8)
        text, undo = it.sent[0]
        self.assertIn("co-editor now", text)
        it2 = interaction(7, bot)
        await undo._undo(it2)
        s.remove_editor.assert_awaited_once_with(1, "c1", 7, 8)

    async def test_a_store_refusal_is_told_in_its_words(self) -> None:
        s = store(add_editor=AsyncMock(side_effect=rule_sets.RuleSetError(rule_sets.EDITOR_NOT_DM)))
        it = interaction(7, bot_with(s))
        bot = it.client
        bot.name_of = lambda guild, user: "Mia"
        await pub.EditorsMenu(campaign(), [])._add(it, SimpleNamespace(id=8))
        self.assertEqual(it.sent[0][0], rule_sets.EDITOR_NOT_DM)


if __name__ == "__main__":
    unittest.main()
