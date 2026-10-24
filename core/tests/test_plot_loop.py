"""PlotBot's checks run in their own loop (#2122): a check that waits on the AI, or hangs, never
delays a recap job or a DM's Post, which run in the recap loop."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot


def bound(bot: Any, name: str) -> Any:
    return getattr(DMBot, name).__get__(bot)


class Loops(unittest.IsolatedAsyncioTestCase):
    async def test_a_hung_plot_check_does_not_hold_up_the_recap_jobs(self) -> None:
        real_sleep = asyncio.sleep
        hang = asyncio.Event()  # never set: the check waits on the AI for good
        started = asyncio.Event()

        async def hung() -> None:
            started.set()
            await hang.wait()

        async def quick(_seconds: float) -> None:
            await real_sleep(0)

        bot = SimpleNamespace(
            run_recaps=AsyncMock(),
            run_plot_checks=hung,
            recap_audio_files=SimpleNamespace(sweep=lambda: 0),
        )
        with patch("dmbot.bot.asyncio.sleep", quick):
            plot = asyncio.create_task(bound(cast(Any, bot), "_plot_loop")())
            recaps = asyncio.create_task(bound(cast(Any, bot), "_recap_loop")())
            try:
                async with asyncio.timeout(2):
                    while bot.run_recaps.await_count < 5 or not started.is_set():
                        await real_sleep(0)
            finally:
                plot.cancel()
                recaps.cancel()
                await asyncio.gather(plot, recaps, return_exceptions=True)
        self.assertGreaterEqual(bot.run_recaps.await_count, 5)  # recaps went on, check or no check

    async def test_the_recap_loop_does_not_run_the_plot_checks_itself(self) -> None:
        real_sleep = asyncio.sleep
        three = asyncio.Event()
        calls = 0

        async def run_recaps() -> None:
            nonlocal calls
            calls += 1
            if calls >= 3:
                three.set()

        bot = SimpleNamespace(
            run_recaps=run_recaps,
            run_plot_checks=AsyncMock(),
            recap_audio_files=SimpleNamespace(sweep=lambda: 0),
        )

        async def quick(_seconds: float) -> None:
            await real_sleep(0)

        with patch("dmbot.bot.asyncio.sleep", quick):
            task = asyncio.create_task(bound(cast(Any, bot), "_recap_loop")())
            async with asyncio.timeout(2):
                await three.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        bot.run_plot_checks.assert_not_awaited()
