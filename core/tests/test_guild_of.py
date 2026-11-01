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


if __name__ == "__main__":
    unittest.main()
