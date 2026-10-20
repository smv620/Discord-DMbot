"""DM-screen questions that show a secret name, so they can be pulled back if the screen opens up
(#2097).

"Is it revealed now?" and "Keep it secret?" are posted on the DM screen with the name in them,
which is safe only while the screen is private. If the DM later lets players peek or opens the
screen, a question still sitting there would show the name to people who may now look. The
messages are remembered here (in memory, per campaign, a handful at a time) and, when the
visibility moves away from private, edited to a plain line with the buttons gone. A restart
forgets the list; the questions are for the session that asked them.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

KEPT_PER_CAMPAIGN = 20
HIDDEN = "This question was about a secret name. Answer it from the name's card."

_posts: dict[tuple[int, str], list[Any]] = defaultdict(list)


def remember(guild_id: int, campaign_id: str, message: Any) -> None:
    """Keep a posted question that shows a secret name (the newest few)."""
    kept = _posts[(guild_id, campaign_id)]
    kept.append(message)
    del kept[:-KEPT_PER_CAMPAIGN]


def pending(guild_id: int, campaign_id: str) -> int:
    return len(_posts.get((guild_id, campaign_id), ()))


async def hide_one(message: Any) -> bool:
    """Edit one question straight away (it was posted just as the screen opened up)."""
    try:
        await message.edit(content=HIDDEN, view=None, allowed_mentions=NO_PINGS)
    except Exception:
        log.warning("A secret question could not be pulled back")
        return False
    return True


def is_exposed(visibility: str, in_server_channel: bool) -> bool:
    """A question on the DM screen (a server channel, not a private message) while players may
    look at the screen. A button pressed then must not write the name back."""
    return in_server_channel and visibility != "private"


# What a question's buttons start with: the keep-it-secret question and the reveal question.
BUTTON_PREFIXES = ("dmbot:secret:", "dmbot:rv:")
SCAN = 50  # how many recent messages of the screen to look through


async def _scan(channel: Any, bot_id: int) -> list[Any]:
    """The bot's own recent messages on the screen that carry a secret question's buttons: what a
    restart forgot. Matched by the buttons' ids, since the text alone can't be told apart."""
    found: list[Any] = []
    try:
        async for message in channel.history(limit=SCAN):
            if getattr(getattr(message, "author", None), "id", None) != bot_id:
                continue
            ids = [
                str(getattr(child, "custom_id", "") or "")
                for row in getattr(message, "components", ())
                for child in getattr(row, "children", ())
            ]
            if any(i.startswith(BUTTON_PREFIXES) for i in ids):
                found.append(message)
    except Exception:
        log.warning("Could not look through the DM screen for secret questions")
    return found


async def hide(
    guild_id: int, campaign_id: str, *, channel: Any = None, bot_id: int | None = None
) -> int:
    """Edit every remembered question of this campaign so it no longer shows the name, and forget
    them. With the screen `channel`, also the ones a restart forgot (still-open questions are
    found by their buttons). A message that is gone or can't be edited is skipped (logged as a
    count only). Returns how many were changed."""
    messages = _posts.pop((guild_id, campaign_id), [])
    if channel is not None and bot_id is not None:
        known = {getattr(m, "id", None) for m in messages}
        messages += [m for m in await _scan(channel, bot_id) if getattr(m, "id", None) not in known]
    changed = failed = 0
    for message in messages:
        try:
            await message.edit(content=HIDDEN, view=None, allowed_mentions=NO_PINGS)
            changed += 1
        except Exception:  # deleted already, or no right to edit: nothing more can be done
            failed += 1
    if messages:
        log.info("Secret questions pulled back: %d changed, %d not", changed, failed)
    return changed
