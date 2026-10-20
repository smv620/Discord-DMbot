"""The bot writes its own side of `installs` (#2163): a row on joining, `left_at` on leaving."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.install_record import InstallStore
from tests.pg import DatabaseTest

G, OTHER = 5001, 5002


class Rows(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1000.0
        self.store = InstallStore(self.db, clock=lambda: self.now)

    async def row(self, guild: int) -> Any:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT installed_by_user_id, installed_at, via, left_at FROM installs"
                " WHERE guild_id = %s",
                (guild,),
            )
            return await cur.fetchone()

    async def test_joining_makes_a_row_with_no_installer(self) -> None:
        await self.store.joined(G)
        row = await self.row(G)
        self.assertEqual(
            (row["installed_by_user_id"], row["installed_at"], row["via"], row["left_at"]),
            (None, 1000, "link", None),
        )

    async def test_leaving_sets_left_at_once_and_keeps_the_row(self) -> None:
        await self.store.joined(G)
        self.now = 2000.0
        await self.store.left(G)
        self.now = 3000.0
        await self.store.left(G)  # a second removal changes nothing
        row = await self.row(G)
        self.assertEqual((row["left_at"], row["installed_at"]), (2000, 1000))

    async def test_a_rejoin_clears_left_at_and_keeps_the_first_date(self) -> None:
        await self.store.joined(G)
        await self.store.left(G)
        self.now = 4000.0
        await self.store.joined(G)
        row = await self.row(G)
        self.assertEqual((row["left_at"], row["installed_at"]), (None, 1000))

    async def test_leaving_a_server_never_seen_writes_nothing(self) -> None:
        await self.store.left(G)
        self.assertIsNone(await self.row(G))

    async def test_a_row_the_website_made_keeps_its_installer_and_way(self) -> None:
        await self.store.joined(G)
        async with self.db.guild(G) as conn:  # the website later fills in who added it
            await conn.execute("UPDATE installs SET via = 'site' WHERE guild_id = %s", (G,))
        await self.store.joined(G)  # a restart
        row = await self.row(G)
        self.assertEqual(row["via"], "site")  # never overwritten here

    async def test_each_server_has_its_own_row(self) -> None:
        await self.store.joined(G)
        await self.store.joined(OTHER)
        await self.store.left(G)
        self.assertIsNotNone((await self.row(G))["left_at"])
        self.assertIsNone((await self.row(OTHER))["left_at"])

    async def test_the_start_up_pass_records_every_server_and_survives_a_failure(self) -> None:
        self.assertEqual(await self.store.present([G, OTHER]), 2)
        self.assertIsNotNone(await self.row(G))
        broken = InstallStore(cast(Any, SimpleNamespace(guild=MagicMock(side_effect=RuntimeError))))
        self.assertEqual(await broken.present([G, OTHER]), 0)  # logged, never raised


class TheHooks(unittest.IsolatedAsyncioTestCase):
    def bot(self) -> Any:
        installs = SimpleNamespace(
            joined=AsyncMock(), left=AsyncMock(), present=AsyncMock(return_value=2)
        )
        tracked: list[str] = []

        def track(coro: Any, name: str) -> None:
            tracked.append(name)
            coro.close()

        return SimpleNamespace(
            installs=installs,
            _track=track,
            tracked=tracked,
            site_offers=SimpleNamespace(sweep_server=AsyncMock()),
        )

    async def test_leaving_is_recorded(self) -> None:
        bot = self.bot()
        await DMBot.on_guild_remove(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["install-left"])
        bot.installs.left.assert_called_once_with(G)

    async def test_without_a_store_nothing_happens(self) -> None:
        bot = self.bot()
        bot.installs = None
        await DMBot.on_guild_remove(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, [])

    async def test_joining_is_recorded_after_the_welcome(self) -> None:
        from unittest import mock

        bot = self.bot()
        with mock.patch("dmbot.bot.install.post_welcome", AsyncMock(return_value="welcomed")):
            await DMBot.on_guild_join(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["offer-sweep", "install-joined"])
        bot.installs.joined.assert_called_once_with(G)

    async def test_the_start_up_pass_covers_every_server_the_bot_is_in(self) -> None:
        bot = self.bot()
        bot._resume_started = False
        bot.guilds = [SimpleNamespace(id=G, me=None), SimpleNamespace(id=OTHER, me=None)]
        bot._resume_with_retries = AsyncMock()
        bot._rebuild_rules_archives = AsyncMock()
        bot._rebuild_npcs = AsyncMock()
        bot._background = []
        await DMBot.on_ready(bot)
        for task in bot._background:
            await task
        self.assertIn("installs", bot.tracked)
        bot.installs.present.assert_called_once_with([G, OTHER])
