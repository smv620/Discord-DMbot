"""The shared-rules update note and the Cherry pick screen (#1191): wording, Discord's limits
(25 choices to a menu, 5 rows), and the session-start rules. Fake stores, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.dm_screen import set_update
from dmbot.rules import rule_sets, set_diff


def update(n: int, notified: int | None = None) -> rule_sets.Update:
    old = {"rules": [], "link": None}
    new = {"rules": [{"number": i, "rule": f"Rule {i}", "instead": None} for i in range(1, n + 1)]}
    follow = rule_sets.Follow(
        "s", "house_rules", "Red Table", "Red", 3, old, True, 1, None, notified
    )
    offer = rule_sets.Offer("s", "house_rules", "Red Table", "Red", 4, new | {"link": None}, None)
    return rule_sets.Update(follow, offer, tuple(set_diff.diff(old, offer.content)))


def campaign() -> Any:
    return SimpleNamespace(guild_id=1, id="a" * 32, dm_user_ids=[7])


class Wording(unittest.TestCase):
    def test_the_note_says_what_changed_and_what_happens_if_nothing_is_pressed(self) -> None:
        text = set_update.note_text(update(2))
        self.assertIn("Red Table", text)
        self.assertIn("(v4): 2 rules", text)
        self.assertIn("or DMbot uses v4 at your next session", text)

    def test_three_buttons_that_survive_a_restart(self) -> None:
        view = set_update.note_view("a" * 32)
        labels = [i.item.label for i in view.children]  # type: ignore[attr-defined]
        self.assertEqual(labels, ["Use all changes", "Pick changes", "See changes"])

    def test_the_change_list_is_cut_to_discords_limit(self) -> None:
        self.assertLessEqual(len(set_update.changes_text(update(400))), 2100)


class Picking(unittest.TestCase):
    def test_every_change_starts_kept_and_a_page_holds_25(self) -> None:
        view = set_update.PickView(campaign(), update(60))
        select = next(c for c in view.children if hasattr(c, "options"))
        self.assertEqual(len(select.options), 25)  # type: ignore[attr-defined]
        self.assertTrue(all(o.default for o in select.options))  # type: ignore[attr-defined]
        self.assertEqual(len(view.accepted), 60)
        self.assertLessEqual(len(view.children), 4)  # menu, back, more, apply: rows are enough
        self.assertIn("page 1 of 3", view.text())

    def test_a_short_list_has_no_paging(self) -> None:
        view = set_update.PickView(campaign(), update(3))
        labels = [getattr(c, "label", None) for c in view.children]
        self.assertEqual([x for x in labels if x], ["Save my picks"])


class SessionStart(unittest.IsolatedAsyncioTestCase):
    def bot(self, pending: rule_sets.Update | None, gone: bool = False) -> Any:
        sets = SimpleNamespace(
            pending_update=AsyncMock(return_value=pending),
            is_gone=AsyncMock(return_value=gone),
            stop_updating=AsyncMock(),
            following=AsyncMock(return_value=[SimpleNamespace(name="Red Table")]),
            mark_notified=AsyncMock(),
            take_update=AsyncMock(),
        )
        return SimpleNamespace(
            rule_sets=sets,
            post=AsyncMock(return_value=object()),
            post_message=AsyncMock(return_value=object()),
        )

    async def test_a_new_update_is_announced_once_and_not_applied(self) -> None:
        bot = self.bot(update(2))
        await set_update.check_update(bot, campaign(), 99)
        bot.post_message.assert_awaited_once()
        bot.rule_sets.mark_notified.assert_awaited_once_with(1, "a" * 32, 4)
        bot.rule_sets.take_update.assert_not_called()

    async def test_an_update_left_alone_is_followed_at_the_next_start(self) -> None:
        bot = self.bot(update(2, notified=4))
        await set_update.check_update(bot, campaign(), 99)
        bot.rule_sets.take_update.assert_awaited_once_with(1, "a" * 32, 7)
        bot.post_message.assert_not_called()

    async def test_nothing_new_says_nothing(self) -> None:
        bot = self.bot(None)
        await set_update.check_update(bot, campaign(), 99)
        bot.post.assert_not_called()
        bot.post_message.assert_not_called()

    async def test_a_set_that_is_gone_is_said_once(self) -> None:
        bot = self.bot(None, gone=True)
        await set_update.check_update(bot, campaign(), 99)
        bot.rule_sets.stop_updating.assert_awaited_once()
        self.assertIn("aren't shared any more", bot.post.await_args.args[1])

    async def test_a_failure_never_escapes(self) -> None:
        bot = self.bot(None)
        bot.rule_sets.pending_update.side_effect = RuntimeError("down")
        await set_update.check_update(bot, campaign(), 99)


if __name__ == "__main__":
    unittest.main()
