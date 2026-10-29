"""The shared-rules update note and the Cherry pick screen (#1191): wording, Discord's limits
(25 choices to a menu, 5 rows), and the session-start rules. Fake stores, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import set_update
from dmbot.rules import rule_sets, set_diff
from dmbot.rules.house import HouseRuleError

CAMPAIGN = "a" * 32
DM = 7


def update(n: int) -> rule_sets.Update:
    old: dict[str, Any] = {"rules": [], "link": None}
    new = {"rules": [{"number": i, "rule": f"Rule {i}", "instead": None} for i in range(1, n + 1)]}
    follow = rule_sets.Follow("s", "house_rules", "Red Table", "Red", 3, old, True, 1, None)
    offer = rule_sets.Offer("s", "house_rules", "Red Table", "Red", 4, new | {"link": None}, None)
    return rule_sets.Update(follow, offer, tuple(set_diff.diff(old, offer.content)))


def campaign() -> Any:
    return SimpleNamespace(guild_id=1, id=CAMPAIGN, dm_user_ids=[DM])


def follow_at(version: int, previous: int | None = 3) -> rule_sets.Follow:
    before: dict[str, Any] = {"rules": [], "link": None}
    now = {"rules": [{"number": 1, "rule": "New rule", "instead": None}], "link": None}
    return rule_sets.Follow(
        "s", "house_rules", "Red Table", "Red", version, now, True, 1, None, previous, before
    )


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
    it.response.is_done = lambda: done["v"]
    it.response.defer = defer
    it.response.send_message = send
    it.followup.send = send
    it.sent = sent
    return it


def bot_with(**sets: Any) -> Any:
    store = SimpleNamespace(
        pending_update=AsyncMock(return_value=None),
        is_gone=AsyncMock(return_value=False),
        stop_updating=AsyncMock(return_value=1),
        following=AsyncMock(return_value=[follow_at(4)]),
        take_update=AsyncMock(),
        undo_last_take=AsyncMock(),
        sent_answers=AsyncMock(return_value=[]),
        unnoted_suggestions=AsyncMock(return_value=[]),
    )
    for name, value in sets.items():
        setattr(store, name, value)
    return SimpleNamespace(
        rule_sets=store,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
        post=AsyncMock(return_value=object()),
        post_message=AsyncMock(return_value=object()),
        house_rules=SimpleNamespace(list=AsyncMock(return_value=[])),
    )


class Wording(unittest.TestCase):
    def test_a_link_only_update_says_so(self) -> None:
        self.assertIn(
            "only the rules document link changed", set_update.note_text("Red Table", 4, 0, True)
        )
        self.assertIn("document link changed", set_update.changes_text("Red Table", 4, [], True))
        both = set_update.note_text("Red Table", 4, 2, True)
        self.assertIn("2 rules and the rules document link", both)

    def test_the_note_says_what_changed_and_that_dmbot_took_it(self) -> None:
        text = set_update.note_text("Red Table", 4, 2)
        self.assertIn("house rules updated to v4: 2 rules", text)
        self.assertIn("DMbot took the new version", text)

    def test_three_buttons_that_survive_a_restart_and_name_their_version(self) -> None:
        view = set_update.note_view(CAMPAIGN, 4)
        children: list[Any] = list(view.children)  # dynamic items: typed loosely on purpose
        self.assertEqual([i.item.label for i in children], ["See changes", "Pick changes", "Undo"])
        self.assertTrue(all(i.item.custom_id.endswith(f":{CAMPAIGN}:4") for i in children))

    def test_the_change_list_is_cut_to_discords_limit(self) -> None:
        changes = list(update(400).changes)
        self.assertLessEqual(len(set_update.changes_text("Red Table", 4, changes)), 2100)


class Picking(unittest.TestCase):
    def test_every_change_starts_kept_and_a_page_holds_25(self) -> None:
        view = set_update.PickView(campaign(), update(60))
        children: list[Any] = list(view.children)
        select = next(c for c in children if hasattr(c, "options"))
        self.assertEqual(len(select.options), 25)
        self.assertTrue(all(o.default for o in select.options))
        self.assertEqual(len(view.accepted), 60)
        self.assertLessEqual(len(view.children), 4)  # menu, back, more, save: rows are enough
        self.assertIn("page 1 of 3", view.text())

    def test_a_short_list_has_no_paging(self) -> None:
        view = set_update.PickView(campaign(), update(3))
        labels = [getattr(c, "label", None) for c in view.children]
        self.assertEqual([x for x in labels if x], ["Save my picks"])


def update_with_definitions() -> rule_sets.Update:
    """One new rule and two relationship changes (#1456)."""
    from dmbot.memory import set_definitions as sd
    from tests.test_set_definitions import term

    old: dict[str, Any] = {"rules": [], "link": None, "definitions": []}
    defs = [sd.definition_of(term()), sd.definition_of(term("guards", "guards"))]
    new = {
        "rules": [{"number": 1, "rule": "R", "instead": None}],
        "link": None,
        "definitions": defs,
    }
    follow = rule_sets.Follow("s", "house_rules", "Red Table", "Red", 3, old, True, 1, None)
    offer = rule_sets.Offer("s", "house_rules", "Red Table", "Red", 4, new, None)
    return rule_sets.Update(
        follow,
        offer,
        tuple(set_diff.diff(old, new)),
        tuple(set_diff.diff_definitions(old, new)),
    )


class PickingRelationships(unittest.IsolatedAsyncioTestCase):
    def test_the_sets_relationships_are_in_the_same_list_after_the_rules(self) -> None:
        view = set_update.PickView(campaign(), update_with_definitions())
        select = next(c for c in view.children if hasattr(c, "options"))
        self.assertEqual([o.value for o in select.options], ["r:1", "d:guards", "d:vowed_to"])
        self.assertTrue(all(o.default for o in select.options))
        self.assertIn("New relationship: vows loyalty to", view.text())
        self.assertIn("relationships you keep become yours", view.text())
        labels = [o.label for o in select.options]
        self.assertEqual(labels, ["1 Added", "New: guards", "New: vows loyalty to"])

    async def test_a_long_mixed_list_pages_and_keeps_rule_numbers_and_keys_apart(self) -> None:
        from dmbot.memory import set_definitions as sd
        from tests.test_set_definitions import term

        defs = [sd.definition_of(term(f"rel_{i:02d}", f"label {i}")) for i in range(5)]
        old: dict[str, Any] = {"rules": [], "link": None, "definitions": []}
        new = {
            "rules": [{"number": i, "rule": f"Rule {i}", "instead": None} for i in range(1, 28)],
            "link": None,
            "definitions": defs,
        }
        follow = rule_sets.Follow("s", "house_rules", "Red", "Red", 3, old, True, 1, None)
        offer = rule_sets.Offer("s", "house_rules", "Red", "Red", 4, new, None)
        upd = rule_sets.Update(
            follow,
            offer,
            tuple(set_diff.diff(old, new)),
            tuple(set_diff.diff_definitions(old, new)),
        )
        view = set_update.PickView(campaign(), upd)
        self.assertEqual(len(view.items), 32)
        self.assertEqual(len(view._page_items()), 25)
        view.page = 1
        self.assertEqual([v for v, _l, _x in view._page_items()][-1], "d:rel_04")
        self.assertEqual(len(view.accepted), 32)
        self.assertTrue(all(v.startswith(("r:", "d:")) for v in view.accepted))

    async def test_the_picks_go_to_the_store_as_numbers_and_keys(self) -> None:
        bot = bot_with(
            take_update=AsyncMock(side_effect=HouseRuleError(rule_sets.CHANGED_AGAIN)),
            pending_update=AsyncMock(return_value=update(3)),
        )
        view = set_update.PickView(campaign(), update_with_definitions())
        view.accepted = {"r:1", "d:guards"}  # the DM unticked one relationship
        await view._apply(interaction(DM, bot))
        call = bot.rule_sets.take_update.await_args
        self.assertEqual((call.args[3], call.kwargs["accepted_defs"]), ({1}, {"guards"}))

    def test_the_note_and_the_list_count_relationships_with_the_rules(self) -> None:
        text = set_update.note_text("Red Table", 4, 1, relationships=2)
        self.assertIn("1 rule and 2 relationships", text)
        self.assertIn("2 relationships", set_update.note_text("Red", 4, 0, relationships=2))
        seen = set_update.changes_text("Red", 4, [], False, update_with_definitions().def_changes)
        self.assertIn("New relationship: guards", seen)


class SavingPicks(unittest.IsolatedAsyncioTestCase):
    async def test_the_picks_are_applied_to_the_version_the_dm_was_shown(self) -> None:
        # The picker was opened on v4; whatever is newest now, the store is told v4 and
        # refuses if the set moved on (it compares the two).
        bot = bot_with(
            take_update=AsyncMock(side_effect=HouseRuleError(rule_sets.CHANGED_AGAIN)),
            pending_update=AsyncMock(return_value=update(3)),
        )
        view = set_update.PickView(campaign(), update(3))
        it = interaction(DM, bot)
        await view._apply(it)
        kwargs = bot.rule_sets.take_update.await_args.kwargs
        self.assertEqual(kwargs["expected_version"], 4)
        self.assertEqual(it.sent[0][0], rule_sets.CHANGED_AGAIN)


class UndoingAPick(unittest.IsolatedAsyncioTestCase):
    async def test_both_undo_replies_say_dmbot_waits_for_a_newer_version(self) -> None:
        for merged in (True, False):
            taken = rule_sets.Taken(merged, follow_at(3, None), 4, offered=4)
            bot = bot_with(undo_take=AsyncMock(return_value=2 if merged else 0))
            it = interaction(DM, bot)
            await set_update.UndoView(campaign(), taken)._undo(it)
            said = it.sent[0][0]
            self.assertIn("Back on v3", said)
            self.assertIn(set_update.UNDONE_AND_WAITING, said)
            if merged:
                self.assertIn("2 merged rules you edited stayed", said)


class Pressing(unittest.IsolatedAsyncioTestCase):
    async def test_a_note_for_an_older_version_does_nothing(self) -> None:
        bot = bot_with(following=AsyncMock(return_value=[follow_at(5)]))
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "undo", 4)
        self.assertEqual(it.sent[0][0], set_update.NEWER)
        bot.rule_sets.undo_last_take.assert_not_called()

    async def test_undo_goes_back_to_the_copy_before(self) -> None:
        bot = bot_with()
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "undo", 4)
        bot.rule_sets.undo_last_take.assert_awaited_once_with(1, CAMPAIGN, DM, 4)
        self.assertIn("Back on v3", it.sent[0][0])
        self.assertIn("DMbot will take the next new version; use Pick changes", it.sent[0][0])

    async def test_pick_undoes_the_take_and_opens_the_picker_in_one_step(self) -> None:
        bot = bot_with(pending_update=AsyncMock(return_value=update(2)))
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "pick", 4)
        bot.rule_sets.undo_last_take.assert_awaited_once()
        text, view = it.sent[0]
        self.assertIn("Pick changes", text)
        self.assertIsInstance(view, set_update.PickView)

    async def test_after_undo_pick_opens_the_picker_without_another_undo(self) -> None:
        # The campaign is back on v3 (undone), and v4 is still the newest version.
        undone = follow_at(3, previous=None)
        bot = bot_with(
            following=AsyncMock(return_value=[undone]),
            pending_update=AsyncMock(return_value=update(2)),
        )
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "pick", 4)
        bot.rule_sets.undo_last_take.assert_not_called()
        self.assertIsInstance(it.sent[0][1], set_update.PickView)

    async def test_after_undo_see_shows_the_changes_and_undo_says_it_is_done(self) -> None:
        bot = bot_with(
            following=AsyncMock(return_value=[follow_at(3, previous=None)]),
            pending_update=AsyncMock(return_value=update(2)),
        )
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "see", 4)
        self.assertIn("Rule 1", it.sent[0][0])
        again = interaction(DM, bot)
        await set_update.press(again, CAMPAIGN, "undo", 4)
        self.assertIn("Already undone", again.sent[0][0])
        bot.rule_sets.undo_last_take.assert_not_called()

    async def test_a_campaign_that_follows_nothing_gets_its_own_message(self) -> None:
        bot = bot_with(following=AsyncMock(return_value=[]))
        it = interaction(DM, bot)
        await set_update.press(it, CAMPAIGN, "pick", 4)
        self.assertEqual(it.sent[0][0], set_update.NOT_FOLLOWING_NOW)

    async def test_see_lists_what_differs_from_the_copy_before(self) -> None:
        it = interaction(DM, bot_with())
        await set_update.press(it, CAMPAIGN, "see", 4)
        self.assertIn("New rule", it.sent[0][0])

    async def test_only_a_dm_may_press(self) -> None:
        bot = bot_with()
        it = interaction(99, bot)
        await set_update.press(it, CAMPAIGN, "undo", 4)
        self.assertEqual(it.sent[0][0], set_update.ONLY_DMS)
        bot.rule_sets.undo_last_take.assert_not_called()


class SessionStart(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_version_is_taken_at_once_by_dmbot_with_a_note(self) -> None:
        bot = bot_with(pending_update=AsyncMock(return_value=update(2)))
        await set_update.check_update(bot, campaign(), 99)
        bot.rule_sets.take_update.assert_awaited_once_with(
            1, CAMPAIGN, None, None, expected_version=4
        )  # no person: DMbot, and the version it looked at
        text, view = bot.post_message.await_args.args[1:]
        self.assertIn("updated to v4", text)
        self.assertEqual(len(view.children), 3)

    async def test_nothing_new_says_nothing(self) -> None:
        bot = bot_with()
        await set_update.check_update(bot, campaign(), 99)
        bot.post.assert_not_called()
        bot.post_message.assert_not_called()
        bot.rule_sets.take_update.assert_not_called()

    async def test_a_set_that_stopped_being_shared_is_said_once(self) -> None:
        bot = bot_with(is_gone=AsyncMock(return_value=True))
        await set_update.check_update(bot, campaign(), 99)
        bot.rule_sets.stop_updating.assert_awaited_once()
        self.assertIn("isn't being shared any more", bot.post.await_args.args[1])
        self.assertIn(
            "Your table keeps version 4, and it won't get updates.", bot.post.await_args.args[1]
        )

    async def test_the_gone_note_is_not_said_twice_when_two_checks_race(self) -> None:
        bot = bot_with(
            is_gone=AsyncMock(return_value=True), stop_updating=AsyncMock(return_value=0)
        )
        await set_update.check_update(bot, campaign(), 99)
        bot.post.assert_not_called()  # the other check already stopped it and said it

    async def test_the_note_names_the_house_rules_set_not_another_follow(self) -> None:
        homebrew = rule_sets.Follow("h", "homebrew", "Pack", "Blue", 9, {}, True, 1, None)
        bot = bot_with(
            is_gone=AsyncMock(return_value=True),
            following=AsyncMock(return_value=[homebrew, follow_at(4)]),
        )
        await set_update.check_update(bot, campaign(), 99)
        said = bot.post.await_args.args[1]
        self.assertIn("Red Table", said)
        self.assertIn("version 4", said)
        self.assertNotIn("Pack", said)

    async def test_dmbot_asks_for_updates_that_skip_an_undone_version(self) -> None:
        bot = bot_with()
        await set_update.check_update(bot, campaign(), 99)
        bot.rule_sets.pending_update.assert_awaited_once_with(1, CAMPAIGN, for_dmbot=True)

    async def test_a_note_that_cannot_be_posted_is_logged(self) -> None:
        bot = bot_with(
            pending_update=AsyncMock(return_value=update(2)),
            post_message=AsyncMock(return_value=None),
        )
        bot.post_message = AsyncMock(return_value=None)
        with self.assertLogs("dmbot.dm_screen.shared_update", level="WARNING") as logged:
            await set_update.check_update(bot, campaign(), 99)
        self.assertIn("without a note", logged.output[0])

    async def test_a_failure_never_escapes_and_posts_nothing(self) -> None:
        bot = bot_with(pending_update=AsyncMock(side_effect=RuntimeError("down")))
        with self.assertLogs("dmbot.dm_screen.set_update", level="ERROR") as logged:
            await set_update.check_update(bot, campaign(), 99)
        self.assertIn("Couldn't check for a shared-rules update", logged.output[0])
        bot.post.assert_not_called()
        bot.post_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
