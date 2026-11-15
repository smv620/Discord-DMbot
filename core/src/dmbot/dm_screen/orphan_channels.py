"""Leftover campaign channels (#4479): the sweep that deletes a channel DMbot made for a campaign
that no longer exists.

A campaign deleted in Discord, on the website or by retention has its channels tidied by the code
that deletes it. Anything deleted where the bot did not see it (the sample run, a database edit, a
restore that replaced a campaign) leaves its DM Bot, transcript and DM screen channels behind,
and their buttons then answer "can't find that campaign". The channel ids go with the campaign's
row, so the only way to find such a channel is by what DMbot made it look like.

The rules (Supervisor's ruling on #4479), all of which must hold before anything is deleted:
- **Only DMbot's own channels.** The name starts with DMbot's prefix for that kind AND the topic is
  one of DMbot's own fixed topics. A channel somebody made themselves never has both.
- **Not one a live campaign of this server points to** (its DM Bot, transcript or DM screen id).
  The look is one server at a time, so another server's campaigns can never protect or catch a
  channel here.
- **Seen so twice, a day apart,** and the channel itself is over a day old: a backup being restored,
  a campaign being moved, or a channel made a moment before its id was saved is never caught
  mid-way. The first sighting is kept in memory only; a restart just starts the clock again, which
  only delays.
- **At most five a server a sweep**, and nothing when DMbot cannot manage channels there.
Nothing here ever holds a campaign's name, a channel's words or an id in a message: the server's
owner is told once per sweep in counts only (`told_words`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from dmbot.dm_screen import dm_bot_channel, names, transcript_channel

log = logging.getLogger(__name__)

GRACE_S = 24 * 3600  # seen orphaned this long ago, and the channel is at least this old
MAX_DELETES = 5  # a server a sweep: the rest wait for the next one
DISCORD_EPOCH_MS = 1_420_070_400_000  # to read a channel's age from its id (a snowflake)

# The fixed parts of the topics DMbot sets (`dm_screen.messages`): each has the campaign's name in
# it, which is never compared. If a topic's wording changes there, `test_orphan_channels` fails.
DM_BOT_TOPIC_END = ": your buttons for settings and help. Only you see the answers."
TRANSCRIPT_TOPIC_START = "Live transcript for "
TRANSCRIPT_TOPIC_END = "Only DMbot posts here. Only people who said yes are recorded."
SCREEN_TOPIC_START = "DM screen for "


class Candidate(Protocol):
    """What a sweep needs of a channel (a Discord text channel has all of it)."""

    id: int
    name: str
    topic: str | None

    async def delete(self, *, reason: str | None = ...) -> None: ...


def kind_of(name: str, topic: str | None) -> str | None:
    """ "dm-bot", "transcript" or "dm-screen" when the channel is named AND worded like one DMbot
    made; None for every other channel."""
    text = topic or ""
    if dm_bot_channel.is_dm_bot_name(name) and text.endswith(DM_BOT_TOPIC_END):
        return "dm-bot"
    if (
        transcript_channel.is_transcript_name(name)
        and text.startswith(TRANSCRIPT_TOPIC_START)
        and text.endswith(TRANSCRIPT_TOPIC_END)
    ):
        return "transcript"
    if names.is_screen_name(name) and text.startswith(SCREEN_TOPIC_START):
        return "dm-screen"
    return None


def age_s(channel_id: int, now: float) -> float:
    """How old a channel is, read from its id."""
    made_ms = (channel_id >> 22) + DISCORD_EPOCH_MS
    return now - made_ms / 1000


@dataclass(frozen=True, slots=True)
class Result:
    seen: int  # candidates that no live campaign points to, this sweep
    deleted: int
    failed: int = 0
    skipped: str = ""  # why nothing was deleted, if so


class Sightings:
    """When each orphan was first seen, in memory (one per bot process)."""

    def __init__(self) -> None:
        self._first: dict[int, float] = {}

    def note(self, orphans: Collection[int], now: float) -> dict[int, float]:
        """Keep the first sighting of each orphan, forget a channel no longer one."""
        self._first = {cid: self._first.get(cid, now) for cid in orphans}
        return dict(self._first)


async def sweep(
    channels: Iterable[Candidate],
    live_ids: Collection[int],
    sightings: Sightings,
    *,
    can_manage: bool,
    now: float | None = None,
) -> Result:
    """One server's look. `channels`: its text channels; `live_ids`: the DM Bot, transcript and
    DM screen channel ids of its live campaigns (this server's only). Never raises for one
    channel's trouble: it is counted and the rest go on."""
    when = time.time() if now is None else now
    orphans = [c for c in channels if c.id not in live_ids and kind_of(c.name, c.topic) is not None]
    first = sightings.note([c.id for c in orphans], when)
    if not can_manage:
        return Result(len(orphans), 0, skipped="no permission")
    due = [c for c in orphans if when - first[c.id] >= GRACE_S and age_s(c.id, when) >= GRACE_S]
    deleted = failed = 0
    for channel in due[:MAX_DELETES]:
        try:
            await channel.delete(reason="DMbot: its campaign no longer exists")
        except Exception as exc:  # (Discord refused or it was gone already: the next sweep looks)
            log.info("A leftover channel could not be deleted (%s)", type(exc).__name__)
            failed += 1
        else:
            deleted += 1
    return Result(len(orphans), deleted, failed)


def told_words(deleted: int) -> str:
    """What the server's owner is told once a sweep that deleted something: counts only, no
    campaign or channel name."""
    return f"DMbot removed {deleted} leftover channel(s) of campaigns that no longer exist."


def live_channel_ids(campaigns: Iterable[Mapping[str, object] | object]) -> set[int]:
    """The channel ids a server's live campaigns point to."""
    live: set[int] = set()
    for campaign in campaigns:
        for field in ("dm_bot_channel_id", "transcript_channel_id", "dm_screen_channel_id"):
            value = getattr(campaign, field, None)
            if isinstance(value, int) and value:
                live.add(value)
    return live
