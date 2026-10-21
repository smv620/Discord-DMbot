"""DM-screen questions that show a secret name, so they can be pulled back if the screen opens up
(#2097).

"Is it revealed now?" and "Keep it secret?" are posted on the DM screen with the name in them,
which is safe only while the screen is private. If the DM later lets players peek or opens the
screen, a question still sitting there would show the name to people who may now look. The
messages are remembered here (in memory, per campaign, a handful at a time) and, when the
visibility moves away from private, edited to a plain line with the buttons gone. A restart
forgets the list, so the screen's own history is searched too, all of it, for the bot's messages
that carry these questions' buttons. That search runs once per campaign per process (everything
posted after it is remembered here), and again after the list had to drop a question. An
answered question carries no name any more (its text is written without it), so nothing is left
for the search to miss.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

KEPT_PER_CAMPAIGN = 20
HIDDEN = "This question was about a secret name. Answer it from the name's card."
HIDDEN_ANSWERED = "This was about a secret name. See the name's card."

_posts: dict[tuple[int, str], list[Any]] = defaultdict(list)


def remember(guild_id: int, campaign_id: str, message: Any) -> None:
    """Keep a posted question that shows a secret name (the newest few)."""
    kept = _posts[(guild_id, campaign_id)]
    kept.append(message)
    if len(kept) > KEPT_PER_CAMPAIGN:
        del kept[:-KEPT_PER_CAMPAIGN]
        _forgot((guild_id, campaign_id))  # one forgotten: the screen must be read again


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
# "dmbot:sa:" is the secret question's newer id (the server on it); the old one still parses.
BUTTON_PREFIXES = ("dmbot:secret:", "dmbot:sa:", "dmbot:rv:")

# Campaigns whose screen history was searched in this process. Questions posted after that are
# remembered in `_posts`, so only what a restart forgot needs the search, and it needs it once;
# a question the list drops (over KEPT_PER_CAMPAIGN) takes the campaign out again.
# The value is the screen channel that was read: a campaign whose screen moved to a new channel is
# read again.
_searched: dict[tuple[int, str], Any] = {}
# How many questions each campaign's list has dropped. A search that began before a drop must not
# mark the campaign as read, since the dropped question may have been posted after the search
# passed it (remember() can run at any time; hide() runs under the campaign's lock).
_drops: dict[tuple[int, str], int] = defaultdict(int)
_UNSEARCHED = object()


def _forgot(key: tuple[int, str]) -> None:
    """A question fell off the list: the screen must be searched again."""
    _drops[key] += 1
    _searched.pop(key, None)


async def _scan(channel: Any, bot_id: int) -> tuple[list[Any], bool]:
    """The bot's own recent messages on the screen that carry a secret question's buttons: what a
    restart forgot. Matched by the buttons' ids, since the text alone can't be told apart. The
    flag says the whole history was read."""
    found: list[Any] = []
    try:
        async for message in channel.history(limit=None):
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
        return found, False
    return found, True


async def hide(
    guild_id: int, campaign_id: str, *, channel: Any = None, bot_id: int | None = None
) -> int:
    """Edit every remembered question of this campaign so it no longer shows the name, and forget
    them. With the screen `channel`, also the ones a restart forgot (still-open questions are
    found by their buttons). A message that is gone or can't be edited is skipped (logged as a
    count only). Returns how many were changed."""
    messages = _posts.pop((guild_id, campaign_id), [])
    key = (guild_id, campaign_id)
    screen = getattr(channel, "id", None)
    if channel is not None and bot_id is not None and _searched.get(key, _UNSEARCHED) != screen:
        drops = _drops[key]
        known = {getattr(m, "id", None) for m in messages}
        older, complete = await _scan(channel, bot_id)
        messages += [m for m in older if getattr(m, "id", None) not in known]
        if complete and _drops[key] == drops:  # a failed or overtaken read is tried again
            _searched[key] = screen
    changed = 0
    failed: list[Any] = []
    for message in messages:
        answered = not getattr(message, "components", None)  # no buttons left: already answered
        try:
            await message.edit(
                content=HIDDEN_ANSWERED if answered else HIDDEN,
                view=None,
                allowed_mentions=NO_PINGS,
            )
            changed += 1
        except Exception:  # deleted already, or no right to edit
            failed.append(message)
    if failed:  # kept, so the next change away from private tries again
        kept = _posts[(guild_id, campaign_id)]
        kept.extend(failed)
        if len(kept) > KEPT_PER_CAMPAIGN:
            del kept[:-KEPT_PER_CAMPAIGN]
            _forgot((guild_id, campaign_id))
    if messages:
        log.info("Secret questions pulled back: %d changed, %d not", changed, len(failed))
    return changed
