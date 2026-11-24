"""What a new name's yes does to its session (#5133 slice 3c-2b, #5144): the lines the rule guard
tests spellings against, and the cleaned transcript following the rules just made. No Discord in it,
so Discord's card and the website's answer make exactly the same change.

Counts only in the log; the words are the campaign's and are never logged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from dmbot.memory.models import DM_ONLY_TYPES, name_key
from dmbot.transcript.respell import contains

log = logging.getLogger(__name__)

FIXED, LATER, FAILED = "fixed", "later", "failed"  # how the session's transcript ended up


async def session_lines(
    transcripts: Any, consent: Any, guild_id: int, campaign_id: str, session: str | None
) -> list[str] | None:
    """That session's lines, of the people who agree now, for the rule guard's word test only
    (nothing is shown or kept). None when they can't be read: then no rule is made."""
    if transcripts is None or session is None:
        return None
    try:
        # (who counts as recorded in this campaign, imports' speakers too, #3305)
        agreed = await consent.recorded_in(guild_id, campaign_id)
        lines = await transcripts.lines(guild_id, session)
    except Exception:
        log.exception("Couldn't read the session for the new name's rules")
        return None
    kept = [x.text for x in lines if x.user_id in agreed and not x.sidebar]
    return kept or None  # (nothing left to test the words against: no rule, as unread)


async def fix_transcript(
    transcripts: Any,
    memory: Any,
    guild_id: int,
    campaign_id: str,
    *,
    entity_id: str,
    session_id: str | None,
    batch: int,
    rules: Sequence[str],
    on_start: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """This session's cleaned transcript follows the rules just made, in the yes's Undo, as Fix
    spelling's does (#2170): only for an entry everyone at the table may read about (the
    transcript is open to the whole server), never inside a longer name, and never completing a
    name players must not read. `FIXED` (nothing to fix counts), `LATER` (from now on only) or
    `FAILED`. `on_start` runs before the lines are changed (Discord shows the wait). Never
    raises."""
    if not rules:
        return FIXED
    try:
        entity = await memory.entity(guild_id, campaign_id, entity_id)
        held = await memory.entries_held(guild_id, campaign_id)
        if (
            transcripts is None
            or session_id is None
            or entity is None
            or entity.type in DM_ONLY_TYPES
            or entity.id not in held
        ):
            return LATER
        if on_start is not None:
            await on_start()  # (the wait, #2564: one session's lines, one change for each spelling)
        everyone = await memory.aliases(guild_id, campaign_id, include_secret=True)
        entities = await memory.entities(guild_id, campaign_id)
        private = {e.id: e.name for e in entities if e.id not in held or e.type in DM_ONLY_TYPES}
        hidden = [a.text for a in everyone if a.secret or a.entity_id in private]
        hidden += private.values()
        now = int(time.time())
        for old in rules:
            longer = [a.text for a in everyone if a.key != name_key(old) and contains(a.text, old)]
            done = await transcripts.respell_names(
                guild_id,
                campaign_id,
                batch,
                old,
                entity.name,
                longer,
                hidden,
                now=now,
                forget_before=now - int(memory.keep_days * 86400),
                session_id=session_id,
            )
            log.info("New name's rules: %d lines of this session", done.lines)  # counts only
    except Exception:
        log.exception("Couldn't fix the session's transcript for a new name's rules")
        return FAILED
    return FIXED


__all__ = ["FAILED", "FIXED", "LATER", "fix_transcript", "session_lines"]
