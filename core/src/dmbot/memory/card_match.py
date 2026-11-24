"""The known name a waiting name's card offers as "Sounds like X" (#394, #5215), shared by Discord's
card and the website's, so the two always suggest the same name."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dmbot.memory.scan import Match, card_sound_keys, near_match_in

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


async def card_match(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, name: str
) -> Match | None:
    """The known name this waiting one sounds like, worked out now (names may have changed, or been
    made secret, since the session): one small query for confirmed, never secret names that share
    a sound code with it or with one of its final-"s" folds ("Explorer League" finds "Explorer's
    League", #5215), then the spelling test. None when there is no one clear match, or it can't be
    read: the card is then asked as new. Never joins anything: the DM decides."""
    try:
        rows = await memory.sound_alikes(guild_id, campaign_id, card_sound_keys(name))
    except Exception:
        log.exception("Couldn't look up names that sound like a suggestion")
        return None
    return near_match_in(name, (r for r in rows if r[0] != entity_id))
