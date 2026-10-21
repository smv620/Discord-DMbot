"""What the recap stages read for one session (#1382), loaded the same way by the bot's job and
by the command-line test run (#1424), so a test run reads exactly what the live job would.

This campaign's own transcript of that session, its names (secret ones read fresh), its earlier
posted recap and its game clock. None when the names can't be read: then nothing is written,
since a secret name couldn't be kept out.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.recaps import fixed_lines
from dmbot.recaps.pipeline import Inputs
from dmbot.recaps.style import NoteStore, StyleStore
from dmbot.recaps.text import strip_cues
from dmbot.timebot.clock import day_of as clock_day_of

log = logging.getLogger(__name__)


async def load_inputs(
    *,
    campaigns: Any,
    lookup: Any,
    transcripts: Any,
    recaps: Any,
    clocks: Any | None,
    guild_id: int,
    campaign_id: str,
    session_at: int,
    styles: StyleStore | None = None,
    notes: NoteStore | None = None,
) -> Inputs | None:
    campaign = await campaigns.get(guild_id, campaign_id)
    if campaign is None:
        return None
    try:
        names = await lookup.get(guild_id, campaign_id)
    except Exception:
        log.exception("Recap: couldn't load the campaign's names")
        return None
    found = [
        s
        for s in await transcripts.sessions(guild_id, campaign_id, limit=1000)
        if s.started_at == session_at
    ]
    if not found:
        return None
    lines = await transcripts.lines(guild_id, found[0].id)
    bridge = await recaps.bridge(guild_id, campaign_id, session_at)
    stored = await clocks.get(guild_id, campaign_id) if clocks is not None else None
    return Inputs(
        lines=lines,
        dm_ids=set(campaign.dm_user_ids),
        allowed_names=sorted(
            {n.text for n in names.names if n.confirmed and not n.secret and not n.person}
        ),
        secret_names=sorted({n.text for n in names.names if n.secret}),
        # Last time's recap without its fixed lines: the writer is not shown lines to copy (#2253).
        previous=fixed_lines.without(strip_cues(bridge.summary)) if bridge is not None else "",
        campaign_name=campaign.name,
        day=clock_day_of(stored.clock.minute) if stored is not None else None,
        # The campaign's example recaps, or the defaults: for the writer's tone only (#2075).
        style=await styles.examples_for(guild_id, campaign_id) if styles is not None else (),
        notes=[n.text for n in await notes.notes(guild_id, campaign_id)]
        if notes is not None
        else (),
    )
