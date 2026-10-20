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


async def hide(guild_id: int, campaign_id: str) -> int:
    """Edit every remembered question of this campaign so it no longer shows the name, and forget
    them. A message that is gone or can't be edited is skipped (logged as a count only). Returns
    how many were changed."""
    messages = _posts.pop((guild_id, campaign_id), [])
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
