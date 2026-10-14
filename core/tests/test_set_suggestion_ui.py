"""Suggest to <set> and [Compare] (#1259): the publisher's note, who may press what, the line
the sender hears, and the buttons on the house rules list. Fake stores, no Discord, no database."""

from __future__ import annotations

import asyncio
import re
import unittest
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import set_suggestion, set_update
from dmbot.rules import rule_sets, set_diff
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.ui import house_rules

CAMPAIGN = "a" * 32
SID = "b" * 32
DM = 7
OPEN = rule_sets.Suggestion(
    SID, "s", "Potions are a bonus action", "Potions take an action", "open", 1
)
BEFORE = {
    "rules": [{"number": 1, "rule": "Flanking gives advantage", "instead": None}],
    "link": None,
}
SET = rule_sets.SharedSet("s", "Red Table", True, 4)


def campaign(dms: tuple[int, ...] = (DM,), screen: int | None = 99) -> Any:
    return SimpleNamespace(
        name="Far Away", guild_id=1, id=CAMPAIGN, dm_user_ids=list(dms), dm_screen_channel_id=screen
    )


def rule(
    number: int, text: str, instead: str | None = None, from_set: str | None = None
) -> HouseRule:
    return HouseRule(number, CAMPAIGN, text, instead, None, None, DM, 1, 1, from_set=from_set)


def store(**kw: Any) -> Any:
    s = MagicMock()
    s.suggestion = AsyncMock(return_value=OPEN)
    s.current_content = AsyncMock(return_value=(SET, BEFORE))
    s.accept_suggestion = AsyncMock(return_value=rule_sets.SharedSet("s", "Red Table", True, 5))
    s.decline_suggestion = AsyncMock(return_value=True)
    s.mark_noted = AsyncMock()
    s.mark_told = AsyncMock()
    s.following = AsyncMock(return_value=[])
    for name, value in kw.items():
        setattr(s, name, value)
    return s


def bot_with(s: Any, *, refusal: str | None = None, camp: Any = None, rules: Any = None) -> Any:
    camp = camp or campaign()
    bot = SimpleNamespace(
        rule_sets=s,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
        plan_gate=AsyncMock(return_value=refusal),
        post=AsyncMock(return_value=True),
        post_message=AsyncMock(return_value=object()),
        house_rules=SimpleNamespace(list=AsyncMock(return_value=rules or [])),
        tasks=[],
    )
    # Background work (the note's own task) runs on the loop; tests await bot.tasks.
    bot.track = lambda work, name: bot.tasks.append(asyncio.ensure_future(work))
    return bot


def interaction(user: int, bot: Any) -> Any:
    sent: list[tuple[str, Any]] = []
    done = {"v": False}

    async def send(text: str, **kw: Any) -> None:
        sent.append((text, kw.get("view")))

    async def defer(**_: Any) -> None:
        done["v"] = True

    it = MagicMock()
    it.user = SimpleNamespace(id=user)
    it.guild = SimpleNamespace(id=1)
    it.client = bot
    it.message = SimpleNamespace(content="the note", edit=AsyncMock())
    it.response.is_done = lambda: done["v"]
    it.response.defer = defer
    it.response.send_message = send
    it.followup.send = send
    it.response.edit_message = AsyncMock()
    it.edit_original_response = AsyncMock()
    it.sent = sent
    return it


class TheNote(unittest.IsolatedAsyncioTestCase):
    async def test_the_note_has_the_words_and_the_set_and_nobody_else(self) -> None:
        text = set_suggestion.note_text("Red Table", OPEN.rule, OPEN.instead)
        self.assertIn("Red Table house rules", text)
        self.assertIn("sender not shown", text)
        self.assertIn("instead of: Potions take an action", text)
        replacing = set_suggestion.note_text("Red Table", "New", "Old rule", True)
        self.assertIn("replaces this set rule: Old rule", replacing)
        self.assertIn("Potions are a bonus action", text)
        self.assertIn("A table using", text)

    async def test_the_buttons_survive_a_restart(self) -> None:
        view = set_suggestion.note_view(CAMPAIGN, SID)
        ids = [c.custom_id for c in view.children]  # type: ignore[attr-defined]
        self.assertEqual(len(ids), 3)
        pattern = re.compile(
            set_suggestion.SuggestionButton.__discord_ui_compiled_template__.pattern
        )
        for custom_id in ids:
            self.assertTrue(pattern.fullmatch(custom_id), custom_id)

    async def test_accept_saves_a_version_after_the_plan_check(self) -> None:
        s = store()
        it = interaction(DM, bot_with(s))
        await set_suggestion.press(it, CAMPAIGN, "accept", SID)
        kwargs = s.accept_suggestion.await_args.kwargs
        self.assertTrue(kwargs["plan_ok"])
        self.assertEqual(kwargs["expected_version"], 4)
        self.assertIn("next session start", it.sent[0][0])
        closed = it.message.edit.await_args.kwargs["content"]
        self.assertIn("version 5", closed)

    async def test_a_refused_plan_a_player_and_an_answered_note_change_nothing(self) -> None:
        s = store()
        it = interaction(DM, bot_with(s, refusal="Sharing house rules needs a paid plan."))
        await set_suggestion.press(it, CAMPAIGN, "accept", SID)
        self.assertEqual(it.sent[0][0], "Sharing house rules needs a paid plan.")
        it2 = interaction(99, bot_with(s))
        await set_suggestion.press(it2, CAMPAIGN, "accept", SID)
        await set_suggestion.press(it2, CAMPAIGN, "no", SID)
        self.assertEqual({t for t, _ in it2.sent}, {set_suggestion.ONLY_DMS})
        done = store(suggestion=AsyncMock(return_value=None))
        it3 = interaction(DM, bot_with(done))
        await set_suggestion.press(it3, CAMPAIGN, "accept", SID)
        self.assertEqual(it3.sent[0][0], set_suggestion.ANSWERED)
        for each in (s, done):
            each.accept_suggestion.assert_not_called()
            each.decline_suggestion.assert_not_called()

    async def test_a_store_refusal_is_said_in_its_words(self) -> None:
        s = store(
            accept_suggestion=AsyncMock(side_effect=rule_sets.RuleSetError(rule_sets.CHANGED_AGAIN))
        )
        it = interaction(DM, bot_with(s))
        await set_suggestion.press(it, CAMPAIGN, "accept", SID)
        self.assertEqual(it.sent[0][0], rule_sets.CHANGED_AGAIN)
        it.message.edit.assert_not_called()

    async def test_no_thanks_closes_it_and_changes_nothing_else(self) -> None:
        s = store()
        it = interaction(DM, bot_with(s))
        await set_suggestion.press(it, CAMPAIGN, "no", SID)
        s.decline_suggestion.assert_awaited_once()
        s.accept_suggestion.assert_not_called()
        it.message.edit.assert_awaited_once()

    async def test_see_shows_the_line_that_would_be_added(self) -> None:
        it = interaction(DM, bot_with(store()))
        await set_suggestion.press(it, CAMPAIGN, "see", SID)
        self.assertIn("version 5", it.sent[0][0])
        self.assertIn("New: rule 2, Potions are a bonus action", it.sent[0][0])


class Posting(unittest.IsolatedAsyncioTestCase):
    async def test_the_note_goes_to_the_dm_screen_and_is_marked(self) -> None:
        s = store()
        bot = bot_with(s)
        self.assertTrue(await set_suggestion.post_note(bot, 1, CAMPAIGN, OPEN, "Red Table"))
        channel, text, _ = bot.post_message.await_args.args
        self.assertEqual(channel, 99)
        self.assertNotIn("Far Away", text)
        s.mark_noted.assert_awaited_once_with(1, CAMPAIGN, SID)

    async def test_no_screen_or_a_failed_post_is_tried_again_later(self) -> None:
        s = store()
        no_screen = bot_with(s, camp=campaign(screen=None))
        self.assertFalse(await set_suggestion.post_note(no_screen, 1, CAMPAIGN, OPEN, "Red Table"))
        failed = bot_with(s)
        failed.post_message = AsyncMock(return_value=None)
        self.assertFalse(await set_suggestion.post_note(failed, 1, CAMPAIGN, OPEN, "Red Table"))
        s.mark_noted.assert_not_called()

    async def test_waiting_notes_are_posted_at_a_session_start(self) -> None:
        s = store(unnoted_suggestions=AsyncMock(return_value=[(OPEN, "Red Table")]))
        bot = bot_with(s)
        await set_suggestion.post_waiting(bot, campaign())
        bot.post_message.assert_awaited_once()
        s.mark_noted.assert_awaited_once()


class TheSendersLine(unittest.IsolatedAsyncioTestCase):
    def test_wording(self) -> None:
        used = set_suggestion.answer_line(
            rule_sets.Sent(SID, "Red Table", "Flank twice", "accepted", 6)
        )
        no = set_suggestion.answer_line(
            rule_sets.Sent(SID, "Red Table", "Flank twice", "declined", None)
        )
        self.assertIn("added your suggestion (now version 6)", used)
        self.assertIn("said no thanks to your suggestion", no)

    async def test_each_answer_is_told_once_and_open_ones_wait(self) -> None:
        sent = [
            rule_sets.Sent("1" * 32, "Red Table", "A", "accepted", 2),
            rule_sets.Sent("2" * 32, "Red Table", "B", "open", None),
            rule_sets.Sent("3" * 32, "Red Table", "C", "declined", None),
        ]
        s = store(sent_answers=AsyncMock(return_value=sent))
        bot = bot_with(s)
        await set_suggestion.tell_answers(bot, campaign(), 55)
        posted = bot.post.await_args.args[1]
        self.assertEqual(posted.count("\n"), 1)  # two lines, one message
        s.mark_told.assert_awaited_once_with(1, CAMPAIGN, ["1" * 32, "3" * 32])

    async def test_nothing_is_marked_told_if_it_could_not_be_posted(self) -> None:
        s = store(
            sent_answers=AsyncMock(return_value=[rule_sets.Sent(SID, "R", "A", "declined", None)])
        )
        bot = bot_with(s)
        bot.post = AsyncMock(return_value=False)
        await set_suggestion.tell_answers(bot, campaign(), 55)
        s.mark_told.assert_not_called()


class TheButtons(unittest.IsolatedAsyncioTestCase):
    def labels(self, menu: Any) -> list[str]:
        return [str(c.label) for c in menu.children if hasattr(c, "label")]

    async def test_own_rules_get_a_suggest_button_only_while_following(self) -> None:
        rules = [rule(1, "Mine"), rule(2, "Merged", from_set="Red Table house rules, v3")]
        on = house_rules.ListMenu(campaign(), rules, 0, is_dm=True, suggest_to="Red Table")
        self.assertEqual([x for x in self.labels(on) if "Suggest" in x], ["💡 Suggest 1"])
        off = house_rules.ListMenu(campaign(), rules, 0, is_dm=True)
        self.assertFalse([x for x in self.labels(off) if "Suggest" in x])
        player = house_rules.ListMenu(campaign(), rules, 0, is_dm=False, suggest_to="Red Table")
        self.assertFalse([x for x in self.labels(player) if "Suggest" in x])

    async def test_the_rule_menu_names_the_set(self) -> None:
        menu = house_rules.RuleMenu(campaign(), rule(1, "Mine"), 0, "Red Table")
        self.assertIn("💡 Suggest 1", self.labels(menu))
        merged = house_rules.RuleMenu(campaign(), rule(1, "M", from_set="x"), 0, "Red Table")
        self.assertFalse([x for x in self.labels(merged) if "Suggest" in x])

    async def test_asking_says_nobody_is_named_and_nothing_is_sent_yet(self) -> None:
        s = store(suggest=AsyncMock())
        it = interaction(DM, bot_with(s))
        await house_rules.ask_suggest(campaign(), rule(1, "Mine"), 0, "Red Table", it)
        shown = it.response.edit_message.await_args.kwargs["content"]
        self.assertIn("with nobody named", shown)
        s.suggest.assert_not_called()

    async def test_sending_posts_the_note_and_a_refusal_is_said(self) -> None:
        s = store(suggest=AsyncMock(return_value=(SID, 5, "c" * 32)))
        s.suggestion = AsyncMock(return_value=OPEN)
        bot = bot_with(s)
        it = interaction(DM, bot)
        await house_rules.ConfirmSuggest(campaign(), rule(1, "Mine"), 0, "Red Table")._yes(it)
        s.suggest.assert_awaited_once_with(1, CAMPAIGN, DM, 1)
        await asyncio.gather(*bot.tasks)
        bot.post_message.assert_awaited_once()
        refused = store(
            suggest=AsyncMock(side_effect=HouseRuleError(rule_sets.TOO_MANY_SUGGESTIONS))
        )
        bot2 = bot_with(refused)
        it2 = interaction(DM, bot2)
        await house_rules.ConfirmSuggest(campaign(), rule(1, "Mine"), 0, "Red Table")._yes(it2)
        await asyncio.gather(*bot2.tasks)
        bot2.post_message.assert_not_called()
        redrawn = it2.edit_original_response.await_args.kwargs["content"]
        self.assertIn(rule_sets.TOO_MANY_SUGGESTIONS[:60], redrawn)


class LogsStayApart(unittest.IsolatedAsyncioTestCase):
    async def test_the_note_is_posted_under_the_publishers_log_tags_not_the_senders(self) -> None:
        from dmbot import logs

        seen: list[int | None] = []

        async def post_message(*_: Any) -> None:
            seen.append(logs._guild.get())  # what a failed-post warning would be tagged with
            return None

        s = store(suggest=AsyncMock(return_value=(SID, 5, "c" * 32)))
        bot = bot_with(s, camp=campaign())
        bot.post_message = post_message
        it = interaction(DM, bot)
        logs.set_log_context(guild_id=1)  # the sender's press is tagged with the sender's server
        await house_rules.ConfirmSuggest(campaign(), rule(1, "Mine"), 0, "Red Table")._yes(it)
        await asyncio.gather(*bot.tasks)
        self.assertEqual(seen, [5])  # the publisher's server, never the sender's


class Compare(unittest.IsolatedAsyncioTestCase):
    OLD: ClassVar[dict[str, Any]] = {
        "rules": [{"number": 1, "rule": "Crits double the dice", "instead": None}],
        "link": None,
    }
    NEW: ClassVar[dict[str, Any]] = {
        "rules": [{"number": 1, "rule": "Crits triple the dice", "instead": None}],
        "link": None,
    }

    def test_only_an_overridden_shared_rule_is_found(self) -> None:
        changes = set_diff.diff(self.OLD, self.NEW)
        mine = rule(4, "Crits max the dice", "Crits double the dice")
        other = rule(5, "Something else")
        merged = rule(6, "Crits max", "Crits double the dice", from_set="Red Table house rules, v3")
        found = set_update.overrides([other, merged, mine], changes)
        self.assertEqual([(c.number, r.number) for c, r in found], [(1, 4)])
        self.assertEqual(set_update.overrides([other], changes), [])
        added = set_diff.diff(
            self.NEW,
            {
                "rules": self.NEW["rules"] * 1 + [{"number": 2, "rule": "New", "instead": None}],
                "link": None,
            },
        )
        self.assertEqual(set_update.overrides([mine], added), [])

    def test_the_text_shows_before_now_and_yours(self) -> None:
        changes = set_diff.diff(self.OLD, self.NEW)
        text = set_update.compare_text(
            "Red Table",
            set_update.overrides([rule(4, "Crits max the dice", "Crits double the dice")], changes),
        )
        for part in (
            "Before: Crits double the dice",
            "Now: Crits triple the dice",
            "Yours (still applies): Crits max the dice",
        ):
            self.assertIn(part, text)

    def test_the_note_says_so_and_gets_a_compare_button(self) -> None:
        self.assertNotIn("Yours still applies", set_update.note_text("Red", 5, 1))
        self.assertIn("Yours still applies", set_update.note_text("Red", 5, 1, False, True))
        self.assertIn("Press Compare", set_update.OVERRIDDEN)
        plain = [c.custom_id for c in set_update.note_view(CAMPAIGN, 5).children]  # type: ignore[attr-defined]
        more = [c.custom_id for c in set_update.note_view(CAMPAIGN, 5, True).children]  # type: ignore[attr-defined]
        self.assertEqual((len(plain), len(more)), (3, 4))
        self.assertTrue(more[2].startswith("dmbot:setupd:compare:"))  # before Undo, the riskier one

    async def test_pressing_compare_shows_the_three_texts(self) -> None:
        follow = rule_sets.Follow(
            "s", "house_rules", "Red Table", "Red", 5, self.NEW, True, 1, None, 4, self.OLD
        )
        s = store(following=AsyncMock(return_value=[follow]))
        mine = rule(4, "Crits max the dice", "Crits double the dice")
        it = interaction(DM, bot_with(s, rules=[mine]))
        await set_update.press(it, CAMPAIGN, "compare", 5)
        self.assertIn("Yours (still applies): Crits max the dice", it.sent[0][0])
        none = interaction(DM, bot_with(s, rules=[rule(9, "Unrelated")]))
        await set_update.press(none, CAMPAIGN, "compare", 5)
        self.assertIn("None of your own rules", none.sent[0][0])


if __name__ == "__main__":
    unittest.main()
