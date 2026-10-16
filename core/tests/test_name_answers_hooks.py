"""The bot's side of "a name, then an answer" (#1188): spoken and typed lines pair on one clock,
and a DM's question never shows a character name. Made-up names, no database."""

from __future__ import annotations

import time
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from tests.test_sidebar_bot import DM, GUILD, PLAYER, make_bot, make_table


class Pairing(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = make_bot()
        self.table = make_table()
        self.table.listening = True
        self.bot.tables[GUILD] = self.table
        self.bot.sidebar.ask_at_table = MagicMock()  # type: ignore[method-assign]

    def pair(self, gap_ms: int) -> int:
        """A spoken call, then a typed answer `gap_ms` later, twice; how many pairs counted."""
        for start in (1_000_000, 2_000_000):
            self.table.name_answers.feed(DM, start, "Mike, what do you do?", consented=True)
            self.bot._deliver_typed(self.table, PLAYER, "I draw my sword", start + gap_ms)
        found = self.table.name_answers.answered(minimum=1)
        return found[0].times if found else 0

    def test_a_typed_answer_nine_seconds_after_a_spoken_call_counts(self) -> None:
        self.assertEqual(self.pair(9_000), 2)

    def test_eleven_seconds_after_does_not(self) -> None:
        self.assertEqual(self.pair(11_000), 0)

    def test_a_typed_call_and_a_spoken_answer_pair_too(self) -> None:
        self.bot._deliver_typed(self.table, DM, "Mike, what do you do?", 5_000_000)
        self.table.name_answers.feed(PLAYER, 5_004_000, "I run with my sword", consented=True)
        self.assertEqual([a.name for a in self.table.name_answers.answered(minimum=1)], ["Mike"])


class TheDmIsKnown(unittest.TestCase):
    """The learner is told who the DMs are before it reads a line (#1547)."""

    def test_a_dm_word_and_a_dms_own_name_count_for_the_dm_who_spoke(self) -> None:
        bot = make_bot()
        table = make_table()
        table.listening = True
        table.dm_user_id, table.dm_user_ids = DM, frozenset({DM})
        bot.tables[GUILD] = table
        bot.sidebar.ask_at_table = MagicMock()  # type: ignore[method-assign]
        cast(Any, bot).name_of = lambda guild_id, user_id: {DM: "Ana", PLAYER: "Mia"}[user_id]
        bot._deliver_typed(table, DM, "Go ahead.", 1_000_000)
        bot._deliver_typed(table, PLAYER, "DM, can I roll?", 1_002_000)
        bot._deliver_typed(table, PLAYER, "Ana, can I roll again?", 1_004_000)
        self.assertEqual(table.name_answers.dm_addressed(), {DM: 2})
        self.assertEqual(table.name_answers.answered(minimum=1), [])  # nothing to learn from it


class TheSidebarsDm(unittest.IsolatedAsyncioTestCase):
    """The sidebar's "DM, GM… all mean <name>" line names the DM the learner resolves, with the
    session's starter as the fallback (#1558)."""

    CO_DM = 77

    async def line(self, *, spoke: dict[int, int], agreed: set[int]) -> list[str]:
        bot = make_bot()
        table = make_table()
        table.listening = True
        table.dm_user_id, table.dm_user_ids = DM, frozenset({DM, self.CO_DM})
        bot.tables[GUILD] = table
        campaign = MagicMock(guild_id=GUILD, id="c1", dm_user_ids=table.dm_user_ids)
        memory: Any = MagicMock()
        memory.table_people = AsyncMock(return_value=[])
        bot.memory = memory
        cast(Any, bot.consent).consenting = AsyncMock(return_value=agreed)
        names = {DM: "Ana", self.CO_DM: "Kim", PLAYER: "Mia"}
        cast(Any, bot).name_of = lambda guild_id, user_id: names[user_id]
        table.name_answers.set_dms({DM: "Ana", self.CO_DM: "Kim"})
        now = int(time.time() * 1000)
        for user, ago_ms in spoke.items():
            table.name_answers.feed(user, now - ago_ms, "Hm.", consented=True)
        return await bot.sidebar_people(campaign)

    async def test_a_lone_dm_who_spoke_is_the_one_even_if_not_the_starter(self) -> None:
        got = await self.line(spoke={self.CO_DM: 1000}, agreed={DM, self.CO_DM})
        self.assertEqual(got, ["DM, GM, Dungeon Master and Game Master all mean Kim"])

    async def test_with_two_dms_the_last_speaker_in_the_window_is_the_one(self) -> None:
        got = await self.line(spoke={DM: 5000, self.CO_DM: 1000}, agreed={DM, self.CO_DM})
        self.assertIn("all mean Kim", got[0])
        got = await self.line(spoke={DM: 1000, self.CO_DM: 5000}, agreed={DM, self.CO_DM})
        self.assertIn("all mean Ana", got[0])

    async def test_nobody_has_spoken_means_the_starter(self) -> None:
        got = await self.line(spoke={}, agreed={DM})
        self.assertIn("all mean Ana", got[0])

    async def test_a_resolved_dm_who_stopped_recording_falls_back_to_the_starter_or_no_line(
        self,
    ) -> None:
        got = await self.line(spoke={self.CO_DM: 1000}, agreed={DM})
        self.assertIn("all mean Ana", got[0])
        self.assertEqual(await self.line(spoke={self.CO_DM: 1000}, agreed=set()), [])


class TheQuestionersName(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_keeps_the_nickname_a_player_gets_their_character(self) -> None:
        bot = make_bot()
        table = make_table()
        table.campaign_id = "c" * 32
        bot.sheets = None
        memory: Any = MagicMock()
        memory.table_people = AsyncMock(
            return_value=[(DM, ["Strahd"], []), (PLAYER, ["Cerric"], [])]
        )
        bot.memory = memory
        names = {DM: "Ana", PLAYER: "Mia"}
        cast(Any, bot).name_of = lambda guild_id, user_id: names[user_id]
        self.assertEqual(await bot.sidebar_table_name(table, DM), "Ana")
        self.assertEqual(await bot.sidebar_table_name(table, PLAYER), "Cerric")


if __name__ == "__main__":
    unittest.main()
