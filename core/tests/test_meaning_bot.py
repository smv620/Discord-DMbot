"""Meaning search in the bot (#2664): nothing is built, sent or run unless the switch is on and
the key is set. A bare bot: only what is read is set."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.config import Settings


def bot(*, on: bool, key: str = "k") -> Any:
    b: Any = DMBot.__new__(DMBot)
    b.settings = SimpleNamespace(
        meaning_search_on=on and bool(key), voyage_embed_model="voyage-3.5"
    )
    b.voyage = object() if on else None
    b.memory = SimpleNamespace(_db=object())
    b.campaigns = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c")))
    b.consent = SimpleNamespace(consenting=AsyncMock(return_value=frozenset()))
    b.plan_allows = AsyncMock(return_value=True)
    return b


TABLE = SimpleNamespace(guild_id=1, campaign_id="c")


class Gating(unittest.IsolatedAsyncioTestCase):
    async def test_the_switch_and_the_key_decide_what_may_be_sent(self) -> None:
        self.assertTrue(await bot(on=True).meaning_allowed())
        self.assertFalse(await bot(on=False).meaning_allowed())
        s = Settings.__dataclass_fields__  # (the setting itself is tested in test_config)
        self.assertIn("meaning_search", s)

    async def test_with_the_switch_off_the_after_session_step_does_nothing(self) -> None:
        b = bot(on=False)
        with patch("dmbot.bot.embedding.run", new=AsyncMock()) as run:
            await b.embed_campaign(TABLE)
        run.assert_not_awaited()
        b.campaigns.get.assert_not_awaited()

    async def test_a_plan_that_does_not_allow_story_is_not_run(self) -> None:
        b = bot(on=True)
        b.plan_allows = AsyncMock(return_value=False)
        with patch("dmbot.bot.embedding.run", new=AsyncMock()) as run:
            await b.embed_campaign(TABLE)
        run.assert_not_awaited()

    async def test_with_everything_on_it_runs_with_the_gate_and_a_call_limit(self) -> None:
        b = bot(on=True)
        with patch("dmbot.bot.embedding.run", new=AsyncMock()) as run:
            await b.embed_campaign(TABLE)
        run.assert_awaited_once()
        call = run.await_args
        assert call is not None
        self.assertEqual(call.kwargs["max_calls"], 50)
        self.assertIs(call.kwargs["allowed"].__func__, DMBot.meaning_allowed)  # asked every call
        self.assertEqual(call.args[5], "c")
        self.assertEqual(
            [type(src).__name__ for src in call.args[2]], ["LineSource", "EntitySource"]
        )
        self.assertIs(call.args[3], b.consent.consenting)  # consent, read for each batch


class Leads(unittest.IsolatedAsyncioTestCase):
    async def test_a_slow_lead_search_is_no_leads_and_a_line_not_a_traceback(self) -> None:
        import asyncio
        from unittest.mock import patch

        b: Any = DMBot.__new__(DMBot)

        async def slow(*args: Any, **kw: Any) -> list[str]:
            await asyncio.sleep(1)
            return ["x"]

        b.meaning_seeds = SimpleNamespace(find=slow)
        b.settings = SimpleNamespace()
        with (
            patch("dmbot.bot.MEANING_WAIT_S", 0.01),
            self.assertLogs("dmbot.bot", "INFO") as logged,
        ):
            leads = await b.meaning_leads(SimpleNamespace(guild_id=1, id="c"), "who?")
        self.assertEqual(leads, [])
        self.assertFalse(any("Traceback" in line for line in logged.output))
