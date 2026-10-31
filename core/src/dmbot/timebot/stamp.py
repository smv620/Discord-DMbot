"""The game minute to stamp on a belief made live in a session (#3091, part 3). Reads the clock,
never writes it.

Only a writer that really is a live happening at the table asks for this: a belief formed after a
session (a reading), an import, lore or the DM's edit leaves its game time empty, and nothing ever
stamps a belief with the clock as it reads at a later time. With no clock the answer is None,
which every reader takes as "time not known".

While the party is split the clock a button acts on is the picked group's, and that is the one
stamped: a group has no list of members, so a character's own group cannot be told.
"""

from __future__ import annotations

from dmbot.timebot.store import ClockStore


async def live_minute(clocks: ClockStore, guild_id: int, campaign_id: str) -> int | None:
    """The game minute of the campaign's clock (the picked group's while split), or None when
    the campaign has no clock."""
    stored = await clocks.get(guild_id, campaign_id)
    return None if stored is None else stored.clock.minute
