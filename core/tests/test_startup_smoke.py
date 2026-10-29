"""DMbot starts: `setup_hook` runs end to end, with the gateway and ears replaced (#1830).

Unit tests cover the parts; nothing else starts the bot as a whole, so a mistake in `setup_hook`
(an import that fails at start-up, a button registered twice, a background task that raises on its
first run) would only show at the restart, with the table waiting. This builds a real `DMBot` on
the test database (so it runs in the database-backed CI job, like the store tests), runs
`setup_hook()` to the end, and checks what it set up. No Discord and no network.
"""

from __future__ import annotations

import asyncio
import collections
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.discord_limits import tree_problems
from dmbot.sessions import SessionStore
from tests.pg import DatabaseTest
from tests.test_dynamic_items import all_dynamic_items

TICK_S = 0.5  # long enough for each loop to run its first pass over empty data


def broken(tasks: list[asyncio.Task[Any]]) -> list[str]:
    """The background tasks that ended: they must run as long as DMbot does (#499)."""
    out = []
    for task in tasks:
        if task.done() and not task.cancelled():
            problem = task.exception()
            out.append(
                f"{task.get_name()}: {problem!r}" if problem else f"{task.get_name()}: ended"
            )
    return out


class StartupSmokeTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            CampaignStore(self.db),
            SessionStore(self.db),
        )
        self.registered: list[type] = []
        self.bot.add_dynamic_items = lambda *items: self.registered.extend(items)  # type: ignore[method-assign]
        self.bot.tree.sync = AsyncMock(return_value=[])  # type: ignore[method-assign]
        self.bot.ears.start = AsyncMock()  # type: ignore[method-assign]

    async def start(self) -> list[asyncio.Task[Any]]:
        with (
            patch("dmbot.bot.install.configure"),
            patch("dmbot.bot.install.install_link", return_value="https://example.org/install"),
        ):
            await asyncio.wait_for(self.bot.setup_hook(), 60)
        self.addAsyncCleanup(self.stop)
        await asyncio.sleep(TICK_S)
        return list(self.bot._background)

    async def stop(self) -> None:
        for task in self.bot._background:
            task.cancel()
        await asyncio.gather(*self.bot._background, return_exceptions=True)

    async def test_setup_hook_runs_to_the_end(self) -> None:
        tasks = await self.start()
        self.assertGreaterEqual(len(tasks), 8)

    async def test_every_dynamic_item_is_registered_exactly_once(self) -> None:
        await self.start()
        twice = [c.__qualname__ for c, n in collections.Counter(self.registered).items() if n > 1]
        self.assertEqual(twice, [])
        self.assertEqual(set(self.registered), all_dynamic_items())

    async def test_the_command_tree_is_built_and_inside_discords_limits(self) -> None:
        await self.start()
        commands = self.bot.tree.get_commands()
        self.assertEqual({c.name for c in commands}, {"dmbot", "consent", "transcript"})
        self.assertEqual(tree_problems([c.to_dict(self.bot.tree) for c in commands]), [])

    async def test_each_background_task_survives_its_first_tick(self) -> None:
        tasks = await self.start()
        self.assertEqual(broken(tasks), [])

    async def test_a_background_task_that_raises_is_caught(self) -> None:
        async def raises() -> None:
            raise RuntimeError("first run failed")

        with patch.object(DMBot, "_idle_sweeper", lambda self: raises()):
            tasks = await self.start()
        found = broken(tasks)
        self.assertEqual(len(found), 1, found)
        self.assertIn("idle-sweep", found[0])
        self.assertIn("first run failed", found[0])
