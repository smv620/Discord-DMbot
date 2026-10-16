"""Publishing a new version, undoing it and the history (#1191): the diff shown first, the checks
on every press, and that nothing is published without a press. Fake stores, no Discord."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRule
from dmbot.ui import shared_rules_defs as defs_ui
from dmbot.ui import shared_rules_publish as pub
from tests.test_shared_rules_ui import bot_with, campaign, interaction

SET = rule_sets.SharedSet("s", "Red Table rules", True, 4)
BEFORE = {
    "rules": [{"number": 1, "rule": "Flanking gives advantage", "instead": None}],
    "link": None,
    "definitions": [],
}


def rule(number: int, text: str) -> HouseRule:
    return HouseRule(number, "c1", text, None, None, None, 7, 1, 1)


def rules_bot(s: Any, rules: list[HouseRule] | None = None, **kw: Any) -> Any:
    """A bot whose campaign holds exactly the rules BEFORE says (or the ones given)."""
    bot = bot_with(s, **kw)
    bot.house_rules.list = AsyncMock(
        return_value=rules if rules is not None else [rule(1, "Flanking gives advantage")]
    )
    return bot


def store(**kw: Any) -> Any:
    s = MagicMock()
    s.current_content = AsyncMock(return_value=(SET, BEFORE))
    s.published = AsyncMock(return_value=SET)
    s.version_log = AsyncMock(return_value=[])
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

    async def test_the_plan_is_checked_before_the_diff_is_shown(self) -> None:
        s = store()
        it = interaction(7, rules_bot(s, refusal="Sharing house rules needs a paid plan."))
        await pub.show_changes(it, campaign())
        self.assertEqual(it.sent[0][0], "Sharing house rules needs a paid plan.")
        s.current_content.assert_not_called()

    async def test_a_link_only_change_is_said_in_words(self) -> None:
        s = store()
        bot = rules_bot(s)
        bot.house_file_links = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(link="https://example.com/rules"))
        )
        it = interaction(7, bot)
        await pub.show_changes(it, campaign())
        text = it.sent[0][0]
        self.assertIn("no rule changed", text)
        self.assertIn(pub.LINK_ONLY, text)

    async def test_publish_checks_the_plan_and_the_version_the_dm_saw(self) -> None:
        made = rule_sets.SharedSet("s", "Red Table rules", True, 5)
        s = store(publish_changes=AsyncMock(return_value=made))
        bot = rules_bot(s, refusal="Sharing house rules needs a paid plan.")
        it = interaction(7, bot)
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it)
        self.assertEqual(it.sent[0][0], "Sharing house rules needs a paid plan.")
        s.publish_changes.assert_not_called()
        # Someone else published meanwhile: the DM looks again, nothing is published.
        s2 = store(
            current_content=AsyncMock(
                return_value=(rule_sets.SharedSet("s", "R", True, 5), BEFORE)
            ),
            publish_changes=AsyncMock(return_value=made),
        )
        it2 = interaction(7, rules_bot(s2))
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it2)
        self.assertEqual(it2.sent[0][0], rule_sets.CHANGED_AGAIN)
        s2.publish_changes.assert_not_called()
        # The normal press publishes.
        s3 = store(publish_changes=AsyncMock(return_value=made))
        it3 = interaction(7, rules_bot(s3))
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it3)
        self.assertIn("version 5", it3.sent[0][0])
        s3.publish_changes.assert_awaited_once()


class WhatWasShown(unittest.IsolatedAsyncioTestCase):
    async def test_rules_edited_after_the_diff_are_not_published_unseen(self) -> None:
        shown = {
            "rules": [{"number": 1, "rule": "Flanking gives advantage", "instead": None}],
            "link": None,
        }
        s = store(publish_changes=AsyncMock())
        bot = bot_with(s)
        bot.house_rules.list = AsyncMock(return_value=[rule(1, "Flanking is optional")])
        it = interaction(7, bot)
        await pub.ConfirmPublish(campaign(), 4, shown)._yes(it)
        self.assertEqual(it.sent[0][0], pub.YOUR_RULES_CHANGED)
        s.publish_changes.assert_not_called()

    async def test_the_store_gets_the_version_the_dm_saw_and_an_unshared_set_is_said(self) -> None:
        s = store(publish_changes=AsyncMock(return_value=rule_sets.SharedSet("s", "R", True, 5)))
        it = interaction(7, rules_bot(s))
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it)
        self.assertEqual(s.publish_changes.await_args.kwargs["expected_version"], 4)
        gone = store(current_content=AsyncMock(return_value=None), publish_changes=AsyncMock())
        it2 = interaction(7, rules_bot(gone))
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it2)
        self.assertEqual(it2.sent[0][0], pub.NOT_SHARED_HERE)
        gone.publish_changes.assert_not_called()


class Limits(unittest.IsolatedAsyncioTestCase):
    def test_a_long_diff_stays_under_the_message_limit(self) -> None:
        from dmbot.rules import set_diff

        changes = [
            set_diff.Change("added", n, None, {"number": n, "rule": "x" * 400, "instead": None})
            for n in range(1, 40)
        ]
        self.assertLess(len(pub.changes_text(changes)), pub.BODY_MAX + 40)


class History(unittest.IsolatedAsyncioTestCase):
    async def test_the_log_is_shown_by_name_without_any_editor_list(self) -> None:
        log = [
            rule_sets.VersionLog(2, 7, 1_700_000_000, "suggestion"),
            rule_sets.VersionLog(1, 7, 1_699_000_000, "publisher"),
        ]
        s = store(version_log=AsyncMock(return_value=log))
        bot = bot_with(s)
        bot.name_of = lambda guild, user: "Sam"
        it = interaction(7, bot)
        await pub.show_history(it, campaign())
        text = it.sent[0][0]
        self.assertIn("Version 2, by Sam", text)
        self.assertIn("from a player's suggestion", text)
        self.assertNotIn("editor", text.lower())

    async def test_a_player_cannot_read_the_history(self) -> None:
        it = interaction(99, bot_with(store()))
        await pub.show_history(it, campaign())
        self.assertIn("Only this campaign's DMs", it.sent[0][0])


class UndoingAPublish(unittest.IsolatedAsyncioTestCase):
    async def test_undo_republishes_the_earlier_rules_as_the_next_version(self) -> None:
        made = rule_sets.SharedSet("s", "Red Table rules", True, 6)
        s = store(
            content_at=AsyncMock(return_value=BEFORE), save_version=AsyncMock(return_value=made)
        )
        it = interaction(7, bot_with(s))
        await pub.UndoPublish(campaign(), 5)._undo(it)
        s.content_at.assert_awaited_once_with(1, "c1", 4)
        args, kwargs = s.save_version.await_args
        self.assertEqual(args[3], BEFORE)
        self.assertEqual(kwargs["expected_version"], 5)
        self.assertIn("Version 6 has the rules from before", it.sent[0][0])

    async def test_undo_is_refused_for_a_player_and_without_a_paid_plan(self) -> None:
        s = store(content_at=AsyncMock(return_value=BEFORE), save_version=AsyncMock())
        it = interaction(99, bot_with(s))
        await pub.UndoPublish(campaign(), 5)._undo(it)
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        it2 = interaction(7, bot_with(s, refusal="Sharing house rules needs a paid plan."))
        await pub.UndoPublish(campaign(), 5)._undo(it2)
        self.assertEqual(it2.sent[0][0], "Sharing house rules needs a paid plan.")
        s.save_version.assert_not_called()

    async def test_a_publish_cannot_be_confirmed_by_a_player(self) -> None:
        s = store(publish_changes=AsyncMock())
        it = interaction(99, rules_bot(s))
        await pub.ConfirmPublish(campaign(), 4, BEFORE)._yes(it)
        self.assertIn("Only this campaign's DMs", it.sent[0][0])
        s.publish_changes.assert_not_called()


def definition(key: str, label: str = "", text: str = "A plain meaning.") -> dict[str, Any]:
    return {
        "key": key,
        "label": label or key,
        "inverse_label": "",
        "description": text,
        "subject_types": ["person"],
        "object_types": ["person"],
        "symmetric": False,
        "version": 1,
    }


def with_memory(bot: Any, own: list[dict[str, Any]]) -> Any:
    bot.memory = object()  # only passed to the (patched) reader
    return patch("dmbot.memory.set_definitions.own_definitions", AsyncMock(return_value=own))


class TickListAtPublish(unittest.IsolatedAsyncioTestCase):
    """Publish my changes: the set's relationships start ticked, a new one does not (#1475);
    unticking one takes it out of the set (#1479)."""

    def shared(self, *held: dict[str, Any]) -> Any:
        return store(
            current_content=AsyncMock(return_value=(SET, {**BEFORE, "definitions": list(held)}))
        )

    async def open_list(self, s: Any, own: list[dict[str, Any]]) -> tuple[Any, Any, Any]:
        bot = rules_bot(s)
        it = interaction(7, bot)
        with with_memory(bot, own):
            await pub.show_changes(it, campaign())
        return bot, it, it.sent[0][1]

    async def test_no_relationships_anywhere_shows_no_list(self) -> None:
        s = self.shared()
        bot = rules_bot(s)
        it = interaction(7, bot)
        with with_memory(bot, []):
            await pub.show_changes(it, campaign())
        self.assertEqual(it.sent[0][0], pub.NOTHING_NEW)

    async def test_the_sets_ones_start_ticked_and_a_new_one_does_not(self) -> None:
        s = self.shared(definition("vowed_to"))
        _, it, view = await self.open_list(s, [definition("vowed_to"), definition("guards")])
        self.assertIsInstance(view, defs_ui.ShareChoose)
        self.assertEqual([d["key"] for d in view.offered], ["vowed_to", "guards"])
        self.assertEqual(view.ticked, {"vowed_to"})
        self.assertIn("Untick one to stop sharing it", it.sent[0][0])
        s.publish_changes.assert_not_called()

    async def test_keep_as_it_is_changes_nothing(self) -> None:
        carried = definition("vowed_to")
        s = self.shared(carried)
        bot, it, view = await self.open_list(s, [carried, definition("guards")])
        view.ticked = set()  # the select was changed first: "Keep as it is" still keeps the start
        with with_memory(bot, [carried, definition("guards")]):
            await view._none(it)
        self.assertEqual(it.sent[1][0], pub.NOTHING_NEW)

    async def test_a_ticked_new_one_is_carried_and_shown_as_new(self) -> None:
        s = self.shared(definition("vowed_to"))
        made = rule_sets.SharedSet("s", "Red Table rules", True, 5)
        s.publish_changes = AsyncMock(return_value=made)
        own = [definition("vowed_to"), definition("guards", "guards")]
        bot, it, view = await self.open_list(s, own)
        with with_memory(bot, own):
            view.ticked = {"vowed_to", "guards"}
            await view._share(it)
            text, confirm = it.sent[1]
            self.assertIn("New relationship: guards", text)
            self.assertIsInstance(confirm, pub.ConfirmPublish)
            await confirm._yes(it)
        sent = s.publish_changes.await_args.kwargs["definitions"]
        self.assertEqual([d["key"] for d in sent], ["vowed_to", "guards"])

    async def test_an_unticked_one_is_taken_out_of_the_version_and_listed_as_removed(self) -> None:
        s = self.shared(definition("vowed_to"), definition("guards"))
        made = rule_sets.SharedSet("s", "Red Table rules", True, 5)
        s.publish_changes = AsyncMock(return_value=made)
        own = [definition("vowed_to"), definition("guards")]
        bot, it, view = await self.open_list(s, own)
        with with_memory(bot, own):
            view.ticked = {"vowed_to"}
            await view._share(it)
            text, confirm = it.sent[1]
            self.assertIn("Removed from the set: guards", text)
            await confirm._yes(it)
        sent = s.publish_changes.await_args.kwargs["definitions"]
        self.assertEqual([d["key"] for d in sent], ["vowed_to"])

    async def test_unticking_every_one_empties_the_sets_relationships(self) -> None:
        s = self.shared(definition("vowed_to"))
        s.publish_changes = AsyncMock(return_value=rule_sets.SharedSet("s", "R", True, 5))
        own = [definition("vowed_to")]
        bot, it, view = await self.open_list(s, own)
        with with_memory(bot, own):
            view.ticked = set()
            await view._share(it)
            confirm = it.sent[1][1]
            self.assertEqual(confirm.shown["definitions"], [])
            await confirm._yes(it)
        self.assertEqual(s.publish_changes.await_args.kwargs["definitions"], [])

    async def test_the_publishers_own_relationship_is_not_touched(self) -> None:
        s = self.shared(definition("vowed_to"))
        own = [definition("vowed_to")]
        bot, it, view = await self.open_list(s, own)
        reader = with_memory(bot, own)
        bot.memory = MagicMock()  # replaces the stand-in: any call on it is recorded
        with reader:
            view.ticked = set()
            await view._share(it)
        # Publishing only reads the memory (through the patched reader); it writes nothing.
        self.assertEqual(bot.memory.mock_calls, [])

    async def test_an_unreadable_memory_stops_a_publish_that_unticks(self) -> None:
        s = self.shared(definition("vowed_to"), definition("guards"))
        own = [definition("vowed_to"), definition("guards")]
        _, it, view = await self.open_list(s, own)
        view.ticked = {"vowed_to"}
        failing = AsyncMock(side_effect=RuntimeError("down"))
        with patch("dmbot.memory.set_definitions.own_definitions", failing):
            await view._share(it)
        self.assertEqual(it.sent[1][0], pub.COULD_NOT_CHANGE)
        s.publish_changes.assert_not_called()

    async def test_a_set_one_the_campaign_no_longer_has_drops_out_of_the_list(self) -> None:
        s = self.shared(definition("vowed_to"), definition("gone"))
        _, _, view = await self.open_list(s, [definition("vowed_to")])
        self.assertEqual([d["key"] for d in view.offered], ["vowed_to"])
        self.assertEqual(view.ticked, {"vowed_to"})

    async def test_a_carried_one_is_refreshed_with_the_campaigns_current_words(self) -> None:
        s = self.shared(definition("vowed_to"))
        edited = definition("vowed_to", text="Swore a binding oath.")
        bot, it, view = await self.open_list(s, [edited])
        with with_memory(bot, [edited]):
            await view._share(it)
        text, confirm = it.sent[1]
        self.assertIn("meaning changed", text)
        self.assertEqual(confirm.shown["definitions"][0]["description"], "Swore a binding oath.")

    async def test_the_press_publishes_what_was_shown_and_stops_if_it_changed(self) -> None:
        s = self.shared(definition("vowed_to"))
        own = [definition("vowed_to"), definition("guards")]
        bot, it, view = await self.open_list(s, own)
        with with_memory(bot, own):
            view.ticked = {"vowed_to", "guards"}
            await view._share(it)
            confirm = it.sent[1][1]
        # The relationship was edited after the DM looked: nothing is published unseen.
        with with_memory(bot, [definition("vowed_to"), definition("guards", text="New words.")]):
            await confirm._yes(it)
        self.assertEqual(it.sent[2][0], pub.YOUR_RULES_CHANGED)
        s.publish_changes.assert_not_called()

    async def test_a_full_set_offers_what_it_has_and_nothing_new(self) -> None:
        held = [definition(f"rel_{i:02d}") for i in range(20)]
        s = self.shared(*held)
        _, _, view = await self.open_list(s, [*held, definition("one_more")])
        self.assertEqual(len(view.offered), 20)
        self.assertNotIn("one_more", [d["key"] for d in view.offered])

    async def test_one_slot_left_offers_one_new_and_the_plan_is_asked_before_the_list(self) -> None:
        held = [definition(f"rel_{i:02d}") for i in range(19)]
        s = self.shared(*held)
        own = [*held, definition("one_more"), definition("two_more")]
        _, _, view = await self.open_list(s, own)
        self.assertEqual(len(view.offered), 20)
        self.assertEqual(view.offered[-1]["key"], "one_more")
        refused = rules_bot(s, refusal="Sharing house rules needs a paid plan.")
        it2 = interaction(7, refused)
        with with_memory(refused, own):
            await pub.show_changes(it2, campaign())
        self.assertEqual(it2.sent[0][0], "Sharing house rules needs a paid plan.")

    async def test_a_press_on_the_list_checks_the_plan_again(self) -> None:
        s = self.shared(definition("vowed_to"))
        bot, it, view = await self.open_list(s, [definition("vowed_to")])
        bot.plan_gate = AsyncMock(return_value="Sharing house rules needs a paid plan.")
        await view._none(it)
        self.assertEqual(it.sent[1][0], "Sharing house rules needs a paid plan.")

    async def test_a_ticked_one_that_is_gone_is_said_and_nothing_is_shown_for_publish(self) -> None:
        s = self.shared(definition("vowed_to"))
        own = [definition("vowed_to"), definition("guards")]
        bot, it, view = await self.open_list(s, own)
        view.ticked = {"vowed_to", "guards"}
        with with_memory(bot, [definition("vowed_to")]):  # deleted before the press
            await view._share(it)
        self.assertEqual(it.sent[1][0], pub.COULD_NOT_CHANGE)

    async def test_no_memory_means_no_list(self) -> None:
        s = self.shared(definition("vowed_to"))
        it = interaction(7, rules_bot(s))
        await pub.show_changes(it, campaign())
        self.assertEqual(it.sent[0][0], pub.NOTHING_NEW)


if __name__ == "__main__":
    unittest.main()
