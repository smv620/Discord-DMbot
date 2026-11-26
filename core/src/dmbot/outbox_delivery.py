"""The bot's side of the outbox (#3843): claim what waits, say it, mark it sent.

A handler is the bot's own function for one kind of row; it builds the message from the row's ids
(never from words in the row) and raises when Discord did not accept it. The row is marked sent only
after the handler returned, so a failed delivery is tried again (up to `outbox.MAX_TRIES`) and a
kind nobody handles ends at once as `unknown-kind`. A bot that dies between Discord accepting the
message and the mark leaves the row `sending`; it is claimed again after `outbox.STALE_S`, so
delivery is at least once and a duplicate line is the worst case. Counts only in the log; the
failure's type, never its text.

The loop wakes on the database's NOTIFY and every `POLL_S` as a backstop, one server at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from dmbot.logs import log_context
from dmbot.outbox import CHANNEL, Outbox

log = logging.getLogger(__name__)

POLL_S = 30.0
HANDLER_S = 60.0  # one delivery may take this long before it counts as a failed try
SWEEP_EVERY_S = 3600.0
Handler = Callable[[int, str, Mapping[str, Any]], Awaitable[None]]  # (server, campaign, payload)
Listen = Callable[[str], Any]  # `Database.listen`: an async iterator of notification payloads


async def _mark(done: Awaitable[None]) -> bool:
    """A mark that fails costs only that row (it is claimed again after `STALE_S`), never the
    round's other rows."""
    try:
        await done
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Outbox: could not mark a row (%s)", type(exc).__name__)
        return False
    return True


async def deliver_round(outbox: Outbox, guild_id: int, handlers: Mapping[str, Handler]) -> int:
    """Deliver what waits in one server. Returns how many were said. Never raises."""
    said = 0
    try:
        with log_context(guild_id=guild_id):
            for row in await outbox.claim(guild_id):
                handler = handlers.get(row.kind)
                if handler is None:
                    await outbox.failed(guild_id, row.id, "unknown-kind", final=True)
                    continue
                try:
                    async with asyncio.timeout(HANDLER_S):  # (a stuck call is a failed try)
                        await handler(row.guild_id, row.campaign_id, row.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # (the type only: a message could quote a row)
                    log.warning(
                        "Outbox: a %s row was not delivered (%s)", row.kind, type(exc).__name__
                    )
                    await _mark(outbox.failed(guild_id, row.id, type(exc).__name__))
                    continue
                if await _mark(outbox.sent(guild_id, row.id)):
                    said += 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Outbox: a round failed (%s)", type(exc).__name__)
    return said


async def listen_and_wake(listen: Listen, wake: asyncio.Event) -> None:
    """Set `wake` on each outbox notification. A dropped connection is waited out and retried: the
    poll still delivers, only a little later."""
    while True:
        try:
            async with contextlib.aclosing(listen(CHANNEL)) as notes:
                async for _ in notes:
                    wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Outbox: lost the listener (%s); will listen again", type(exc).__name__)
        await asyncio.sleep(5)


async def run(
    outbox: Outbox,
    guilds: Callable[[], Iterable[int]],
    handlers: Mapping[str, Handler],
    wake: asyncio.Event,
    *,
    poll_s: float = POLL_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """The delivery loop: a round when woken or every `poll_s`; old rows are swept now and then."""
    last_sweep = clock()
    while True:
        sweep = clock() - last_sweep >= SWEEP_EVERY_S
        for guild_id in list(guilds()):
            await deliver_round(outbox, guild_id, handlers)
            if sweep:
                with contextlib.suppress(Exception):
                    await outbox.sweep(guild_id)
        if sweep:
            last_sweep = clock()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), poll_s)
        wake.clear()
