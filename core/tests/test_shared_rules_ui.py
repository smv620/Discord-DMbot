"""The Shared rules menu (#1190, slice 2): who may use it, the paid-plan check, and that
following asks first. Fake stores and a fake interaction: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.rules import rule_sets
from dmbot.ui import shared_rules


def campaign(dms: tuple[int, ...] = (7,)) -> Any:
    return SimpleNamespace(guild_id=1, id="c1", dm_user_ids=list(dms))


def interaction(user: int, bot: Any) -> Any:
    sent: list[tuple[str, Any]] = []

    async def send(text: str, **kw: Any) -> None:
        sent.append((text, kw.get("view")))

    done = {"v": False}

    async def defer(**_: Any) -> None:
        done["v"] = True

    it = MagicMock()
    it.user = SimpleNamespace(id=user)
    it.client = bot
    it.response.is_done = lambda: done["v"]
    it.response.defer = defer
    it.response.send_message = send
    it.followup.send = send
    it.sent = sent
    return it


def bot_with(store: Any, *, refusal: str | None = None, camp: Any = None) -> Any:
    camp = camp or campaign()
    return SimpleNamespace(
        rule_sets=store,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
        plan_gate=AsyncMock(return_value=refusal),
        house_rules=SimpleNamespace(list=AsyncMock(return_value=[])),
        house_file_links=None,
    )


class Sharing(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_cannot_share(self) -> None:
        store = MagicMock()
        bot = bot_with(store)
        it = interaction(99, bot)
        await shared_rules.share(it, campaign(), "Mine")
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        store.publish.assert_not_called()

    async def test_the_plan_is_checked_before_anything_is_shared(self) -> None:
        store = MagicMock()
        bot = bot_with(store, refusal="Sharing house rules needs a paid plan.")
        it = interaction(7, bot)
        await shared_rules.share(it, campaign(), "Mine")
        self.assertEqual(it.sent[0][0], "Sharing house rules needs a paid plan.")
        store.publish.assert_not_called()
        bot.plan_gate.assert_awaited_once_with("share", 1, bot.campaigns.get.return_value, 7)

    async def test_sharing_gives_a_code(self) -> None:
        store = MagicMock()
        store.publish = AsyncMock(return_value=rule_sets.SharedSet("s", "Mine", True, 1))
        store.make_code = AsyncMock(return_value="ABCDEFGHJK")
        it = interaction(7, bot_with(store))
        await shared_rules.share(it, campaign(), "Mine")
        self.assertIn("`ABCDEFGHJK`", it.sent[0][0])

    async def test_a_store_problem_is_told_in_its_words(self) -> None:
        store = MagicMock()
        store.publish = AsyncMock(side_effect=rule_sets.RuleSetError(rule_sets.NO_RULES))
        it = interaction(7, bot_with(store))
        await shared_rules.share(it, campaign(), "Mine")
        self.assertEqual(it.sent[0][0], rule_sets.NO_RULES)


class Following(unittest.IsolatedAsyncioTestCase):
    def offer(self) -> rule_sets.Offer:
        content = {"rules": [{"number": 1, "rule": "x", "instead": None}], "link": None}
        return rule_sets.Offer("s", "house_rules", "Red", "Red Table", 2, content, "CODE")

    async def test_a_code_shows_the_set_and_copies_nothing(self) -> None:
        store = MagicMock()
        store.by_code = AsyncMock(return_value=self.offer())
        store.follow = AsyncMock()
        it = interaction(7, bot_with(store))
        await shared_rules.look_up(it, campaign(), "code")
        text, view = it.sent[0]
        self.assertIn("1 house rule (", text)
        self.assertIsNotNone(view)
        store.follow.assert_not_called()

    async def test_a_wrong_code_says_so(self) -> None:
        store = MagicMock()
        store.by_code = AsyncMock(side_effect=rule_sets.RuleSetError(rule_sets.BAD_CODE))
        it = interaction(7, bot_with(store))
        await shared_rules.look_up(it, campaign(), "nope")
        self.assertEqual(it.sent[0][0], rule_sets.BAD_CODE)

    async def test_yes_follows_and_rechecks_the_person_is_still_a_dm(self) -> None:
        store = MagicMock()
        follow = rule_sets.Follow("s", "house_rules", "Red", "Red Table", 2, {}, True, 5)
        store.follow = AsyncMock(return_value=follow)
        view = shared_rules.ConfirmFollow(campaign(), self.offer())
        gone = interaction(99, bot_with(store))  # no longer a DM
        await view._yes(gone)
        store.follow.assert_not_called()
        it = interaction(7, bot_with(store))
        await view._yes(it)
        store.follow.assert_awaited_once()
        self.assertIn("Now using", it.sent[0][0])


if __name__ == "__main__":
    unittest.main()
