"""Test helper: give a stand-in bot the real question-sending methods (#3210), so a test of a step
that asks the DM can fake only the last hop (`ask_privately` and `post`)."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

from dmbot.bot import DMBot


def with_asking(fake: SimpleNamespace) -> SimpleNamespace:
    fake.ask_dm = partial(DMBot.ask_dm, fake)  # type: ignore[attr-defined]
    fake.send_asks = partial(DMBot.send_asks, fake)  # type: ignore[attr-defined]
    return fake


def asked(fake: Any) -> list[Any]:
    """What `ask_privately` was given: (guild_id, campaign_id, text, view) for each call."""
    return [call.args for call in fake.ask_privately.await_args_list]
