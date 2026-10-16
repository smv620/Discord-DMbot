"""The Homebrew packs menu (#1527, part B): who may use it, the paid-plan check before anything
is published, that entries from a book are named as staying home, that following asks first, and
that a running session is told when the followed packs change. Fake stores and a fake
interaction: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.rules import homebrew_packs as packs
from dmbot.rules import rule_sets
from dmbot.rules.homebrew import Homebrew
from dmbot.ui import shared_packs
from tests.test_shared_rules_ui import campaign, interaction


def entry(name: str, *, book: bool = False) -> Homebrew:
    return Homebrew(
        id=1, campaign_id="c1", kind="monster", name=name, text="t", summary=None,
        players_may_know=False, created_by=7, created_at=1, updated_at=1, from_book=book,
    )  # fmt: skip


def store() -> Any:
    return MagicMock(spec=packs.HomebrewPackStore)


def bot_with(st: Any, *, refusal: str | None = None, mine: list[Homebrew] | None = None) -> Any:
    return SimpleNamespace(
        rule_sets=st,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        plan_gate=AsyncMock(return_value=refusal),
        homebrew=SimpleNamespace(list=AsyncMock(return_value=mine or [])),
        homebrew_changed=MagicMock(),
    )


def follow(name: str = "Frost", set_id: str = "a" * 32) -> rule_sets.Follow:
    return rule_sets.Follow(set_id, "homebrew", name, "Red Table", 2, {"entries": []}, True, 1)


def offer(kind: str = "homebrew") -> rule_sets.Offer:
    content = {
        "entries": [{"name": "Hollow King", "known": False}, {"name": "Lamp", "known": True}]
    }
    return rule_sets.Offer("s" * 32, kind, "Frost", "Red Table", 2, content, "CODE")


class Sharing(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_cannot_share(self) -> None:
        st = store()
        it = interaction(99, bot_with(st))
        await shared_packs.share(it, campaign(), "Mine")
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        st.publish_pack.assert_not_called()

    async def test_the_plan_is_checked_before_anything_is_published(self) -> None:
        st = store()
        bot = bot_with(st, refusal="Sharing needs a paid plan.")
        it = interaction(7, bot)
        await shared_packs.share(it, campaign(), "Mine")
        self.assertEqual(it.sent[0][0], "Sharing needs a paid plan.")
        st.publish_pack.assert_not_called()
        bot.plan_gate.assert_awaited_once()
        self.assertEqual(bot.plan_gate.await_args.args[0], "share")

    async def test_sharing_gives_a_code_and_names_what_stays_home(self) -> None:
        st = store()
        st.publish_pack = AsyncMock(
            return_value=packs.Published(
                rule_sets.SharedSet("s", "Mine", True, 1), ("Booked One", "Booked Two")
            )
        )
        st.make_pack_code = AsyncMock(return_value="ABCDEFGHJK")
        mine = [entry("A"), entry("Booked One", book=True), entry("Booked Two", book=True)]
        it = interaction(7, bot_with(st, mine=mine))
        await shared_packs.share(it, campaign(), "Mine")
        text = it.sent[0][0]
        self.assertIn("`ABCDEFGHJK`", text)
        self.assertIn("1 entry", text)
        self.assertIn("Not shared: Booked One, Booked Two. They come from a book", text)
        self.assertEqual(st.publish_pack.await_args.args[-1], mine)

    async def test_a_problem_is_told_in_its_words(self) -> None:
        st = store()
        st.publish_pack = AsyncMock(side_effect=rule_sets.RuleSetError(packs.NO_ENTRIES))
        it = interaction(7, bot_with(st))
        await shared_packs.share(it, campaign(), "Mine")
        self.assertEqual(it.sent[0][0], packs.NO_ENTRIES)

    def test_the_sentence_for_what_stays(self) -> None:
        self.assertEqual(shared_packs.stays_home([]), "")
        self.assertTrue(shared_packs.stays_home(["One"]).startswith("Not shared: One. They come"))
        many = shared_packs.stays_home([f"N{i}" for i in range(8)])
        self.assertIn("and 3 more", many)

    async def test_the_menu_says_before_sharing_what_would_stay(self) -> None:
        st = store()
        st.published_pack = AsyncMock(return_value=None)
        st.followed_packs = AsyncMock(return_value=[])
        mine = [entry("A"), entry("Booked", book=True)]
        it = interaction(7, bot_with(st, mine=mine))
        await shared_packs.open_menu(it, campaign())
        self.assertIn("Not shared: Booked. They come from a book", it.sent[0][0])

    async def test_stopping_asks_first(self) -> None:
        st = store()
        st.published_pack = AsyncMock(return_value=rule_sets.SharedSet("s", "Mine", True, 1))
        st.followed_packs = AsyncMock(return_value=[])
        it = interaction(7, bot_with(st))
        await shared_packs.open_menu(it, campaign())
        menu = it.sent[0][1]
        it2 = interaction(7, bot_with(st))
        await menu._stop(it2)
        self.assertIn("Stop sharing?", it2.sent[0][0])
        st.stop_sharing_pack.assert_not_called()


class Following(unittest.IsolatedAsyncioTestCase):
    async def test_a_code_shows_the_pack_and_copies_nothing(self) -> None:
        st = store()
        st.by_code = AsyncMock(return_value=offer())
        it = interaction(7, bot_with(st))
        await shared_packs.look_up(it, campaign(), "code")
        text, view = it.sent[0]
        self.assertIn("2 entries (update 2)", text)
        self.assertIn("1 of them is hidden from players", text)
        self.assertIn("Your own homebrew wins", text)
        st.follow_pack.assert_not_called()
        self.assertIsInstance(view, shared_packs.ConfirmFollow)

    async def test_a_house_rules_code_is_sent_to_the_right_place(self) -> None:
        st = store()
        st.by_code = AsyncMock(return_value=offer("house_rules"))
        it = interaction(7, bot_with(st))
        await shared_packs.look_up(it, campaign(), "code")
        self.assertEqual(it.sent[0][0], shared_packs.NOT_A_PACK_CODE)

    async def test_yes_follows_and_tells_the_running_session(self) -> None:
        st = store()
        st.follow_pack = AsyncMock(return_value=follow())
        bot = bot_with(st)
        view = shared_packs.ConfirmFollow(campaign(), offer())
        it = interaction(7, bot)
        await view._yes(it)
        st.follow_pack.assert_awaited_once()
        bot.homebrew_changed.assert_called_once_with(1, "c1")
        self.assertIn("Now following **Frost**", it.sent[0][0])

    async def test_a_player_pressing_yes_changes_nothing(self) -> None:
        st = store()
        view = shared_packs.ConfirmFollow(campaign(), offer())
        await view._yes(interaction(99, bot_with(st)))
        st.follow_pack.assert_not_called()

    async def test_the_list_leaves_out_what_is_already_followed(self) -> None:
        st = store()
        st.packs_here = AsyncMock(return_value=[offer()])
        menu = shared_packs.SharedPacksMenu(
            campaign(), sharing=False, follows=[follow(set_id="s" * 32)]
        )
        it = interaction(7, bot_with(st))
        await menu._follow(it)
        view = it.sent[0][1]
        self.assertIsNone(view.pick)  # the only pack here is already followed

    async def test_moving_and_unfollowing_tell_the_session_and_undo_restores(self) -> None:
        st = store()
        one, two = follow("One", "1" * 32), follow("Two", "2" * 32)
        st.move_pack = AsyncMock(return_value=[two, one])
        st.published_pack = AsyncMock(return_value=None)
        st.followed_packs = AsyncMock(return_value=[two, one])
        st.unfollow_pack = AsyncMock(return_value=(one, 1))
        st.restore_pack = AsyncMock()
        bot = bot_with(st)
        actions = shared_packs.PackActions(campaign(), one)
        it = interaction(7, bot)
        it.edit_original_response = AsyncMock()
        it.response.edit_message = AsyncMock()
        await actions._earlier(it)
        st.move_pack.assert_awaited_once_with(1, "c1", 7, one.set_id, -1)
        bot.homebrew_changed.assert_called_once()
        it2 = interaction(7, bot)
        await actions._unfollow(it2)
        self.assertEqual(bot.homebrew_changed.call_count, 2)
        undo = it2.sent[0][1]
        await undo._undo(interaction(7, bot))
        st.restore_pack.assert_awaited_once_with(1, "c1", 7, one, 1)
        self.assertEqual(bot.homebrew_changed.call_count, 3)

    async def test_a_player_cannot_move_a_pack(self) -> None:
        st = store()
        actions = shared_packs.PackActions(campaign(), follow())
        await actions._earlier(interaction(99, bot_with(st)))
        st.move_pack.assert_not_called()

    async def test_the_menu_lists_the_order_and_stays_within_the_limit(self) -> None:
        st = store()
        st.published_pack = AsyncMock(return_value=None)
        st.followed_packs = AsyncMock(
            return_value=[follow("One", "1" * 32), follow("Two", "2" * 32)]
        )
        it = interaction(7, bot_with(st))
        await shared_packs.open_menu(it, campaign())
        text, menu = it.sent[0]
        self.assertIn("1. **One**", text)
        self.assertIn("2. **Two**", text)
        self.assertLessEqual(len(menu.children), 5)


if __name__ == "__main__":
    unittest.main()


class Guards(unittest.IsolatedAsyncioTestCase):
    async def test_publishing_and_codes_need_the_paid_plan(self) -> None:
        st = store()
        bot = bot_with(st, refusal="Sharing needs a paid plan.")
        it = interaction(7, bot)
        await shared_packs.publish_changes(it, campaign())
        await shared_packs.new_code(interaction(7, bot), campaign())
        st.publish_pack_changes.assert_not_called()
        st.make_pack_code.assert_not_called()
        self.assertEqual(it.sent[0][0], "Sharing needs a paid plan.")

    async def test_the_select_callbacks_change_nothing_for_a_player(self) -> None:
        st = store()
        st.packs_here = AsyncMock(return_value=[offer()])
        st.followed_packs = AsyncMock(return_value=[follow()])
        menu = shared_packs.SharedPacksMenu(campaign(), sharing=False, follows=[follow()])
        assert menu.pick is not None
        menu.pick._values = [follow().set_id]
        await menu._pick(interaction(99, bot_with(st)))
        choose = shared_packs.FollowChoose(campaign(), [offer()])
        assert choose.pick is not None
        choose.pick._values = [offer().set_id]
        it = interaction(99, bot_with(st))
        await choose._picked(it)
        st.followed_packs.assert_not_called()
        self.assertIn("Only this campaign's DMs", it.sent[0][0])

    async def test_undo_and_stop_are_for_dms_only(self) -> None:
        st = store()
        bot = bot_with(st)
        await shared_packs.UndoUnfollow(campaign(), follow(), 0)._undo(interaction(99, bot))
        await shared_packs.ConfirmStop(campaign())._yes(interaction(99, bot))
        st.restore_pack.assert_not_called()
        st.stop_sharing_pack.assert_not_called()

    async def test_a_player_pressing_share_is_told_before_the_form(self) -> None:
        st = store()
        bot = bot_with(st)
        menu = shared_packs.SharedPacksMenu(campaign(), sharing=False, follows=[])
        it = interaction(99, bot)
        await menu._share(it)
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        bot.plan_gate.assert_not_called()


class CardWords(unittest.IsolatedAsyncioTestCase):
    """The owner's wording for the card (#1615): bold labels as Discord writes bold, no HTML,
    and no D&D Beyond line (packs hold only the game masters' own homebrew)."""

    def test_the_intro_and_the_two_bold_labels(self) -> None:
        from dmbot.ui import shared_packs as sp

        self.assertIn("A Homebrew pack is a named bundle of your own homebrew", sp.INTRO)
        self.assertIn("⚙️ Settings → 📚 Homebrew", sp.INTRO)
        self.assertIn(
            "Only homebrew is shared in a pack, never your story, names or notes.", sp.INTRO
        )
        self.assertNotIn("D&D Beyond", sp.INTRO)
        self.assertNotIn("<b>", sp.INTRO)

    async def test_the_card_shows_the_bold_labels_for_sharing_and_following(self) -> None:
        st = store()
        st.published_pack = AsyncMock(return_value=None)
        st.followed_packs = AsyncMock(return_value=[])
        it = interaction(7, bot_with(st))
        text, _ = await shared_packs._menu(it, campaign(), st)
        self.assertIn("• **Sharing Status:** Not sharing your homebrew.", text)
        self.assertIn("• **Following:** Not following any pack.", text)
        self.assertNotIn("<b>", text)
        self.assertNotIn("D&D Beyond", text)
        st.followed_packs = AsyncMock(return_value=[follow("Frost")])
        text, _ = await shared_packs._menu(it, campaign(), st)
        self.assertIn("• **Following:** the top one wins if two packs use the same name.", text)
        self.assertIn("1. **Frost** from Red Table", text)
