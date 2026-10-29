"""Session start: check the followed house-rules set and every followed homebrew pack, and post one
combined note (#1538). It lives apart from both checks because each imports `shared_update`."""

from __future__ import annotations

import logging
from typing import Any

from dmbot.campaigns import Campaign
from dmbot.dm_screen import pack_update, set_update, shared_update

log = logging.getLogger(__name__)


async def check_all(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """Never raises: each check catches its own trouble and hands back what it did finish."""
    found = shared_update.Collected()
    # Each check on its own, as when they were two tasks: one failing never loses the other's note.
    for check in (set_update.collect, pack_update.collect):
        try:
            await check(bot, campaign, screen_id, found)
        except Exception:
            log.exception("A shared-rules check failed")
    try:
        await shared_update.post(bot, campaign.id, screen_id, found)
    except Exception:
        log.exception("Couldn't post the shared-rules note")
