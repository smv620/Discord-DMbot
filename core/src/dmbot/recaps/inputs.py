"""What the recap stages read for one session (#1382), loaded the same way by the bot's job and
by the command-line test run (#1424), so a test run reads exactly what the live job would.

This campaign's own transcript of that session, its names (secret ones read fresh), its earlier
posted recap and its game clock. None when the names can't be read: then nothing is written,
since a secret name couldn't be kept out.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.memory.extractor import readable
from dmbot.memory.models import name_key
from dmbot.recaps import fixed_lines
from dmbot.recaps import split as sp
from dmbot.recaps.pipeline import Inputs
from dmbot.recaps.style import NoteStore, StyleStore
from dmbot.recaps.text import strip_cues
from dmbot.timebot.clock import day_of as clock_day_of

log = logging.getLogger(__name__)


def said_in(lines: Any) -> str:
    """What the table said in a session, for the recap's name check: the consented table lines
    (as heard and as cleaned), never the DM sidebar, and squashed as names are compared."""
    return " ".join(
        name_key(f"{line.heard} {line.text}") for line in lines if not getattr(line, "sidebar", "")
    )


async def recap_said(
    transcripts: Any, guild_id: int, campaign_id: str, session_at: int | None
) -> str | None:
    """What the table said in that session (for names the players haven't been shown yet, #2404),
    or None when there is no session or its lines can't be read: then such a name is not allowed."""
    if session_at is None or transcripts is None:
        return None
    try:
        sessions = await transcripts.sessions(guild_id, campaign_id, limit=1000)
        found = [s for s in sessions if s.started_at == session_at]
        if not found:
            return None
        return said_in(await transcripts.lines(guild_id, found[0].id))
    except Exception:
        log.exception("Recap: couldn't read what the session said")
        return None


def recap_name_sets(names: Any, said: str | None) -> tuple[list[str], list[str]]:
    """(the names the recap may use, the names it must not) for a session. A secret name is never
    allowed. A name the players haven't been shown yet (#2404) is allowed only when the table said
    it in this session (`said`, from `said_in`): the recap writes what the party knows, and saying
    a name at the table is how the party learns it. With no `said` (the session's lines can't be
    read) it is not allowed: stricter, never looser."""

    def spoken(text: str) -> bool:
        return said is not None and f" {name_key(text)} " in f" {said} "

    allowed = {
        n.text
        for n in names.names
        if n.confirmed and not n.secret and not n.person and (not n.entry_hidden or spoken(n.text))
    }
    secret = {n.text for n in names.names if n.secret or (n.entry_hidden and not spoken(n.text))}
    return sorted(allowed), sorted(secret)


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
    with_earlier: bool = True,
    importance: Any | None = None,
) -> Inputs | None:
    campaign = await campaigns.get(guild_id, campaign_id)
    if campaign is None:
        return None
    try:
        names = await lookup.get(guild_id, campaign_id)
    except Exception:
        log.exception("Recap: couldn't load the campaign's names")
        return None
    sessions = await transcripts.sessions(guild_id, campaign_id, limit=1000)
    found = [s for s in sessions if s.started_at == session_at]
    if not found:
        return None
    lines = await transcripts.lines(guild_id, found[0].id)
    bridge = await recaps.bridge(guild_id, campaign_id, session_at)
    earlier = (
        await _earlier(transcripts, recaps, guild_id, campaign_id, session_at)
        if with_earlier
        else None
    )
    stored = await clocks.get(guild_id, campaign_id) if clocks is not None else None
    # How connected each name is in The truth (#2411): ranks the beats, gives the writer nothing.
    scores = await importance.get(guild_id, campaign_id) if importance is not None else {}
    return Inputs(
        lines=lines,
        dm_ids=set(campaign.dm_user_ids),
        allowed_names=recap_name_sets(names, said_in(lines))[0],
        secret_names=recap_name_sets(names, said_in(lines))[1],
        # Last time's recap without its fixed lines: the writer is not shown lines to copy (#2253).
        previous=fixed_lines.without(strip_cues(bridge.summary)) if bridge is not None else "",
        campaign_name=campaign.name,
        earlier=earlier,
        importance=dict(scores),
        # Which of the ten closing lines: by this session's place in the campaign (#2253).
        ending=fixed_lines.ending_for(sum(1 for s in sessions if s.started_at < session_at)),
        day=clock_day_of(stored.clock.minute) if stored is not None else None,
        # The campaign's example recaps, or the defaults: for the writer's tone only (#2075).
        style=await styles.examples_for(guild_id, campaign_id) if styles is not None else (),
        notes=[n.text for n in await notes.notes(guild_id, campaign_id)]
        if notes is not None
        else (),
    )


async def _earlier(
    transcripts: Any, recaps: Any, guild_id: int, campaign_id: str, session_at: int
) -> sp.Earlier | None:
    """What the campaign's previous session was about (#2253), for finding a retelling of it at
    the start of this one: the content words of its play and its beat sentences. Only its play, by
    its stored split, so a retelling inside it is not carried on into this one (15 into 16 into
    17); a session from before the split is taken whole. The previous session is the latest one
    before this, including one whose transcript the keep-days limit deleted: then its beats (from
    its recap job, if it had one) stand in for it, and with no job there is nothing to compare
    with, never an older session. None when there is no earlier session at all: then nothing in
    this one is a recap. Read once, for the split stage."""
    previous = await transcripts.latest_before(guild_id, campaign_id, session_at)
    if previous is None:
        return None
    started, deleted = previous
    job = await recaps.earlier_job(guild_id, campaign_id, session_at)
    if job is not None and job.session_at != started:
        job = None  # a recap of an older session: its line numbers are not that transcript's
    beats = sp.beat_sentences(job.beats or []) if job is not None else ()
    if deleted:
        return sp.Earlier(frozenset(), beats)
    found = [
        s
        for s in await transcripts.sessions(guild_id, campaign_id, limit=1000)
        if s.started_at == started
    ]
    if not found:
        return sp.Earlier(frozenset(), beats)
    said = readable(await transcripts.lines(guild_id, found[0].id))
    split = sp.from_json(job.split) if job is not None else None
    if split is not None:
        said = sp.pick(said, split, sp.PLAY)
    return sp.Earlier(sp.vocabulary_of(s.text for s in said), beats)
