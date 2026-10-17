"""Tell the bot when a campaign's house rules or the sets it follows changed (#1752).

The rules archive channel shows the campaign's house rules, so it must hear about every change:
a rule added, edited or removed, a followed set taken or dropped. The stores are plain data code
that knows nothing of Discord, so each carries an optional `on_change` callback (the bot sets
it) and the methods that change rules are marked `@notifies`: after one succeeds, the callback is
told the server and campaign. The callback must be quick and must not raise (it only schedules
work), and a failing callback never turns a saved change into an error.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

ChangeHook = Callable[[int, str], None]


def notifies[R](method: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    """For a store method whose first two arguments are `guild_id, campaign_id`."""

    @functools.wraps(method)
    async def wrapper(self: Any, guild_id: int, campaign_id: str, *args: Any, **kwargs: Any) -> R:
        result = await method(self, guild_id, campaign_id, *args, **kwargs)
        hook: ChangeHook | None = getattr(self, "on_change", None)
        if hook is not None:
            try:
                hook(guild_id, campaign_id)
            except Exception:
                log.exception("A house-rules change hook failed")
        return result

    return wrapper
