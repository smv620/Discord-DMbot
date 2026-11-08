"""The bot's round for 🎤 Say it (#2316): nothing starts while a table is listening or when the
switch is off, one round runs at a time, a table starting stops it, and the model is unloaded when
idle."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.said_as.recogniser import WorkerRecogniser


def fake_bot(*, on: bool = True, tables: Any = None, store: Any = "store") -> Any:
    me = SimpleNamespace(
        settings=SimpleNamespace(said_as_voice=on, said_as_model="/model"),
        said_as_listens=store,
        tables=tables or {},
        _listening=None,
        _speech_sounds=None,
        _closing=False,
        guilds=[SimpleNamespace(id=1)],
    )
    me._listen_round = AsyncMock()
    return me


class Round(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_starts_when_the_switch_is_off_or_a_table_is_listening(self) -> None:
        for me in (fake_bot(on=False), fake_bot(tables={1: object()}), fake_bot(store=None)):
            await DMBot.run_said_as_listens(me)
            me._listen_round.assert_not_called()
            self.assertIsNone(me._listening)

    async def test_a_round_starts_and_only_one_runs_at_a_time(self) -> None:
        me = fake_bot()
        gate = asyncio.Event()

        async def slow(_store: Any) -> None:
            await gate.wait()

        me._listen_round = slow
        await DMBot.run_said_as_listens(me)
        first = me._listening
        assert first is not None
        await DMBot.run_said_as_listens(me)
        self.assertIs(me._listening, first)  # still the same round
        gate.set()
        await first

    async def test_the_idle_model_is_unloaded_even_while_a_table_listens(self) -> None:
        me = fake_bot(tables={1: object()})
        me._speech_sounds = SimpleNamespace(unload_if_idle=AsyncMock())
        await DMBot.run_said_as_listens(me)
        me._speech_sounds.unload_if_idle.assert_awaited_once()
        me._listen_round.assert_not_called()

    async def test_the_recogniser_exists_only_with_the_switch_on_and_is_made_once(self) -> None:
        self.assertIsNone(DMBot.recogniser(fake_bot(on=False)))
        me = fake_bot()
        first = DMBot.recogniser(me)
        self.assertIsInstance(first, WorkerRecogniser)
        self.assertIs(DMBot.recogniser(me), first)

    async def test_a_round_stops_at_once_when_a_table_starts(self) -> None:
        me = fake_bot()
        me.tables = {1: object()}  # a table started after the round was begun
        store = SimpleNamespace(claim_next=AsyncMock(return_value=None))
        await DMBot._listen_round(me, store)  # type: ignore[arg-type]
        store.claim_next.assert_not_called()
