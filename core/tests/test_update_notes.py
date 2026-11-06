"""One DM-screen note for everything that changed at a session's start (#1538): one update keeps the
note it always had; two or more share a numbered note with a button row each; a fifth and later are
named and left to Settings; "no longer updated" lines join; and the buttons are the same
restart-proof ones, each still tied to its own version. Fake stores: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.dm_screen import pack_update, set_update, shared_update, update_notes
from dmbot.rules import pack_diff, rule_sets
from dmbot.rules.pack_updates import PackTaken, PackUpdate, PackUpdateStore
from tests.test_pack_update_ui import NEW, OLD
from tests.test_pack_update_ui import follow as pack_follow
from tests.test_set_update_ui import update as set_update_of

CAMPAIGN = "c" * 32


def campaign() -> Any:
    return SimpleNamespace(guild_id=1, id=CAMPAIGN, dm_user_ids=[7])


def pack_update_of(n: int) -> PackUpdate:
    set_id = f"{n:x}" * 32
    follow = rule_sets.Follow(
        set_id, "homebrew", f"Pack {n}", "Red Table", 1, OLD, True, n, "CODE", None, None
    )
    offer = rule_sets.Offer(set_id, "homebrew", f"Pack {n}", "Red Table", 2 + n, NEW, "CODE")
    return PackUpdate(follow, offer, tuple(pack_diff.diff(OLD, NEW)))


def bot_with(
    *, house: bool = False, packs: int = 0, gone_packs: list[Any] | None = None,
    house_gone: bool = False,
) -> Any:  # fmt: skip
    st = MagicMock(spec=PackUpdateStore)
    st.pending_update = AsyncMock(return_value=set_update_of(2) if house else None)
    st.is_gone = AsyncMock(return_value=house_gone)
    st.stop_updating = AsyncMock(return_value=1)
    st.following = AsyncMock(return_value=[])
    st.take_update = AsyncMock()
    st.sent_answers = AsyncMock(return_value=[])
    st.unnoted_suggestions = AsyncMock(return_value=[])
    st.pending_pack_updates = AsyncMock(
        return_value=[pack_update_of(n) for n in range(1, packs + 1)]
    )
    st.take_pack_update = AsyncMock(return_value=PackTaken(False, pack_follow(1), 0, 2, offered=2))
    st.gone_packs = AsyncMock(return_value=gone_packs or [])
    st.stop_updating_pack = AsyncMock(return_value=True)
    return SimpleNamespace(
        rule_sets=st,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        homebrew=SimpleNamespace(list=AsyncMock(return_value=[])),
        homebrew_changed=MagicMock(),
        house_rules=SimpleNamespace(list=AsyncMock(return_value=[])),
        post_message=AsyncMock(return_value=object()),
        post=AsyncMock(return_value=object()),
    )


def labels(view: Any) -> list[tuple[int | None, str]]:
    return [(i.item.row, i.item.label) for i in view.children]


class Combined(unittest.IsolatedAsyncioTestCase):
    async def test_one_update_keeps_the_note_it_always_had(self) -> None:
        for bot, words in ((bot_with(house=True), "house rules updated to v4"),
                           (bot_with(packs=1), "homebrew updated to v3")):  # fmt: skip
            await update_notes.check_all(bot, campaign(), 99)
            bot.post_message.assert_awaited_once()
            text, view = bot.post_message.await_args.args[1:]
            self.assertIn(words, text)
            self.assertNotIn("Shared rules updated", text)
            self.assertEqual(len(view.children), 3)
            self.assertTrue(all(i.item.row is None for i in view.children))

    async def test_nothing_new_posts_nothing(self) -> None:
        bot = bot_with()
        await update_notes.check_all(bot, campaign(), 99)
        bot.post_message.assert_not_called()
        bot.post.assert_not_called()

    async def test_two_updates_share_one_note_with_a_row_each(self) -> None:
        bot = bot_with(house=True, packs=1)
        await update_notes.check_all(bot, campaign(), 99)
        bot.post_message.assert_awaited_once()  # one message, not two
        text, view = bot.post_message.await_args.args[1:]
        self.assertTrue(text.startswith(shared_update.HEADER))
        self.assertIn("1. **Red Table** house rules, now v4: 2 rules", text)
        self.assertIn("2. **Pack 1** homebrew, now v3: 2 entries changed", text)
        self.assertEqual(
            labels(view),
            [(0, "See 1"), (0, "Pick 1"), (0, "Undo 1"),
             (1, "See 2"), (1, "Pick 2"), (1, "Undo 2")],
        )  # fmt: skip

    async def test_each_row_keeps_its_own_pack_and_version(self) -> None:
        bot = bot_with(packs=2)
        await update_notes.check_all(bot, campaign(), 99)
        view = bot.post_message.await_args.args[2]
        ids = [i.item.custom_id for i in view.children]
        self.assertEqual(len(ids), 6)
        self.assertTrue(all(len(i) <= 100 for i in ids))
        self.assertIn(f"dmbot:packupd:undo:{CAMPAIGN}:{'1' * 32}:3", ids)
        self.assertIn(f"dmbot:packupd:undo:{CAMPAIGN}:{'2' * 32}:4", ids)  # not the first's version

    async def test_five_updates_give_four_rows_and_name_the_rest(self) -> None:
        bot = bot_with(house=True, packs=4)
        bot.rule_sets.pending_pack_updates = AsyncMock(
            return_value=[pack_update_of(n) for n in range(1, 5)]
        )
        await update_notes.check_all(bot, campaign(), 99)
        text, view = bot.post_message.await_args.args[1:]
        self.assertIn("4. **Pack 3**", text)
        self.assertIn("5. **Pack 4**", text)
        self.assertIn("**Pack 4** homebrew, now v6", text)  # named, though it has no buttons
        self.assertIn("1 without buttons here: open ⚙️ Settings → 📚 Shared rules.", text)
        self.assertEqual({i.item.row for i in view.children}, {0, 1, 2, 3})
        self.assertEqual(bot.rule_sets.take_pack_update.await_count, 4)  # all were still taken

    async def test_six_updates_say_how_many_more(self) -> None:
        bot = bot_with(house=True, packs=5)
        await update_notes.check_all(bot, campaign(), 99)
        text = bot.post_message.await_args.args[1]
        self.assertIn("2 without buttons here: open ⚙️ Settings → 📚 Shared rules.", text)
        self.assertIn("6. **Pack 5**", text)

    async def test_gone_lines_join_the_note_as_plain_lines(self) -> None:
        bot = bot_with(packs=2, gone_packs=[pack_follow(1)])
        await update_notes.check_all(bot, campaign(), 99)
        bot.post.assert_not_called()
        text = bot.post_message.await_args.args[1]
        self.assertIn("isn't being shared any more", text)
        self.assertNotIn("1. **Frost** isn't", text)  # a plain line, not a numbered update

    async def test_one_update_with_a_gone_line_is_a_combined_note(self) -> None:
        bot = bot_with(packs=1, gone_packs=[pack_follow(1)])
        await update_notes.check_all(bot, campaign(), 99)
        bot.post_message.assert_awaited_once()
        text = bot.post_message.await_args.args[1]
        self.assertTrue(text.startswith(shared_update.HEADER))
        self.assertIn("isn't being shared any more", text)

    async def test_only_gone_lines_are_one_plain_post(self) -> None:
        bot = bot_with(gone_packs=[pack_follow(1), pack_follow(1)])
        await update_notes.check_all(bot, campaign(), 99)
        bot.post_message.assert_not_called()
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[1].count("isn't being shared any more"), 2)

    async def test_a_failing_pack_check_still_posts_the_sets_note(self) -> None:
        bot = bot_with(house=True)
        bot.rule_sets.pending_pack_updates = AsyncMock(side_effect=RuntimeError("down"))
        with self.assertLogs("dmbot.dm_screen.pack_update", level="ERROR"):
            await update_notes.check_all(bot, campaign(), 99)
        self.assertIn("house rules updated to v4", bot.post_message.await_args.args[1])

    async def test_a_note_that_cannot_post_is_logged_and_never_raises(self) -> None:
        bot = bot_with(house=True, packs=1)
        bot.post_message = AsyncMock(return_value=None)
        with self.assertLogs("dmbot.dm_screen.shared_update", level="WARNING") as logged:
            await update_notes.check_all(bot, campaign(), 99)
        self.assertIn("without a note", logged.output[0])
        bot.post_message = AsyncMock(side_effect=RuntimeError("down"))
        with self.assertLogs("dmbot.dm_screen.update_notes", level="ERROR"):
            await update_notes.check_all(bot, campaign(), 99)

    async def test_a_long_note_drops_extras_first_and_keeps_every_line(self) -> None:
        found = shared_update.Collected(gone=[f"gone line {n} " + "x" * 300 for n in range(3)])
        for n in range(4):
            found.updates.append(
                shared_update.Update(
                    f"update {n}", "a", lambda: MagicMock(), lambda r, t: [], ["b" * 600]
                )
            )
        text = shared_update.combined_text(found)
        self.assertLessEqual(len(text), shared_update.TEXT_MAX)
        for n in range(4):
            self.assertIn(f"{n + 1}. update {n}", text)
        for n in range(3):
            self.assertIn(f"gone line {n}", text)  # said only once, so never cut

    def test_the_compare_line_names_its_number(self) -> None:
        found = shared_update.Collected()
        for _ in range(2):
            found.updates.append(
                shared_update.Update("u", "a", lambda: MagicMock(), lambda r, t: [], ["Press {n}"])
            )
        text = shared_update.combined_text(found)
        self.assertIn("   Press 1", text)
        self.assertIn("   Press 2", text)

    def test_combined_labels_are_short_and_keep_their_number_in_view(self) -> None:
        for label in ("See changes", "Pick changes", "Compare", "Undo"):
            shown = shared_update.numbered(label, "3")
            self.assertTrue(shown.endswith(" 3"))
            self.assertLessEqual(len(shown), 10)
        self.assertEqual(shared_update.numbered("See changes", ""), "See changes")

    async def test_a_check_that_raises_does_not_lose_the_others_note(self) -> None:
        bot = bot_with(house=True, packs=1)
        with (
            patch.object(pack_update, "collect", AsyncMock(side_effect=RuntimeError("x"))),
            self.assertLogs("dmbot.dm_screen.update_notes", level="ERROR"),
        ):
            await update_notes.check_all(bot, campaign(), 99)
        self.assertIn("house rules updated to v4", bot.post_message.await_args.args[1])
        bot = bot_with(house=True, packs=1)
        with (
            patch.object(set_update, "collect", AsyncMock(side_effect=RuntimeError("x"))),
            self.assertLogs("dmbot.dm_screen.update_notes", level="ERROR"),
        ):
            await update_notes.check_all(bot, campaign(), 99)
        self.assertIn("homebrew updated to v3", bot.post_message.await_args.args[1])

    async def test_a_pack_whose_extras_cannot_be_worked_out_still_gets_its_buttons(self) -> None:
        bot = bot_with(packs=2)
        bot.homebrew = SimpleNamespace(list=AsyncMock(side_effect=RuntimeError("x")))
        await update_notes.check_all(bot, campaign(), 99)
        self.assertEqual(len(bot.post_message.await_args.args[2].children), 6)

    async def test_a_set_that_went_away_is_said_once_in_the_note(self) -> None:
        bot = bot_with(packs=1, house_gone=True)
        await update_notes.check_all(bot, campaign(), 99)
        text = bot.post_message.await_args.args[1]
        self.assertIn("A set your table follows isn't being shared any more", text)

    async def test_suggestion_answers_run_once_for_each_kind(self) -> None:
        bot = bot_with(house=True, packs=1)
        await update_notes.check_all(bot, campaign(), 99)
        bot.rule_sets.sent_answers.assert_awaited_once()
        bot.rule_sets.unnoted_suggestions.assert_awaited_once()

    async def test_a_failed_post_is_tried_once_more(self) -> None:
        bot = bot_with(house=True, packs=1)
        bot.post_message = AsyncMock(side_effect=[None, object()])
        await update_notes.check_all(bot, campaign(), 99)
        self.assertEqual(bot.post_message.await_count, 2)

    def test_the_single_notes_are_unchanged(self) -> None:
        self.assertEqual(
            pack_update.note_text("Frost", 5, 1),
            "📦 **Frost** homebrew updated to v5: 1 entry changed. DMbot took the new version.",
        )
        self.assertIn("house rules updated to v4: 1 rule.", set_update.note_text("Red", 4, 1))


if __name__ == "__main__":
    unittest.main()
