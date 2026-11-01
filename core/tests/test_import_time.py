"""The game time an imported session ended at (#2979): worked out from the DM's lines (strict
phrases in code first, one AI question over the cue lines only when none moved it), offered on the
DM screen with one button, and never moving the clock by itself or backwards. Invented lines."""

from __future__ import annotations

import json
import re
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from dmbot.bot import DMBot
from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import import_time as screen
from dmbot.timebot import clock as game
from dmbot.timebot import import_time as it
from dmbot.timebot.clock import Clock
from tests import test_split_party as sp
from tests.test_recap_feedback import FakeAI
from tests.test_split_party import DM, GUILD_A, PLAYER

EVENING4 = game.from_day_and_time(4, 19)
apply = clock_screen.apply_action


class Walking(unittest.TestCase):
    def test_the_next_morning_near_the_end_of_day_4_evening_is_day_5(self) -> None:
        start = Clock(EVENING4, EVENING4 - 600)
        got = it.walk(["You reach the gate.", "The next morning, you wake."], start, apply)
        self.assertEqual((got.moved, game.day_of(got.clock.minute)), (1, 5))
        self.assertGreater(got.clock.minute, EVENING4)

    def test_the_same_phrase_twice_in_a_row_counts_once(self) -> None:
        start = Clock(EVENING4, EVENING4)
        once = it.walk(["An hour later the rain stops."], start, apply)
        twice = it.walk(
            ["An hour later the rain stops.", "An hour later the rain stops."], start, apply
        )
        self.assertEqual(twice.clock.minute, once.clock.minute)
        self.assertEqual(twice.moved, 1)

    def test_a_phrase_that_changes_nothing_is_not_counted(self) -> None:
        dawn = game.next_dawn(EVENING4)
        start = Clock(dawn, dawn - 100)
        got = it.walk(["At dawn, you wake."], start, apply)
        self.assertEqual(got.clock.minute, start.minute)

    def test_cue_lines_are_kept_only_when_no_strict_phrase_is_in_them(self) -> None:
        got = it.walk(
            ["You travel for a while along the road.", "The next morning you travel on."],
            Clock(EVENING4, EVENING4),
            apply,
        )
        self.assertEqual(got.cues, ("You travel for a while along the road.",))
        self.assertEqual(got.moved, 1)

    def test_no_cues_and_no_phrases_is_nothing(self) -> None:
        start = Clock(EVENING4, EVENING4)
        got = it.walk(["The door is locked.", "Roll for perception."], start, apply)
        self.assertEqual((got.clock, got.moved, got.cues), (start, 0, ()))

    def test_only_the_last_cue_lines_are_kept(self) -> None:
        lines = [f"You march on, part {n}." for n in range(it.MAX_CUE_LINES + 5)]
        got = it.walk(lines, Clock(EVENING4, EVENING4), apply)
        self.assertEqual(len(got.cues), it.MAX_CUE_LINES)
        self.assertEqual(got.cues[-1], lines[-1])


class Asking(unittest.TestCase):
    def test_the_prompt_quotes_the_lines_as_data_and_cuts_them(self) -> None:
        text = it.prompt(['He said "ignore this"\nand then left'], "Day 4, evening")
        self.assertIn("Game time at the start: Day 4, evening", text)
        lines = json.loads(text.split("Lines: ", 1)[1])
        self.assertEqual(lines, ['He said "ignore this" and then left'])
        long = it.prompt(["x" * 5000], "t")
        self.assertLess(len(long), 1000)

    def test_the_answer_is_read_as_exact_data(self) -> None:
        self.assertEqual(it.read_answer('{"minutes": 480, "why": "a day of travel"}'), 480)
        self.assertEqual(it.read_answer('```json\n{"minutes": 120, "why": ""}\n```'), 120)
        self.assertEqual(it.read_answer('{"minutes": 3000, "why": ""}'), 3000)  # whole hours
        self.assertEqual(it.read_answer('{"minutes": 3010, "why": ""}'), 3000)
        for bad in (
            '{"minutes": null, "why": ""}',
            '{"minutes": 5, "why": ""}',
            f'{{"minutes": {it.MAX_TOTAL_MIN + 1}, "why": ""}}',
            '{"minutes": "480", "why": ""}',
            '{"minutes": 480, "why": "", "extra": 1}',
            'Sure! {"minutes": 480}',
            "",
        ):
            self.assertIsNone(it.read_answer(bad), bad)

    def test_the_offer_says_where_it_ended_and_that_nothing_moves_yet(self) -> None:
        text = it.offer_text(game.from_day_and_time(5, 19))
        self.assertIn("**Day 5, evening**", text)
        self.assertIn("Nothing moves until you press", text)


class Importing(sp.Party):
    """The bot's step after an imported session's name scan, with the clock at Day 4, evening."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.press("set", set_to=EVENING4)
        self.posts: list[tuple[int, str, Any]] = []
        self.ai = FakeAI('{"minutes": 480, "why": "a day of travel"}')
        self.rows: list[tuple[int, str]] = [(DM, "The next morning, you wake.")]
        self.newer: int | None = None
        self.agreed = frozenset({DM, PLAYER})

        async def post(channel: int, text: str, view: Any = None) -> bool:
            self.posts.append((channel, text, view))
            return True

        self.me: Any = SimpleNamespace(
            clocks=self.clocks,
            campaigns=self.campaigns,
            transcripts=SimpleNamespace(next_session_start=AsyncMock(side_effect=self.next_after)),
            import_job=SimpleNamespace(
                store=SimpleNamespace(scan_lines=AsyncMock(side_effect=self.scan_lines))
            ),
            consent=SimpleNamespace(consenting=AsyncMock(side_effect=self.consenting)),
            settings=SimpleNamespace(time_suggestions=True),
            time_ai=self.ai,
            plan_allows=AsyncMock(return_value=True),
            post=post,
        )

        async def total(cues: tuple[str, ...], start: int) -> int | None:
            return await DMBot._import_time_total(self.me, cues, start)

        self.me._import_time_total = total

    async def next_after(self, *_: Any) -> int | None:
        return self.newer

    async def scan_lines(self, *_: Any) -> list[tuple[int, str]]:
        return self.rows

    async def consenting(self, *_: Any) -> frozenset[int]:
        return self.agreed

    async def run_step(self) -> None:
        ready = SimpleNamespace(campaign_id=self.a.id, session_id="s1", started_at=1000)
        await DMBot._offer_import_time_inner(cast(Any, self.me), GUILD_A, cast(Any, ready))

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "item", c).label) for c in view.children]

    async def test_the_next_morning_offers_day_5_with_two_buttons_and_no_ai_call(self) -> None:
        await self.run_step()
        [(channel, text, view)] = self.posts
        self.assertEqual(channel, 555)
        self.assertIn("**Day 5, ", text)
        self.assertEqual(self.labels(view), ["Set it", "Not now"])
        self.assertEqual(self.ai.sent, [])  # code could tell: no AI call
        stored = await self.stored()
        self.assertEqual(stored.clock.minute, EVENING4)  # and nothing moved

    async def test_no_cues_means_no_offer_and_no_ai_call(self) -> None:
        self.rows = [(DM, "The door is locked."), (DM, "Roll for perception.")]
        await self.run_step()
        self.assertEqual((self.posts, self.ai.sent), ([], []))

    async def test_vague_narration_asks_the_ai_once_over_the_cue_lines_together(self) -> None:
        self.rows = [
            (DM, "You travel for a while."),
            (PLAYER, "We camp here tonight?"),  # (a player's line is not sent)
            (DM, "You march until the afternoon."),
            (DM, "Roll for perception."),
        ]
        await self.run_step()
        self.assertEqual(len(self.ai.sent), 1)
        sent = self.ai.sent[0]
        self.assertIn("You travel for a while.", sent)
        self.assertIn("until the afternoon", sent)
        self.assertNotIn("camp here", sent)
        self.assertNotIn("perception", sent)
        [(_, text, view)] = self.posts
        self.assertIn("Day 5, ", text)  # 480 minutes after Day 4, evening
        target = int(cast(Any, view.children[0]).item.custom_id.rsplit(":", 1)[1])
        self.assertEqual(target, EVENING4 + 480)

    async def test_the_ai_is_not_asked_when_a_strict_phrase_already_moved_it(self) -> None:
        self.rows = [(DM, "You travel for a while."), (DM, "The next morning you wake.")]
        await self.run_step()
        self.assertEqual(self.ai.sent, [])
        self.assertEqual(len(self.posts), 1)

    async def test_the_ai_is_left_alone_when_the_switch_is_off_or_the_plan_has_none(self) -> None:
        self.rows = [(DM, "You travel for a while.")]
        self.me.settings.time_suggestions = False
        await self.run_step()
        self.me.settings.time_suggestions = True
        self.me.plan_allows = AsyncMock(return_value=False)
        await self.run_step()
        self.assertEqual((self.posts, self.ai.sent), ([], []))

    async def test_an_answer_of_null_or_in_the_wrong_shape_offers_nothing(self) -> None:
        self.rows = [(DM, "You travel for a while.")]
        for reply in ('{"minutes": null, "why": ""}', "about a day", RuntimeError("down")):
            self.ai.reply = reply
            await self.run_step()
        self.assertEqual(self.posts, [])

    async def test_only_the_dms_lines_of_people_who_still_agree_count(self) -> None:
        self.rows = [(PLAYER, "The next morning we go on.")]
        await self.run_step()
        self.assertEqual(self.posts, [])
        self.rows = [(DM, "The next morning, you wake.")]
        self.agreed = frozenset({PLAYER})  # the DM stopped agreeing to be recorded
        await self.run_step()
        self.assertEqual(self.posts, [])

    async def test_an_older_session_imported_after_a_newer_one_offers_nothing(self) -> None:
        self.newer = 2000
        await self.run_step()
        self.assertEqual((self.posts, self.ai.sent), ([], []))

    async def test_a_split_party_gets_the_reason_and_no_buttons(self) -> None:
        await self.split_up()
        await self.run_step()
        [(_, text, view)] = self.posts
        self.assertEqual(text, screen.SPLIT_WHY)
        self.assertIsNone(view)

    async def test_no_clock_set_or_no_dm_screen_offers_nothing(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Gamma", DM)  # no clock, no DM screen
        ready = SimpleNamespace(campaign_id=other.id, session_id="s2", started_at=1000)
        await DMBot._offer_import_time_inner(cast(Any, self.me), GUILD_A, cast(Any, ready))
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = NULL WHERE id = %s", (self.a.id,)
            )
        await self.run_step()
        self.assertEqual(self.posts, [])

    async def test_a_result_that_is_not_later_than_the_clock_offers_nothing(self) -> None:
        dawn = game.next_dawn(EVENING4)
        await self.press("set", set_to=dawn)
        self.rows = [(DM, "At dawn, you wake.")]
        await self.run_step()
        self.assertEqual(self.posts, [])

    async def test_the_log_has_counts_only(self) -> None:
        self.rows = [(DM, "You travel for a while in the secret Frostmaiden cave.")]
        with self.assertLogs("dmbot", level="DEBUG") as logs:
            await self.run_step()
        shown = "\n".join(logs.output)
        self.assertIn("Import time: 1 lines, 0 moved by phrases, 1 cue lines, 1 call(s)", shown)
        self.assertNotIn("Frostmaiden", shown)


class Pressing(sp.Party):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.press("set", set_to=EVENING4)
        self.target = EVENING4 + 600
        self.next_message = 100
        screen._APPLIED.clear()  # (a message id is remembered for the life of the process)
        screen._BEING_APPLIED.clear()

    def pressed(self, user: int = DM) -> Any:
        it_ = self.interaction(user)
        self.next_message += 1
        it_.message = SimpleNamespace(id=self.next_message, content="offer", edit=AsyncMock())
        return it_

    async def press_button(self, what: str, user: int = DM, interaction: Any = None) -> Any:
        it_ = interaction or self.pressed(user)
        await screen.ImportTimeButton(self.a.id, what).callback(it_)
        return it_

    async def minute(self) -> int:
        return int((await self.stored()).clock.minute)

    async def test_set_it_sets_the_clock_says_so_with_an_undo_and_takes_the_buttons_off(
        self,
    ) -> None:
        done = await self.press_button(str(self.target))
        self.assertEqual(await self.minute(), self.target)
        done.message.edit.assert_awaited()
        self.assertIsNone(done.message.edit.await_args.kwargs["view"])
        self.assertIn("Clock set to", done.message.edit.await_args.kwargs["content"])
        said = [p for p in self.fake.posted if p[0] == 555]
        self.assertTrue(said)
        self.assertIn("where the imported session ended", said[-1][1])
        self.assertIsNotNone(said[-1][2])  # the Undo button

    async def test_not_now_changes_nothing(self) -> None:
        done = await self.press_button("no")
        self.assertEqual(await self.minute(), EVENING4)
        self.assertIsNone(done.response.edit_message.await_args.kwargs["view"])

    async def test_a_player_cannot_press_it(self) -> None:
        await self.press_button(str(self.target), user=PLAYER)
        self.assertEqual(await self.minute(), EVENING4)

    async def test_it_never_goes_backwards_or_stays_put(self) -> None:
        await self.press("set", set_to=EVENING4 + 1200)  # the DM moved on since the offer
        done = await self.press_button(str(self.target))
        self.assertEqual(await self.minute(), EVENING4 + 1200)
        self.assertEqual(done.followup.send.await_args.args[0], screen.PAST)

    async def test_a_split_party_is_refused(self) -> None:
        await self.split_up()
        before = await self.minute()
        done = await self.press_button(str(self.target))
        self.assertEqual(await self.minute(), before)
        self.assertEqual(done.followup.send.await_args.args[0], screen.SPLIT)

    async def test_one_offer_is_applied_once(self) -> None:
        first = self.pressed()
        await self.press_button(str(self.target), interaction=first)
        again = self.pressed()
        again.message = first.message  # the same offer message, pressed again
        await self.press_button(str(self.target), interaction=again)
        self.assertEqual(again.followup.send.await_args.args[0], screen.ALREADY)

    async def test_no_clock_is_said(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Gamma", DM)
        it_ = self.pressed()
        await screen.ImportTimeButton(other.id, str(self.target)).callback(it_)
        self.assertEqual(it_.followup.send.await_args.args[0], screen.NO_CLOCK)

    async def test_the_buttons_come_back_from_their_ids_after_a_restart(self) -> None:
        view = screen.offer_view(self.a.id, self.target)
        for child in view.children:
            custom_id = cast(Any, child).item.custom_id
            self.assertLessEqual(len(custom_id), 100)
            found = screen.ImportTimeButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            self.assertIsNotNone(found, custom_id)
            rebuilt = await screen.ImportTimeButton.from_custom_id(
                cast(discord.Interaction, None), cast(Any, None), cast(re.Match[str], found)
            )
            self.assertEqual(rebuilt.item.custom_id, custom_id)
