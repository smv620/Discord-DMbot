"""The DM-screen side of pack updates (#1527 part C): the note, who may press its buttons, Undo
and Pick changes, and the session-start check that takes a new version and posts one note.
Fake stores and a fake interaction: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import pack_update
from dmbot.rules import pack_diff, rule_sets
from dmbot.rules.pack_updates import PackTaken, PackUpdate, PackUpdateStore
from tests.test_shared_rules_ui import campaign, interaction

SET = "a" * 32
OLD = {
    "entries": [{"kind": "item", "name": "Rope", "summary": None, "text": "Long.", "known": False}]
}
NEW = {
    "entries": [
        {"kind": "item", "name": "Rope", "summary": None, "text": "Very long.", "known": False},
        {"kind": "item", "name": "Torch", "summary": None, "text": "Burns.", "known": True},
    ]
}


def follow(version: int = 1, previous: int | None = None) -> rule_sets.Follow:
    return rule_sets.Follow(
        SET, "homebrew", "Frost", "Red Table", version, OLD if version == 1 else NEW, True, 1,
        "CODE", previous, OLD if previous else None,
    )  # fmt: skip


def update() -> PackUpdate:
    offer = rule_sets.Offer(SET, "homebrew", "Frost", "Red Table", 2, NEW, "CODE")
    return PackUpdate(follow(), offer, tuple(pack_diff.diff(OLD, NEW)))


def store() -> Any:
    return MagicMock(spec=PackUpdateStore)


def bot_with(st: Any, *, mine: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        rule_sets=st,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        homebrew=SimpleNamespace(list=AsyncMock(return_value=mine or [])),
        homebrew_changed=MagicMock(),
        post_message=AsyncMock(return_value=object()),
        post=AsyncMock(),
    )


def interaction_in_guild(user: int, bot: Any) -> Any:
    it = interaction(user, bot)
    it.guild = SimpleNamespace(id=1)
    return it


class Note(unittest.TestCase):
    def test_the_note_says_what_happened_in_plain_words(self) -> None:
        text = pack_update.note_text("Frost", 5, 2)
        self.assertIn(
            "**Frost** homebrew updated to v5: 2 entries changed. DMbot took the new version.", text
        )
        self.assertIn("1 entry", pack_update.note_text("Frost", 5, 1))
        self.assertIn("same name as Rope", pack_update.note_text("Frost", 5, 1, ["Rope"]))

    def test_a_note_with_no_changes_says_so(self) -> None:
        self.assertIn("no entry is different", pack_update.note_text("Frost", 2, 0))

    def test_the_buttons_fit_discords_custom_id_limit_and_round_trip(self) -> None:
        view = pack_update.note_view("c" * 32, SET, 999_999_999)
        self.assertEqual(len(view.children), 3)
        for item in view.children:
            self.assertLessEqual(len(cast(Any, item).custom_id or ""), 100)

    def test_changes_are_listed_by_name(self) -> None:
        text = pack_update.changes_text("Frost", 2, update().changes)
        self.assertIn("Changed: Rope", text)
        self.assertIn("New: Torch", text)


class Presses(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_can_press_nothing(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(2, 1)])
        for action in ("see", "pick", "undo"):
            it = interaction_in_guild(99, bot_with(st))
            await pack_update.press(it, "c1", SET, action, 2)
            self.assertIn("Only this campaign's DMs", it.sent[0][0])
        st.undo_last_pack_take.assert_not_called()

    async def test_undo_goes_back_and_tells_the_session(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(2, 1)])
        st.undo_last_pack_take = AsyncMock()
        bot = bot_with(st)
        it = interaction_in_guild(7, bot)
        await pack_update.press(it, "c1", SET, "undo", 2)
        st.undo_last_pack_take.assert_awaited_once_with(1, "c1", 7, SET, 2)
        bot.homebrew_changed.assert_called_once_with(1, "c1")
        self.assertIn("Back on v1", it.sent[0][0])

    async def test_an_old_button_cannot_undo_a_newer_update(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(3, 2)])
        it = interaction_in_guild(7, bot_with(st))
        await pack_update.press(it, "c1", SET, "undo", 2)
        self.assertEqual(it.sent[0][0], pack_update.NEWER)
        st.undo_last_pack_take.assert_not_called()

    async def test_see_lists_what_the_update_changed(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(2, 1)])
        it = interaction_in_guild(7, bot_with(st))
        await pack_update.press(it, "c1", SET, "see", 2)
        self.assertIn("New: Torch", it.sent[0][0])

    async def test_pick_undoes_and_opens_the_picker_with_everything_ticked(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(2, 1)])
        st.undo_last_pack_take = AsyncMock()
        st.pending_pack_update = AsyncMock(return_value=update())
        it = interaction_in_guild(7, bot_with(st))
        await pack_update.press(it, "c1", SET, "pick", 2)
        text, view = it.sent[0]
        self.assertIn("Ticked = take this change", text)
        self.assertEqual(view.accepted, {"rope", "torch"})

    async def test_saving_with_something_unticked_asks_the_store_to_merge(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(1)])
        st.take_pack_update = AsyncMock(
            return_value=PackTaken(True, follow(1), 0, 1, (5,), offered=2)
        )
        bot = bot_with(st)
        view = pack_update.PickView(campaign(), update())
        view.accepted.discard("rope")
        it = interaction_in_guild(7, bot)
        await view._apply(it)
        args = st.take_pack_update.await_args
        self.assertEqual(args.args[-1], {"torch"})
        self.assertEqual(args.kwargs["expected_version"], 2)  # the version the DM was shown
        self.assertIn("your own homebrew", it.sent[0][0])
        bot.homebrew_changed.assert_called_once()

    async def test_a_player_cannot_save_picks_or_undo_a_merge(self) -> None:
        st = store()
        st.followed_packs = AsyncMock(return_value=[follow(1)])
        await pack_update.PickView(campaign(), update())._apply(
            interaction_in_guild(99, bot_with(st))
        )
        await pack_update.UndoView(campaign(), PackTaken(False, follow(1), 0, 2))._undo(
            interaction_in_guild(99, bot_with(st))
        )
        st.take_pack_update.assert_not_called()
        st.undo_pack_take.assert_not_called()


def campaign32() -> Any:
    return SimpleNamespace(guild_id=1, id="c" * 32, dm_user_ids=[7])


class SessionStart(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_version_is_taken_and_one_note_is_posted(self) -> None:
        st = store()
        st.pending_pack_updates = AsyncMock(return_value=[update()])
        st.take_pack_update = AsyncMock(return_value=PackTaken(False, follow(1), 0, 2, offered=2))
        st.gone_packs = AsyncMock(return_value=[])
        own = SimpleNamespace(name="Rope")
        bot = bot_with(st, mine=[own])
        await pack_update.check_updates(bot, campaign32(), 55)
        self.assertIsNone(st.take_pack_update.await_args.args[2])  # DMbot, not a person
        text = bot.post_message.await_args.args[1]
        self.assertIn("updated to v2: 2 entries changed", text)
        self.assertIn("same name as Rope", text)  # it has a Rope of its own
        bot.homebrew_changed.assert_called_once()

    async def test_nothing_new_posts_nothing(self) -> None:
        st = store()
        st.pending_pack_updates = AsyncMock(return_value=[])
        st.gone_packs = AsyncMock(return_value=[])
        bot = bot_with(st)
        await pack_update.check_updates(bot, campaign32(), 55)
        bot.post_message.assert_not_called()

    async def test_a_pack_that_went_away_is_said_once(self) -> None:
        st = store()
        st.pending_pack_updates = AsyncMock(return_value=[])
        st.gone_packs = AsyncMock(return_value=[follow(1)])
        st.stop_updating_pack = AsyncMock(side_effect=[True, False])
        bot = bot_with(st)
        await pack_update.check_updates(bot, campaign32(), 55)
        await pack_update.check_updates(bot, campaign32(), 55)
        self.assertEqual(bot.post.await_count, 1)
        self.assertIn("isn't being shared any more", bot.post.await_args.args[1])

    async def test_a_failure_never_raises(self) -> None:
        st = store()
        st.pending_pack_updates = AsyncMock(side_effect=RuntimeError("down"))
        await pack_update.check_updates(bot_with(st), campaign32(), 55)

    async def test_a_bot_without_the_store_does_nothing(self) -> None:
        await pack_update.check_updates(SimpleNamespace(rule_sets=None), campaign32(), 55)


if __name__ == "__main__":
    unittest.main()
