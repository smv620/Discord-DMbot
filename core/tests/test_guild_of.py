"""A campaign's server is found from the database, so a button in a private chat survives a
restart (#3210). Pure stand-ins: no database needed."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from dmbot.campaigns.store import CampaignStore


class GuildOfTest(unittest.IsolatedAsyncioTestCase):
    async def test_finds_the_server_that_holds_the_campaign(self) -> None:
        async def guilds() -> list[int]:
            return [1, 2, 3]

        async def has(guild_id: int, campaign_id: str) -> bool:
            return guild_id == 2 and campaign_id == "abc"

        me = SimpleNamespace(guilds_with_campaigns=guilds, has_campaign=has)
        self.assertEqual(await CampaignStore.guild_of(cast(Any, me), "abc"), 2)

    async def test_unknown_campaign_has_no_server(self) -> None:
        async def guilds() -> list[int]:
            return [1]

        async def has(guild_id: int, campaign_id: str) -> bool:
            return False

        me = SimpleNamespace(guilds_with_campaigns=guilds, has_campaign=has)
        self.assertIsNone(await CampaignStore.guild_of(cast(Any, me), "nope"))


class AskPrivatelyTest(unittest.IsolatedAsyncioTestCase):
    """`DMBot.ask_privately` (#3210): at least one DM getting it is the contract, and it never
    raises."""

    def bot(self, dms: set[int], results: dict[int, Any]) -> Any:
        from unittest.mock import AsyncMock

        campaign = SimpleNamespace(name="The *Test* Table", dm_user_ids=dms)
        sent: list[tuple[int, str]] = []

        async def send_dm(user_id: int, text: str, view: Any = None) -> str:
            sent.append((user_id, text))
            outcome = results[user_id]
            if isinstance(outcome, Exception):
                raise outcome
            return str(outcome)

        me = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            sidebar_send_dm=send_dm,
            sent=sent,
        )
        return me

    async def ask(self, me: Any) -> bool:
        from dmbot.bot import DMBot

        return await DMBot.ask_privately(cast(Any, me), 1, "abc", "Question?", cast(Any, None))

    async def test_each_dm_gets_the_marked_question_and_one_success_is_enough(self) -> None:
        me = self.bot({7, 8}, {7: "sent", 8: "forbidden"})
        self.assertTrue(await self.ask(me))
        self.assertEqual([u for u, _ in me.sent], [7, 8])  # in order, none skipped
        self.assertTrue(me.sent[0][1].startswith("❓ **DMbot has a question** · "))
        self.assertIn("\\*Test\\*", me.sent[0][1])  # the campaign's name cannot format the line

    async def test_all_closed_means_false_so_the_screen_keeps_the_question(self) -> None:
        me = self.bot({7}, {7: "forbidden"})
        self.assertFalse(await self.ask(me))

    async def test_a_failing_dm_does_not_stop_the_others_or_undo_a_send(self) -> None:
        me = self.bot({7, 8}, {7: RuntimeError("boom"), 8: "sent"})
        with self.assertLogs("dmbot.bot", level="ERROR"):
            self.assertTrue(await self.ask(me))
        self.assertEqual([u for u, _ in me.sent], [7, 8])

    async def test_no_campaign_means_false(self) -> None:
        from unittest.mock import AsyncMock

        me = self.bot({7}, {7: "sent"})
        me.campaigns.get = AsyncMock(return_value=None)
        self.assertFalse(await self.ask(me))
        self.assertEqual(me.sent, [])


if __name__ == "__main__":
    unittest.main()
