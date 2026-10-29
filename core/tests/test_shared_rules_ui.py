"""The Shared rules menu (#1190, slice 2): who may use it, the paid-plan check, and that
following asks first. Fake stores and a fake interaction: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def test_with_relationships_to_offer_the_dm_ticks_before_anything_is_shared(self) -> None:
        from dmbot.memory import set_definitions as sd
        from tests.test_set_definitions import term

        offered = [sd.definition_of(term()), sd.definition_of(term("guards", "guards"))]
        store = MagicMock()
        store.publish = AsyncMock(return_value=rule_sets.SharedSet("s", "Mine", True, 1))
        store.make_code = AsyncMock(return_value="ABCDEFGHJK")
        bot = bot_with(store)
        bot.memory = object()
        with patch("dmbot.memory.set_definitions.own_definitions", AsyncMock(return_value=offered)):
            it = interaction(7, bot)
            await shared_rules.share(it, campaign(), "Mine")
        text, menu = it.sent[0]
        self.assertIn("Pick the relationships to share", text)
        self.assertIn("vows loyalty to", text)  # what each says is shown
        store.publish.assert_not_called()  # nothing leaves until the DM presses Share
        menu.ticked = {"guards"}  # the DM unticks one
        it2 = interaction(7, bot)
        await menu._share(it2)
        args = store.publish.await_args.args
        self.assertEqual([d["key"] for d in args[-1]], ["guards"])
        self.assertIn("Shared with it: 1 relationship", it2.sent[0][0])

    async def test_sharing_the_rules_only_sends_no_relationship(self) -> None:
        from dmbot.memory import set_definitions as sd
        from tests.test_set_definitions import term

        store = MagicMock()
        store.publish = AsyncMock(return_value=rule_sets.SharedSet("s", "Mine", True, 1))
        store.make_code = AsyncMock(return_value="ABCDEFGHJK")
        bot = bot_with(store)
        bot.memory = object()
        with patch(
            "dmbot.memory.set_definitions.own_definitions",
            AsyncMock(return_value=[sd.definition_of(term())]),
        ):
            it = interaction(7, bot)
            await shared_rules.share(it, campaign(), "Mine")
        await it.sent[0][1]._none(interaction(7, bot))
        self.assertEqual(store.publish.await_args.args[-1], [])

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


class Robustness(unittest.IsolatedAsyncioTestCase):
    async def test_an_unexpected_store_error_still_gets_an_answer(self) -> None:
        store = MagicMock()
        store.make_code = AsyncMock(side_effect=RuntimeError("db down"))
        it = interaction(7, bot_with(store))
        await shared_rules.new_code(it, campaign())
        self.assertEqual(it.sent[0][0], shared_rules.COULDNT)

    async def test_a_missing_store_is_told_not_left_thinking(self) -> None:
        for call in (shared_rules.new_code, shared_rules.stop_sharing):
            it = interaction(7, bot_with(None))
            await call(it, campaign())
            self.assertEqual(it.sent[0][0], shared_rules.NOT_READY)

    async def test_a_non_dm_is_refused_on_every_handler(self) -> None:
        store = MagicMock()
        for call in (shared_rules.new_code, shared_rules.stop_sharing, shared_rules.open_menu):
            it = interaction(99, bot_with(store))
            await call(it, campaign())
            self.assertEqual(it.sent[0][0], shared_rules.ONLY_DMS)
        store.make_code.assert_not_called()

    async def test_a_copy_that_no_longer_updates_says_so_without_blaming_the_publisher(
        self,
    ) -> None:
        # Also what a campaign restored from a backup shows: the publisher may still share it.
        follow = rule_sets.Follow("s", "house_rules", "Red", "Far", 3, {}, False, 5)
        store = MagicMock()
        store.published = AsyncMock(return_value=None)
        store.following = AsyncMock(return_value=[follow])
        it = interaction(7, bot_with(store))
        await shared_rules.open_menu(it, campaign())
        text = it.sent[0][0]
        self.assertIn("no longer gets updates", text)
        # The whole way back, in order: a new code is refused while the old copy is followed.
        self.assertIn("ask Far for a new code", text)
        self.assertLess(
            text.index(shared_rules.UNFOLLOW_LABEL), text.index(shared_rules.FOLLOW_LABEL)
        )
        self.assertNotIn("isn't shared any more", text)

    async def test_undo_rechecks_the_person(self) -> None:
        store = MagicMock()
        store.restore = AsyncMock()
        follow = rule_sets.Follow("s", "house_rules", "Red", "T", 1, {}, True, 5)
        await shared_rules.UndoUnfollow(campaign(), follow)._undo(interaction(99, bot_with(store)))
        store.restore.assert_not_called()

    def test_the_name_box_matches_the_limit(self) -> None:
        self.assertEqual(shared_rules.ShareForm.name.max_length, rule_sets.NAME_MAX)

    def test_the_confirm_quotes_a_few_rules(self) -> None:
        content = {"rules": [{"number": i, "rule": f"rule {i}", "instead": None} for i in range(9)]}
        self.assertEqual(shared_rules._preview(content).count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
