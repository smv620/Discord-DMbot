"""Suggest to <pack> on the DM screen and in the homebrew menu (#1537): the publisher's note (no
sender), who may press its buttons, Add to pack and No thanks, the sender's outcome line, and the
💡 button on an entry. Fakes: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.dm_screen import pack_suggestion as ps
from dmbot.rules.homebrew import Homebrew
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_suggestions import PackSent, PackSuggestion, PackSuggestionStore
from dmbot.ui import homebrew as ui

CID, SID = "ab" * 16, "cd" * 16


def suggestion(status: str = "open", known: bool = False) -> PackSuggestion:
    return PackSuggestion(SID, "s" * 32, "monster", "Hollow King", None, "A crowned skeleton.",
                          known, status, 1)  # fmt: skip


def campaign(dms: tuple[int, ...] = (7,)) -> Any:
    return SimpleNamespace(guild_id=1, id=CID, dm_user_ids=set(dms), dm_screen_channel_id=55)


def store(**kw: Any) -> Any:
    st = MagicMock(spec=PackSuggestionStore)
    for name, value in kw.items():
        setattr(st, name, value)
    return st


def bot_with(st: Any, *, refusal: str | None = None, mine: Any = None) -> Any:
    return SimpleNamespace(
        rule_sets=st,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        plan_gate=AsyncMock(return_value=refusal),
        homebrew=SimpleNamespace(find=AsyncMock(return_value=mine)),
        homebrew_changed=MagicMock(),
        post=AsyncMock(return_value=True),
        post_message=AsyncMock(return_value=object()),
        track=lambda coro, name: coro.close(),
    )


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
    it.guild = SimpleNamespace(id=1)
    it.response.is_done = lambda: done["v"]
    it.response.defer = defer
    it.response.send_message = send
    it.followup.send = send
    it.message = SimpleNamespace(content="the note", edit=AsyncMock())
    it.sent = sent
    return it


class Words(unittest.TestCase):
    def test_the_note_names_the_pack_and_the_entry_but_nobody_else(self) -> None:
        text = ps.note_text("Frost Pack", suggestion())
        self.assertIn("A table using **Frost Pack homebrew** suggests (sender not shown)", text)
        self.assertIn("**Hollow King** (Monster): A crowned skeleton.", text)
        self.assertIn("It would be a new entry.", text)
        self.assertIn("It would replace your entry", ps.note_text("Frost Pack", suggestion(), True))
        self.assertNotIn("hidden", text)  # (nothing is hidden from players any more)

    def test_names_cannot_format_or_ping(self) -> None:
        text = ps.note_text("**Evil** @everyone", suggestion())
        self.assertNotIn("**Evil**", text)

    def test_the_outcome_lines(self) -> None:
        def line(status: str, version: int | None = None) -> str:
            return ps.answer_line(PackSent("i", "Frost Pack", "Ember Lamp", status, version))

        self.assertIn("added your suggestion (now version 3)", line("accepted", 3))
        self.assertIn("already had that entry", line("present"))
        self.assertIn("isn't being shared any more", line("gone"))
        self.assertIn("can send it again", line("expired"))
        self.assertIn("said no thanks", line("declined"))

    def test_the_buttons_fit_discords_custom_id_limit_and_match_their_template(self) -> None:
        view = ps.note_view(CID, SID)
        items: list[Any] = list(view.children)
        self.assertEqual([i.item.label for i in items], ["See", "Add to pack", "No thanks"])
        for item in items:
            self.assertLessEqual(len(item.item.custom_id), 100)
            template = item.__class__.__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))


class Presses(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_can_press_nothing(self) -> None:
        st = store(pack_suggestion=AsyncMock(return_value=suggestion()))
        for action in ("see", "accept", "no"):
            it = interaction(99, bot_with(st))
            await ps.press(it, CID, action, SID)
            self.assertEqual(it.sent[0][0], ps.ONLY_DMS)
        st.accept_pack_suggestion.assert_not_called()
        st.decline_pack_suggestion.assert_not_called()

    async def test_add_to_pack_checks_the_plan_then_adds(self) -> None:
        st = store(
            pack_suggestion=AsyncMock(return_value=suggestion()),
            accept_pack_suggestion=AsyncMock(return_value=("Frost Pack", 2)),
        )
        bot = bot_with(st)
        it = interaction(7, bot)
        await ps.press(it, CID, "accept", SID)
        bot.plan_gate.assert_awaited_once()
        self.assertEqual(bot.plan_gate.await_args.args[0], "share")
        self.assertTrue(st.accept_pack_suggestion.await_args.kwargs["plan_ok"])
        bot.homebrew_changed.assert_called_once_with(1, CID)  # a running session reads it
        self.assertIn("Tables using it get it at their next session start", it.sent[-1][0])

    async def test_a_lapsed_plan_adds_nothing(self) -> None:
        st = store(pack_suggestion=AsyncMock(return_value=suggestion()))
        bot = bot_with(st, refusal="Sharing needs a paid plan.")
        it = interaction(7, bot)
        await ps.press(it, CID, "accept", SID)
        self.assertEqual(it.sent[0][0], "Sharing needs a paid plan.")
        st.accept_pack_suggestion.assert_not_called()

    async def test_a_store_problem_is_told_in_its_words(self) -> None:
        st = store(
            pack_suggestion=AsyncMock(return_value=suggestion()),
            accept_pack_suggestion=AsyncMock(side_effect=HouseRuleError("Nope.")),
        )
        it = interaction(7, bot_with(st))
        await ps.press(it, CID, "accept", SID)
        self.assertEqual(it.sent[0][0], "Nope.")

    async def test_no_thanks_closes_the_note(self) -> None:
        st = store(
            pack_suggestion=AsyncMock(return_value=suggestion()),
            decline_pack_suggestion=AsyncMock(return_value=True),
        )
        it = interaction(7, bot_with(st))
        await ps.press(it, CID, "no", SID)
        edited = it.message.edit.await_args.kwargs
        self.assertIn("No thanks. Nothing changed.", edited["content"])
        self.assertIsNone(edited["view"])

    async def test_answered_and_old_suggestions_say_so(self) -> None:
        for status, words in (("accepted", ps.ANSWERED), ("expired", ps.EXPIRED)):
            st = store(pack_suggestion=AsyncMock(return_value=suggestion(status)))
            it = interaction(7, bot_with(st))
            await ps.press(it, CID, "accept", SID)
            self.assertEqual(it.sent[0][0], words)
        st = store(pack_suggestion=AsyncMock(return_value=None))
        it = interaction(7, bot_with(st))
        await ps.press(it, CID, "see", SID)
        self.assertEqual(it.sent[0][0], ps.ANSWERED)

    async def test_see_says_whether_it_replaces_an_entry_of_the_campaigns_own(self) -> None:
        st = store(pack_suggestion=AsyncMock(return_value=suggestion()))
        new = interaction(7, bot_with(st))
        await ps.press(new, CID, "see", SID)
        self.assertIn("this new entry goes into your homebrew and the pack", new.sent[0][0])
        own = interaction(7, bot_with(st, mine=SimpleNamespace(name="hollow king")))
        await ps.press(own, CID, "see", SID)
        self.assertIn("your entry **hollow king** is replaced by", own.sent[0][0])


class Notes(unittest.IsolatedAsyncioTestCase):
    async def test_a_note_is_posted_to_the_publishers_screen_and_marked(self) -> None:
        st = store(mark_pack_noted=AsyncMock())
        bot = bot_with(st)
        self.assertTrue(await ps.post_note(bot, 1, CID, suggestion(), "Frost Pack"))
        self.assertEqual(bot.post_message.await_args.args[0], 55)
        st.mark_pack_noted.assert_awaited_once_with(1, CID, SID)

    async def test_a_failed_post_is_not_marked_so_it_is_tried_again(self) -> None:
        st = store(mark_pack_noted=AsyncMock())
        bot = bot_with(st)
        bot.post_message = AsyncMock(return_value=None)
        self.assertFalse(await ps.post_note(bot, 1, CID, suggestion(), "Frost Pack"))
        st.mark_pack_noted.assert_not_called()

    async def test_waiting_notes_are_posted_at_a_session_start(self) -> None:
        st = store(
            unnoted_pack_suggestions=AsyncMock(return_value=[(suggestion(), "Frost Pack")]),
            mark_pack_noted=AsyncMock(),
        )
        bot = bot_with(st)
        await ps.post_waiting(bot, campaign())
        st.mark_pack_noted.assert_awaited_once()

    async def test_answers_are_told_once(self) -> None:
        sent = [
            PackSent("a", "Frost Pack", "Ember Lamp", "accepted", 2),
            PackSent("b", "Frost Pack", "Rope", "open", None),
        ]
        st = store(pack_sent_answers=AsyncMock(return_value=sent), mark_pack_told=AsyncMock())
        bot = bot_with(st)
        await ps.tell_answers(bot, campaign(), 77)
        self.assertIn("added your suggestion", bot.post.await_args.args[1])
        st.mark_pack_told.assert_awaited_once_with(1, CID, ["a"])  # the waiting one stays

    async def test_a_failure_never_raises(self) -> None:
        st = store(pack_sent_answers=AsyncMock(side_effect=RuntimeError("down")))
        await ps.tell_answers(bot_with(st), campaign(), 77)
        st = store(unnoted_pack_suggestions=AsyncMock(side_effect=RuntimeError("down")))
        await ps.post_waiting(bot_with(st), campaign())


def entry(book: bool = False) -> Homebrew:
    return Homebrew(
        id=5, campaign_id=CID, kind="item", name="Ember Lamp", text="Glows.", summary=None,
        created_by=7, created_at=1, updated_at=1, version=3,
        from_book=book,
    )  # fmt: skip


class OnAnEntry(unittest.IsolatedAsyncioTestCase):
    def menu(self, packs: list[tuple[str, str]], book: bool = False) -> ui.EntryMenu:
        return ui.EntryMenu(campaign(), entry(book), 0, packs)

    def test_the_button_is_only_there_when_the_campaign_follows_a_pack(self) -> None:
        def labels(menu: ui.EntryMenu) -> list[str]:
            return [str(getattr(c, "label", "")) for c in menu.children]

        self.assertNotIn(ui.SUGGEST_LABEL, labels(self.menu([])))
        self.assertIn(ui.SUGGEST_LABEL, labels(self.menu([("a" * 32, "Frost Pack")])))

    async def test_a_book_entry_says_it_stays_home(self) -> None:
        menu = self.menu([("a" * 32, "Frost Pack")], book=True)
        bot = bot_with(store())
        bot.homebrew_removed = {}
        it = interaction(7, bot)
        with patch.object(ui, "_is_dm", AsyncMock(return_value=True)):
            await menu._suggest(it)
        self.assertEqual(it.sent[0][0], ui.FROM_A_BOOK)

    async def test_one_pack_asks_once_then_sends_with_nobody_named(self) -> None:
        menu = self.menu([("a" * 32, "Frost Pack")])
        st = store(suggest_entry=AsyncMock(return_value=("sid", 9, "pubcamp", "Frost Pack")))
        bot = bot_with(st)
        it = interaction(7, bot)
        it.response.edit_message = AsyncMock()
        with patch.object(ui, "_is_dm", AsyncMock(return_value=True)):
            await menu._suggest(it)
        shown = it.response.edit_message.await_args.kwargs
        self.assertIn("Send **Ember Lamp** to the **Frost Pack** DM?", shown["content"])
        self.assertIn("not your name, table or server", shown["content"])
        st.suggest_entry.assert_not_called()  # nothing before the confirmation
        confirm = shown["view"]
        yes = interaction(7, bot)
        with (
            patch.object(ui, "_redraw", AsyncMock()) as redraw,
            patch.object(ps, "send_note") as send_note,
        ):
            await confirm._yes(yes)
        assert st.suggest_entry.await_args is not None
        args = st.suggest_entry.await_args.args
        self.assertEqual(args, (1, CID, 7, 5, 3, "a" * 32))  # the version the DM was shown
        send_note.assert_called_once_with(bot, 9, "pubcamp", "sid", "Frost Pack")
        assert redraw.await_args is not None
        self.assertIn("Sent, with no name", redraw.await_args.kwargs["note"])

    async def test_more_than_one_pack_asks_which(self) -> None:
        packs = [("a" * 32, "Frost Pack"), ("b" * 32, "Rope Pack")]
        menu = self.menu(packs)
        it = interaction(7, bot_with(store()))
        it.response.edit_message = AsyncMock()
        with patch.object(ui, "_is_dm", AsyncMock(return_value=True)):
            await menu._suggest(it)
        shown = it.response.edit_message.await_args.kwargs
        self.assertIn("Send **Ember Lamp** to which pack?", shown["content"])
        self.assertEqual(
            [c.label for c in shown["view"].children],
            ["To Frost Pack", "To Rope Pack", ui.KEEP_PRIVATE_LABEL],
        )

    def test_five_followed_packs_all_get_a_button(self) -> None:
        packs = [(f"{n}" * 32, f"Pack {n}") for n in range(5)]
        menu = ui.PickPack(campaign(), entry(), 0, packs)
        labels = [str(getattr(c, "label", "")) for c in menu.children]
        self.assertEqual(labels[:5], [f"To Pack {n}" for n in range(5)])
        self.assertEqual(labels[5], ui.KEEP_PRIVATE_LABEL)

    async def test_a_player_pressing_it_is_refused(self) -> None:
        menu = self.menu([("a" * 32, "Frost Pack")])
        it = interaction(99, bot_with(store()))
        with patch.object(ui, "_is_dm", AsyncMock(return_value=False)):
            await menu._suggest(it)
        self.assertEqual(it.sent[0][0], ui.ONLY_DMS)


if __name__ == "__main__":
    unittest.main()
