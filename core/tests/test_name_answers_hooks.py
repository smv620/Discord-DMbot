"""The bot's side of "a name, then an answer" (#1188): spoken and typed lines pair on one clock,
and a DM's question never shows a character name. Made-up names, no database."""

from __future__ import annotations

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
