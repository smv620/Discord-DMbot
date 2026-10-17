"""TimeBot's quiet "where are we" note (#1738): the words (one time, or each group's), the half-hour
rule with a fake clock, anything that shows or moves the clock starting the wait again, and no
note in combat, on Quiet, or without a clock. No database: the clock store is a stand-in."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import levels
from dmbot.timebot import clock as game
from dmbot.timebot import where_are_we as w
from dmbot.timebot.clock import Clock
from dmbot.timebot.split import Group
from dmbot.timebot.store import Stored

START = 1000.0
GAP = 1800.0


def stored(minute: int, **kw: Any) -> Stored:
    return Stored(Clock(minute, 0, **kw), None, None)


def at(day: int, hour: int, minute: int = 0) -> int:
    return game.from_day_and_time(day, hour, minute)


class Words(unittest.TestCase):
    def test_one_time_in_the_clocks_own_words(self) -> None:
        self.assertEqual(
            w.line(stored(at(4, 14, 30))), "🕰️ Where we are: afternoon of Day 4 (14:30)."
        )

    def test_a_split_party_lists_each_group(self) -> None:
        groups = (
            Group(1, "cellar", Clock(at(2, 23, 10), 0)),
            Group(2, "tower", Clock(at(3, 5, 40), 0)),
        )
        both = Stored(Clock(at(2, 23, 10), 0), None, None, groups=groups, picked=1)
        self.assertEqual(
            w.line(both),
            "🕰️ Where we are: the cellar group, night of Day 2 (23:10); "
            "the tower group, dawn of Day 3 (05:40).",
        )

    def test_every_time_of_day_uses_a_word_the_clock_already_has(self) -> None:
        words = {game.day_part(m) for m in range(0, 24 * 60, 5)}
        for m in range(0, 24 * 60, 5):
            self.assertIn(game.day_part(m), w.line(stored(m)))
        self.assertEqual(len(words), 14)

    def test_a_fight_in_any_group_is_a_fight(self) -> None:
        fighting = Clock(at(1, 9), 0, combat_round=2)
        calm = Clock(at(1, 9), 0)
        self.assertTrue(w.in_combat(stored(at(1, 9), combat_round=2)))
        groups = (Group(1, "a", calm), Group(2, "b", fighting))
        self.assertTrue(w.in_combat(Stored(calm, None, None, groups=groups, picked=1)))
        self.assertFalse(w.in_combat(stored(at(1, 9))))


class Levels(unittest.TestCase):
    def test_quiet_none_normal_half_hour_chatty_quarter_hour(self) -> None:
        self.assertIsNone(levels.reminder_gap_s("quiet"))
        self.assertEqual(levels.reminder_gap_s("normal"), 1800.0)
        self.assertEqual(levels.reminder_gap_s("chatty"), 900.0)
        self.assertEqual(levels.reminder_gap_s("surprise"), 1800.0)  # unknown counts as Normal
        self.assertFalse(levels.allows("quiet", levels.REMINDER))
        self.assertTrue(levels.allows("normal", levels.REMINDER))

    def test_due(self) -> None:
        self.assertFalse(w.due("normal", START, START + GAP - 1))
        self.assertTrue(w.due("normal", START, START + GAP))
        self.assertFalse(w.due("quiet", START, START + 10 * GAP))
        self.assertTrue(w.due("chatty", START, START + 900))


class Fake:
    """A bot with one clock per campaign and a list of what it posted."""

    def __init__(self) -> None:
        self.clocks = SimpleNamespace(get=self._get)
        self.campaigns = SimpleNamespace(get=self._campaign)
        self.tables: dict[int, Any] = {}
        self.posted: list[tuple[int, str]] = []
        self.stores: dict[str, Stored | None] = {}

    async def _get(self, guild_id: int, campaign_id: str) -> Stored | None:
        return self.stores.get(campaign_id)

    async def _campaign(self, guild_id: int, campaign_id: str) -> Any:
        return SimpleNamespace(
            id=campaign_id, guild_id=guild_id, dm_screen_channel_id=guild_id * 10
        )

    async def post_message(self, channel_id: int, text: str, view: Any = None) -> Any:
        self.posted.append((channel_id, text))
        return object()

    def table(self, guild: int, campaign: str, **kw: Any) -> Any:
        table = SimpleNamespace(
            guild_id=guild, campaign_id=campaign, listening=True, screen_level="normal",
            clock_shown_at=START, **kw,
        )  # fmt: skip
        self.tables[guild] = table
        return table


def campaign_of(table: Any) -> Any:
    return SimpleNamespace(id=table.campaign_id, guild_id=table.guild_id, dm_screen_channel_id=1)


class Rule(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = Fake()
        self.table = self.bot.table(1, "a" * 32)
        self.bot.stores["a" * 32] = stored(at(4, 14, 30))

    async def tick(self, now: float) -> bool:
        return await w.tick(self.bot, self.table, now)

    async def test_nothing_before_thirty_minutes_one_at_thirty_and_not_again_until_the_next(
        self,
    ) -> None:
        self.assertFalse(await self.tick(START + GAP - 1))
        self.assertTrue(await self.tick(START + GAP))
        self.assertEqual(len(self.bot.posted), 1)
        self.assertIn("afternoon of Day 4 (14:30)", self.bot.posted[0][1])
        # (saying it counted as shown: the wait starts again from now)
        self.table.clock_shown_at = START + GAP
        self.assertFalse(await self.tick(START + GAP + 60))
        self.assertFalse(await self.tick(START + 2 * GAP - 1))
        self.assertTrue(await self.tick(START + 2 * GAP))
        self.assertEqual(len(self.bot.posted), 2)

    async def test_anything_that_shows_or_moves_the_clock_starts_the_wait_again(self) -> None:
        campaign = campaign_of(self.table)
        for text in ("🌅 Dawn.", "😴 A rest."):
            self.table.clock_shown_at = START
            await clock_screen.say(self.bot, campaign, [text])
            self.assertGreater(self.table.clock_shown_at, START)
        self.table.clock_shown_at = START
        w.shown(self.bot, campaign)  # what `show` does on any button, rest or skip
        self.assertGreater(self.table.clock_shown_at, START)
        self.assertFalse(await w.tick(self.bot, self.table, self.table.clock_shown_at + 60))

    async def test_nothing_in_combat_on_quiet_with_no_clock_or_when_not_listening(self) -> None:
        self.bot.stores["a" * 32] = stored(at(4, 14), combat_round=3)
        self.assertFalse(await self.tick(START + 3 * GAP))
        self.bot.stores["a" * 32] = stored(at(4, 14))
        self.table.screen_level = "quiet"
        self.assertFalse(await self.tick(START + 3 * GAP))
        self.table.screen_level = "normal"
        self.bot.stores["a" * 32] = None
        self.assertFalse(await self.tick(START + 3 * GAP))
        self.bot.stores["a" * 32] = stored(at(4, 14))
        self.table.listening = False
        self.assertFalse(await self.tick(START + 3 * GAP))
        self.assertEqual(self.bot.posted, [])

    async def test_chatty_is_fifteen_minutes(self) -> None:
        self.table.screen_level = "chatty"
        self.assertFalse(await self.tick(START + 899))
        self.assertTrue(await self.tick(START + 900))

    async def test_a_split_party_gets_one_line_with_each_group(self) -> None:
        groups = (
            Group(1, "cellar", Clock(at(2, 23, 10), 0)),
            Group(2, "tower", Clock(at(3, 5, 40), 0)),
        )
        self.bot.stores["a" * 32] = Stored(groups[0].clock, None, None, groups=groups, picked=1)
        self.assertTrue(await self.tick(START + GAP))
        (only,) = self.bot.posted
        self.assertIn("the cellar group", only[1])
        self.assertIn("the tower group", only[1])

    async def test_two_campaigns_never_hold_each_other_up(self) -> None:
        other = self.bot.table(2, "b" * 32)
        self.bot.stores["b" * 32] = stored(at(1, 8))
        w.shown(self.bot, campaign_of(self.table))  # campaign A's clock was shown just now
        self.assertGreater(self.table.clock_shown_at, START)
        self.assertEqual(other.clock_shown_at, START)  # B's wait is its own
        self.assertTrue(await w.tick(self.bot, other, START + GAP))
        self.assertEqual([c for c, _ in self.bot.posted], [20])  # B's own DM screen only
        wrong = SimpleNamespace(id="c" * 32, guild_id=1, dm_screen_channel_id=1)
        before = self.bot.tables[1].clock_shown_at
        w.shown(self.bot, wrong)  # a different campaign in the same server: not this table
        self.assertEqual(self.bot.tables[1].clock_shown_at, before)

    async def test_only_the_dm_screen_hears_it(self) -> None:
        await self.tick(START + GAP)
        self.assertEqual([c for c, _ in self.bot.posted], [10])
