"""The bot writes its own side of `installs` (#2163): a row on joining, `left_at` on leaving."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock
from unittest.mock import AsyncMock

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
        async with self.db.user(9) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (9, 1, 1)"
                " ON CONFLICT DO NOTHING"
            )
        async with self.db.guild(G) as conn:  # as the website's install writes it
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, 9, 500, 'site')",
                (G,),
            )
        for step in (self.store.joined, self.store.left, self.store.joined):
            self.now += 100
            await step(G)  # a restart, a removal, a rejoin
            row = await self.row(G)
            self.assertEqual(
                (row["installed_by_user_id"], row["installed_at"], row["via"]), (9, 500, "site")
            )
        self.assertIsNone((await self.row(G))["left_at"])

    async def test_each_server_has_its_own_row(self) -> None:
        await self.store.joined(G)
        await self.store.joined(OTHER)
        await self.store.left(G)
        self.assertIsNotNone((await self.row(G))["left_at"])
        self.assertIsNone((await self.row(OTHER))["left_at"])

    async def test_the_start_up_pass_records_every_server_and_survives_a_failure(self) -> None:
        self.assertEqual(await self.store.present([G, OTHER]), 2)
        self.assertIsNotNone(await self.row(G))
        broken = InstallStore(self.db)
        real, calls = broken.joined, []

        async def first_fails(guild_id: int) -> None:
            calls.append(guild_id)
            if len(calls) == 1:
                raise RuntimeError("database error with a row in it")
            await real(guild_id)

        with mock.patch.object(broken, "joined", first_fails):
            self.assertEqual(await broken.present([G + 10, OTHER + 10]), 1)
        self.assertEqual(calls, [G + 10, OTHER + 10])  # the second was still tried
        self.assertIsNotNone(await self.row(OTHER + 10))


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
        bot.get_guild = lambda gid: None
        bot._note_left = lambda gid: DMBot._note_left(bot, gid)
        await DMBot.on_guild_remove(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["install-left"])
        await DMBot._note_left(bot, G)
        bot.installs.left.assert_called_once_with(G)

    async def test_a_kick_and_a_quick_re_add_does_not_leave_the_server_marked_left(self) -> None:
        bot = self.bot()
        bot.get_guild = lambda gid: SimpleNamespace(id=gid)  # it is back already
        await DMBot._note_left(bot, G)
        bot.installs.left.assert_not_called()

    def test_an_outage_is_not_a_removal(self) -> None:
        # Discord's own "unavailable" event is a different one, and nothing here handles it by
        # writing left_at (a server that is only unavailable is still ours).
        self.assertFalse(hasattr(DMBot, "on_guild_unavailable"))

    async def test_without_a_store_nothing_happens(self) -> None:
        bot = self.bot()
        bot.installs = None
        await DMBot.on_guild_remove(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, [])

    async def test_joining_is_recorded_even_when_the_welcome_fails(self) -> None:
        bot = self.bot()
        with (
            mock.patch("dmbot.bot.install.post_welcome", AsyncMock(side_effect=RuntimeError)),
            self.assertRaises(RuntimeError),
        ):
            await DMBot.on_guild_join(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["install-joined"])  # queued before the welcome
        bot.installs.joined.assert_called_once_with(G)

    async def test_joining_is_recorded_and_the_welcome_still_goes(self) -> None:
        bot = self.bot()
        with mock.patch("dmbot.bot.install.post_welcome", AsyncMock(return_value="welcomed")):
            await DMBot.on_guild_join(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["install-joined", "offer-sweep"])

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
