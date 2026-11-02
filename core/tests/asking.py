"""Test helper: give a stand-in bot the real question-sending methods (#3210), so a test of a step
that asks the DM can fake only the last hop (`ask_privately` and `post`)."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

from dmbot.bot import DMBot


def with_asking(fake: SimpleNamespace) -> SimpleNamespace:
    me: Any = fake  # (a stand-in for the bot: typed loosely on purpose)
    fake.ask_dm = partial(DMBot.ask_dm, me)
    fake.send_asks = partial(DMBot.send_asks, me)
    return fake


def asked(fake: Any) -> list[Any]:
    """What `ask_privately` was given: (guild_id, campaign_id, text, view) for each call."""
    return [call.args for call in fake.ask_privately.await_args_list]


def live_on_screen(bot: Any) -> None:
    """Make a stand-in or real bot ask live questions on the screen, through whatever
    `post_message` is at the time of the call. For tests of what is asked, not of where (#3210)."""

    async def ask_live(table: Any, text: str, make_view: Any, on_sent: Any = None) -> Any:
        message = await bot.post_message(table.screen_channel_id, text, make_view())
        if message is not None and on_sent is not None:
            on_sent(message)
        return [message] if message is not None else []

    bot.ask_live = ask_live
