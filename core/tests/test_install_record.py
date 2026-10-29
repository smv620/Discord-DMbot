"""The bot writes its own side of `installs` (#2163): a row on joining, `left_at` on leaving."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock
from unittest.mock import AsyncMock

import discord

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
        self.assertEqual(await self.store.present([(G, None), (OTHER, 700)]), 2)
        self.assertIsNotNone(await self.row(G))
        broken = InstallStore(self.db)
        real, calls = broken.joined, []

        async def first_fails(guild_id: int, at: int | None = None, gone: Any = None) -> None:
            calls.append(guild_id)
            if len(calls) == 1:
                raise RuntimeError("database error with a row in it")
            await real(guild_id, at, gone=gone)

        with mock.patch.object(broken, "joined", first_fails):
            self.assertEqual(await broken.present([(G + 10, None), (OTHER + 10, None)]), 1)
        self.assertEqual(calls, [G + 10, OTHER + 10])  # the second was still tried
        self.assertIsNotNone(await self.row(OTHER + 10))

    async def test_a_row_made_at_start_up_carries_the_real_join_date(self) -> None:
        await self.store.present([(G, 700), (OTHER, None)])
        self.assertEqual((await self.row(G))["installed_at"], 700)  # Discord's date
        self.assertEqual((await self.row(OTHER))["installed_at"], 1000)  # no date known: now
        await self.store.present([(G, 900)])  # and a later pass never moves it
        self.assertEqual((await self.row(G))["installed_at"], 700)

    async def test_servers_left_while_offline_are_marked_and_the_rest_left_alone(self) -> None:
        third = OTHER + 1
        for guild in (G, OTHER, third):
            await self.store.joined(guild)
        self.now = 2000.0
        await self.store.left(third)  # already marked, by an event
        self.now = 3000.0
        self.assertEqual(await self.store.left_except([G]), 1)  # only OTHER is newly marked
        self.assertIsNone((await self.row(G))["left_at"])
        marked_at = (await self.row(OTHER))["left_at"]
        self.assertGreater(marked_at, 1_700_000_000)  # the database's own clock
        self.assertEqual((await self.row(third))["left_at"], 2000)  # the first date stays
        self.assertEqual(await self.store.left_except([G]), 0)  # and a second pass changes nothing

    async def test_the_sweep_keeps_who_added_the_bot_and_has_no_other_effect(self) -> None:
        async with self.db.user(9) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (9, 1, 1)"
                " ON CONFLICT DO NOTHING"
            )
        async with self.db.guild(G) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, 9, 500, 'site')",
                (G,),
            )
        await self.store.left_except([OTHER])
        row = await self.row(G)
        self.assertEqual(
            (row["installed_by_user_id"], row["installed_at"], row["via"], row["left_at"]),
            (9, 500, "site", row["left_at"]),
        )
        self.assertGreater(row["left_at"], 1_700_000_000)

    async def test_an_empty_list_marks_nothing(self) -> None:
        await self.store.joined(G)
        self.assertEqual(await self.store.left_except([]), 0)
        async with self.db.unscoped() as conn:  # and the database function itself agrees
            for empty in ("ARRAY[]::bigint[]", "NULL::bigint[]"):
                cur = await conn.execute(f"SELECT dmbot_installs_left_except({empty}) AS n")
                got = await cur.fetchone()
                assert got is not None
                self.assertEqual(got["n"], 0)
        self.assertIsNone((await self.row(G))["left_at"])

    async def test_the_start_up_pass_does_not_write_back_a_server_that_was_just_kicked(
        self,
    ) -> None:
        await self.store.present([(G, 700), (OTHER, 800)], gone=lambda gid: gid == G)
        self.assertIsNone(await self.row(G))
        self.assertIsNotNone(await self.row(OTHER))

    async def test_only_the_bot_may_call_the_sweep(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT has_function_privilege('public', 'dmbot_installs_left_except(bigint[])',"
                " 'EXECUTE') AS everyone"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["everyone"])

    async def test_a_join_and_a_quick_kick_leave_no_row_marked_present(self) -> None:
        # The join waits behind nothing, but the bot is already gone by its turn: no row.
        await self.store.joined(G, gone=lambda: True)
        self.assertIsNone(await self.row(G))
        # A leave queued behind a join that came back writes nothing either.
        await self.store.joined(OTHER)
        await self.store.left(OTHER, back=lambda: True)
        self.assertIsNone((await self.row(OTHER))["left_at"])

    async def test_one_servers_writes_go_one_at_a_time_in_order(self) -> None:
        import asyncio

        order: list[str] = []
        gate = asyncio.Event()
        real = self.store._db.guild

        class Slow:
            def __init__(self, inner: Any, name: str) -> None:
                self.inner, self.name = inner, name

            async def __aenter__(self) -> Any:
                order.append(f"start {self.name}")
                if self.name == "join":
                    await gate.wait()
                return await self.inner.__aenter__()

            async def __aexit__(self, *exc: Any) -> Any:
                order.append(f"end {self.name}")
                return await self.inner.__aexit__(*exc)

        names = iter(["join", "leave"])
        with mock.patch.object(self.store._db, "guild", lambda gid: Slow(real(gid), next(names))):
            join = asyncio.create_task(self.store.joined(G))
            await asyncio.sleep(0.05)
            leave = asyncio.create_task(self.store.left(G))
            await asyncio.sleep(0.05)
            self.assertEqual(order, ["start join"])  # the leave waits for the join's turn
            gate.set()
            await asyncio.gather(join, leave)
        self.assertEqual(order, ["start join", "end join", "start leave", "end leave"])


class TheHooks(unittest.IsolatedAsyncioTestCase):
    def bot(self) -> Any:
        installs = SimpleNamespace(
            joined=AsyncMock(),
            left=AsyncMock(),
            present=AsyncMock(return_value=2),
            left_except=AsyncMock(return_value=0),
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
        bot.installs.left.assert_called_once()
        self.assertEqual(bot.installs.left.await_args.args, (G,))
        self.assertFalse(bot.installs.left.await_args.kwargs["back"]())  # asked again at its turn

    async def test_a_kick_and_a_quick_re_add_does_not_leave_the_server_marked_left(self) -> None:
        bot = self.bot()
        bot.get_guild = lambda gid: SimpleNamespace(id=gid)  # it is back already
        await DMBot._note_left(bot, G)
        bot.installs.left.assert_not_called()

    async def test_an_outage_is_not_a_removal(self) -> None:
        # Discord's own "unavailable" event is a different one: dispatch it the way discord.py
        # does (the handler named for the event, if there is one) and nothing is written.
        bot = self.bot()
        bot.get_guild = lambda gid: None
        handler = getattr(DMBot, "on_guild_unavailable", None)
        if handler is not None:
            await handler(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, [])
        bot.installs.left.assert_not_called()
        bot.installs.left_except.assert_not_called()

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
        bot.installs.joined.assert_called_once()
        self.assertEqual(bot.installs.joined.call_args.args[0], G)

    async def test_joining_is_recorded_and_the_welcome_still_goes(self) -> None:
        bot = self.bot()
        with mock.patch("dmbot.bot.install.post_welcome", AsyncMock(return_value="welcomed")):
            await DMBot.on_guild_join(bot, cast(Any, SimpleNamespace(id=G)))
        self.assertEqual(bot.tracked, ["install-joined", "offer-sweep"])

    async def test_the_start_up_pass_covers_every_server_the_bot_is_in(self) -> None:
        bot = self.bot()
        bot._resume_started = False
        bot.guilds = [
            SimpleNamespace(
                id=G,
                me=SimpleNamespace(
                    joined_at=datetime.fromtimestamp(700, UTC),
                    guild_permissions=discord.Permissions.all(),
                ),
            ),
            SimpleNamespace(id=OTHER, me=None),
        ]
        bot._note_present = lambda first: DMBot._note_present(bot, first)
        bot._resume_with_retries = AsyncMock()
        bot._start_up_campaign_sweep = AsyncMock()
        bot._name_old_speakers = AsyncMock()
        bot._background = []
        await DMBot.on_ready(bot)
        for task in bot._background:
            await task
        self.assertIn("installs", bot.tracked)
        await DMBot._note_present(bot, True)  # (what the tracked task runs)
        bot.installs.present.assert_called_once()
        self.assertEqual(bot.installs.present.call_args.args[0], [(G, 700), (OTHER, None)])
        bot.installs.left_except.assert_called_once_with([G, OTHER])

    async def test_a_reconnect_records_the_servers_again_but_does_not_sweep(self) -> None:
        bot = self.bot()
        bot._resume_started = True  # not the first connect
        bot.guilds = [SimpleNamespace(id=G, me=None)]
        bot._note_present = lambda first: DMBot._note_present(bot, first)
        await DMBot.on_ready(bot)
        self.assertEqual(bot.tracked, ["installs"])
        await DMBot._note_present(bot, False)
        self.assertEqual(bot.installs.present.call_args.args[0], [(G, None)])
        bot.installs.left_except.assert_not_called()  # a reconnect's list may still be filling

    async def test_a_failed_sweep_costs_only_the_sweep(self) -> None:
        bot = self.bot()
        bot.guilds = [SimpleNamespace(id=G, me=None)]
        bot.installs.left_except = AsyncMock(side_effect=RuntimeError("row with a name in it"))
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            await DMBot._note_present(bot, True)
        self.assertNotIn("name in it", " ".join(logs.output))
        bot.installs.present.assert_called_once()
