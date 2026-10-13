"""Combat state (#1127): the round arithmetic (6 seconds a round, the clock rounds up to the next
minute when combat ends), the strict phrases and what they refuse, who may change it (a DM, never
a player), the DM-screen buttons, Undo, the players' line, that it survives a restart and a
backup restore, and the question other parts ask. The store tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from dmbot.campaigns import CampaignError
from dmbot.dm_screen import clock as ui
from dmbot.timebot import clock as game
from dmbot.timebot import combat_phrases as phrases
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import ClockStore
from tests.test_timebot import (
    DM,
    GUILD_A,
    GUILD_B,
    NOON4,
    OTHER_DM,
    PLAYER,
    AtTheTable,
    Clocks,
)


class Arithmetic(unittest.TestCase):
    def test_a_fight_starts_at_round_one_and_only_once(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        self.assertEqual((c.combat_round, c.minute), (1, NOON4))
        self.assertIsNone(game.combat_start(c))  # "roll initiative" twice is one fight

    def test_every_round_is_six_seconds_so_the_minute_moves_every_tenth(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        for _ in range(9):
            nxt = game.combat_next(c)
            assert nxt is not None
            c = nxt
        self.assertEqual((c.combat_round, c.minute, c.combat_secs), (10, NOON4, 54))
        nxt = game.combat_next(c)
        assert nxt is not None
        self.assertEqual((nxt.combat_round, nxt.minute, nxt.combat_secs), (11, NOON4 + 1, 0))

    def test_a_named_round_spends_the_rounds_passed_and_going_back_spends_nothing(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        far = game.combat_round_to(c, 21)  # twenty rounds: 120 seconds
        assert far is not None
        self.assertEqual((far.combat_round, far.minute, far.combat_secs), (21, NOON4 + 2, 0))
        back = game.combat_round_to(far, 3)  # a correction
        assert back is not None
        self.assertEqual((back.combat_round, back.minute), (3, NOON4 + 2))
        self.assertIsNone(game.combat_round_to(back, 3))  # already there
        self.assertIsNone(game.combat_round_to(back, 100))  # out of range
        self.assertIsNone(game.combat_round_to(Clock(NOON4, 0), 2))  # not in combat

    def test_ending_combat_rounds_up_to_the_next_minute(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        over = game.combat_end(c)  # one round of 6 seconds still costs a minute
        assert over is not None
        self.assertEqual((over.combat_round, over.combat_secs, over.minute), (None, 0, NOON4 + 1))
        c = game.combat_round_to(c, 11)  # 60 seconds spent: one minute gone, plus the last round
        assert c is not None
        self.assertEqual(c.minute, NOON4 + 1)
        over = game.combat_end(c)
        assert over is not None
        self.assertEqual(over.minute, NOON4 + 2)
        self.assertIsNone(game.combat_end(over))

    def test_the_other_clock_moves_keep_the_fight(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        for moved in (game.advance(c, 10), game.set_to(c, NOON4 + 5), game.told_tired(c)):
            self.assertEqual(moved.combat_round, 1)

    def test_the_question_other_parts_ask(self) -> None:
        self.assertFalse(game.in_combat(None))
        self.assertFalse(game.in_combat(Clock(NOON4, 0)))
        self.assertTrue(game.in_combat(game.combat_start(Clock(NOON4, 0))))
        start = game.combat_start(Clock(NOON4, 0))
        assert start is not None
        third = game.combat_round_to(start, 3)
        assert third is not None
        self.assertEqual(game.combat_label(third), "⚔️ Combat, round 3")


class Phrases(unittest.TestCase):
    def test_what_the_dm_plainly_says(self) -> None:
        said = {
            "Roll initiative!": ("start", None),
            "okay, roll for initiative": ("start", None),
            "combat starts": ("start", None),
            "round two": ("round", 2),
            "Round 2.": ("round", 2),
            "it's round three": ("round", 3),
            "next round": ("next", None),
            "new round": ("next", None),
            "combat's over": ("end", None),
            "the fight ends": ("end", None),
            "End of combat.": ("end", None),
        }
        for line, (kind, number) in said.items():
            found = phrases.find_combat(line)
            assert found is not None, line
            self.assertEqual((found.kind, found.number), (kind, number), line)

    def test_questions_wishes_conditions_and_stories_are_refused(self) -> None:
        for line in (
            "do we roll initiative?",
            "if we roll initiative",
            "he will roll initiative when the door opens",
            "let's end combat",
            "the goblin dies next round",
            "when combat starts you hide",
            "is combat over",
            "round 120",
            "round 0",
            "the fight ends badly for him",
            "initiative",
            "I think combat is over",
            "after the fight ends we rest",
            "you don't roll initiative",
        ):
            self.assertIsNone(phrases.find_combat(line), line)


class Wiring(unittest.TestCase):
    def test_the_button_ids_fit_discord_and_match_their_templates(self) -> None:
        cid = "0123456789abcdef0123456789abcdef"
        for action in ("cstart", "cnext", "cend"):
            custom_id = ui.ClockButton(cid, action).item.custom_id
            assert custom_id is not None and len(custom_id) <= 100
            self.assertIsNotNone(
                ui.ClockButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            )
        big = Clock(game.MAX_MINUTE, game.MAX_MINUTE, None, 99, 59)
        spec = ui.combat_undo_id(big, big)
        custom_id = ui.CombatUndoButton(cid, spec).item.custom_id
        assert custom_id is not None and len(custom_id) <= 100
        self.assertIsNotNone(
            ui.CombatUndoButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        )

    def test_the_pinned_buttons_follow_the_fight_and_fit_a_row(self) -> None:
        cid = "0123456789abcdef0123456789abcdef"
        out = ui.clock_view(cid, Clock(NOON4, 0))
        fight = game.combat_start(Clock(NOON4, 0))
        inside = ui.clock_view(cid, fight)
        label = lambda v: [str(cast(Any, i).item.label) for i in v.children]  # noqa: E731
        self.assertIn("Start combat", label(out))
        self.assertNotIn("Next round", label(out))
        self.assertIn("Next round", label(inside))
        self.assertIn("End combat", label(inside))
        self.assertNotIn("Start combat", label(inside))
        for view in (out, inside):
            rows: dict[int, int] = {}
            for item in view.children:
                row = cast(Any, item).item.row
                rows[row] = rows.get(row, 0) + 1
            self.assertTrue(all(n <= 5 for n in rows.values()))

    def test_the_players_see_only_the_round(self) -> None:
        out = ui.players_text(Clock(NOON4, 0))
        self.assertNotIn("Combat", out)
        fight = game.combat_start(Clock(NOON4, 0))
        assert fight is not None
        inside = ui.players_text(fight)
        self.assertIn("⚔️ Combat, round 1", inside)
        self.assertIn(game.short_label(NOON4), inside)

    def test_the_dm_message_has_the_round_too(self) -> None:
        c = game.combat_start(Clock(NOON4, 0))
        assert c is not None
        self.assertIn("⚔️ Combat, round 1", ui.clock_text(c))
        self.assertNotIn("Combat", ui.clock_text(Clock(NOON4, 0)))


class Stored(Clocks):
    async def test_the_buttons_run_a_fight_and_only_dms_may(self) -> None:
        await self.press("set", set_to=NOON4)
        self.assertFalse(await self.clocks.in_combat(GUILD_A, self.a.id))
        for user in (PLAYER, 12345):
            with self.assertRaises(CampaignError):
                await self.press("cstart", user)
        await self.press("cstart")
        self.assertTrue(await self.clocks.in_combat(GUILD_A, self.a.id))
        await self.press("cnext", OTHER_DM)  # a second DM may
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.combat_round, 2)
        with self.assertRaises(CampaignError):
            await self.press("cend", PLAYER)
        result = await self.press("cend")
        self.assertTrue(result.changed)
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual((stored.clock.combat_round, stored.clock.minute), (None, NOON4 + 1))
        self.assertFalse(await self.clocks.in_combat(GUILD_A, self.a.id))

    async def test_a_press_that_changes_nothing_says_so(self) -> None:
        await self.press("set", set_to=NOON4)
        for action in ("cnext", "cend"):
            self.assertFalse((await self.press(action)).changed, action)  # not in combat
        await self.press("cstart")
        self.assertFalse((await self.press("cstart")).changed)  # already started

    async def test_it_survives_a_restart(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        await self.press("cround", set_to=12)  # 66 seconds: a minute and 6 seconds
        again = ClockStore(self.db)  # a new process reads it back
        stored = await again.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(
            (stored.clock.combat_round, stored.clock.minute, stored.clock.combat_secs),
            (12, NOON4 + 1, 6),
        )
        self.assertTrue(await again.in_combat(GUILD_A, self.a.id))

    async def test_a_rest_during_a_fight_keeps_it(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        await self.press("h1")
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.combat_round, 1)

    async def test_campaigns_and_servers_never_share_a_fight(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        self.assertFalse(await self.clocks.in_combat(GUILD_B, self.b.id))
        self.assertFalse(await self.clocks.in_combat(GUILD_B, self.a.id))  # another server

    async def test_a_restored_backup_starts_out_of_combat(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertFalse(await self.clocks.in_combat(GUILD_B, restored.id))

    async def test_undo_goes_back_only_if_nothing_changed_since(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        result = await self.press("cend")
        assert result.before is not None and result.after is not None
        spec = ui.combat_undo_id(result.before, result.after)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=DM),
            client=self.client(),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        interaction.client.post_message = AsyncMock(return_value=None)
        await self.press("m10")  # the clock moved since
        await ui.CombatUndoButton(self.a.id, spec).callback(cast(Any, interaction))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual((stored.clock.combat_round, stored.clock.minute), (None, NOON4 + 11))
        # Straight after the change it works: back in the fight, at the time before.
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        result = await self.press("cend")
        assert result.before is not None and result.after is not None
        spec = ui.combat_undo_id(result.before, result.after)
        await ui.CombatUndoButton(self.a.id, spec).callback(cast(Any, interaction))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual((stored.clock.combat_round, stored.clock.minute), (1, NOON4))

    async def test_undo_lets_the_24_hour_line_be_said_again(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        for _ in range(100):  # each round passes time; force the 24-hour line to be told
            await self.press("h1")
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None and stored.clock.tired_told_for is not None
        told = stored.clock
        result = await self.press("cnext")
        assert result.before is not None and result.after is not None
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=DM),
            client=self.client(),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        interaction.client.post_message = AsyncMock(return_value=None)
        spec = ui.combat_undo_id(result.before, result.after)
        await ui.CombatUndoButton(self.a.id, spec).callback(cast(Any, interaction))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.combat_round, told.combat_round)
        self.assertIsNone(stored.clock.tired_told_for)

    async def test_a_player_cannot_undo(self) -> None:
        await self.press("set", set_to=NOON4)
        result = await self.press("cstart")
        assert result.before is not None and result.after is not None
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=PLAYER),
            client=self.client(),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        spec = ui.combat_undo_id(result.before, result.after)
        await ui.CombatUndoButton(self.a.id, spec).callback(cast(Any, interaction))
        self.assertTrue(await self.clocks.in_combat(GUILD_A, self.a.id))


class Heard(AtTheTable):
    async def combat_round(self) -> int | None:
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        return stored.clock.combat_round

    async def hear(self, user: int, line: str) -> None:
        await self.bot._note_clock_phrase(cast(Any, self.table), user, line)

    async def test_the_dm_runs_a_fight_by_voice_and_each_change_says_so(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(DM, "Roll initiative!")
        self.assertEqual(await self.combat_round(), 1)
        self.assertTrue(any("Combat starts" in text for text in self.posted))
        self.table.clock_said.clear()
        await self.hear(DM, "round three")
        self.assertEqual(await self.combat_round(), 3)
        self.assertTrue(any("Round 3" in text for text in self.posted))
        self.table.clock_said.clear()
        await self.hear(DM, "combat's over")
        self.assertIsNone(await self.combat_round())
        self.assertTrue(any("Combat is over" in text for text in self.posted))

    async def test_a_players_words_never_change_it(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(PLAYER, "roll initiative")
        self.assertIsNone(await self.combat_round())
        await self.press("cstart")
        for line in ("next round", "round five", "combat's over"):
            await self.hear(PLAYER, line)
        self.assertEqual(await self.combat_round(), 1)

    async def test_questions_and_stories_change_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(DM, "should we roll initiative?")
        await self.hear(DM, "the orc will roll initiative when he wakes")
        self.assertIsNone(await self.combat_round())
        await self.press("cstart")
        await self.hear(DM, "the goblin dies next round")
        self.assertEqual(await self.combat_round(), 1)

    async def test_the_same_words_twice_count_once_and_nothing_to_change_posts_nothing(
        self,
    ) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("cstart")
        for _ in range(2):  # said aloud and typed
            await self.hear(DM, "next round")
        self.assertEqual(await self.combat_round(), 2)
        self.posted.clear()
        self.table.clock_said.clear()
        await self.hear(DM, "round two")  # already round two
        self.assertEqual(self.posted, [])

    async def test_out_of_combat_round_words_do_not_start_a_fight(self) -> None:
        await self.press("set", set_to=NOON4)
        for line in ("next round", "round two", "combat's over"):
            self.table.clock_said.clear()
            await self.hear(DM, line)
        self.assertIsNone(await self.combat_round())

    async def test_without_a_clock_nothing_happens(self) -> None:
        await self.hear(DM, "roll initiative")
        self.assertIsNone(await self.clocks.get(GUILD_A, self.a.id))
        self.assertEqual(self.posted, [])


class Setting(Clocks):
    async def test_side_initiative_is_off_by_default_and_the_dm_can_turn_it_on(self) -> None:
        campaign = await self.campaigns.get(GUILD_A, self.a.id)
        assert campaign is not None
        self.assertFalse(campaign.side_initiative)
        saved = await self.campaigns.set_side_initiative(GUILD_A, self.a.id, True)
        self.assertTrue(saved.side_initiative)
        other = await self.campaigns.get(GUILD_B, self.b.id)
        assert other is not None
        self.assertFalse(other.side_initiative)  # another campaign is untouched

    async def test_it_goes_in_a_backup_and_an_old_backup_has_it_off(self) -> None:
        await self.campaigns.set_side_initiative(GUILD_A, self.a.id, True)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        self.assertIs(data["campaign"]["side_initiative"], True)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertTrue(restored.side_initiative)
        del data["campaign"]["side_initiative"]  # made before the setting existed
        older = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertFalse(older.side_initiative)
        data["campaign"]["side_initiative"] = "yes"
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, data, DM)

    def test_the_button_id_matches_its_template(self) -> None:
        from dmbot.dm_screen.settings import SideInitiativeButton

        cid = "0123456789abcdef0123456789abcdef"
        for on in (True, False):
            button = SideInitiativeButton(cid, on)
            self.assertIsNotNone(
                SideInitiativeButton.__discord_ui_compiled_template__.fullmatch(
                    str(button.item.custom_id)
                )
            )
            self.assertLessEqual(len(str(button.item.label)), 25)
            self.assertIsInstance(button.item, discord.ui.Button)
