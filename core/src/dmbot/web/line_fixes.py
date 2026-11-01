"""A line fixed from the website's Transcripts page (#2858; the parity row of #2554).

Discord's `/dmbot fix` has two paths and this copies both without widening either. The line's own
speaker suggests new words and they wait for the DM, who accepts or declines on the DM screen. A DM
of the campaign rewrites any spoken line at once, as `submit_dm` does. The door
(`dmbot_web_line_fix_add`) decides which from who is asking and repeats every rule it can check in
the database; the bot does the rest when it acts (the secret names, consent again, whether the line
changed since it was shown) and reports where it stands in the shared progress shape
(`dmbot.progress`, #2564). The website never writes a line and never touches the raw transcript.

A line is named the way the page's put-back is: its session by number, its speaker and the moment it
started. Counts, stages and fixed words are all that leave; the person's own words go in and are
blanked by the bot when it finishes.
"""

from __future__ import annotations

import re
import time
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.transcript.suggestions import tidy
from dmbot.web import transcripts
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# What a failed fix says, as the code the page turns into words (the bot's own list).
REASONS = {
    "player_stopped",
    "secret",
    "line_changed",
    "line_gone",
    "not_written",
    "not_available",
    "open",
    "many",
    "no_dm_screen",
    "failed",
}
# A speaker's suggestion is never checked against the secret names when it is made (Discord checks
# only when the DM accepts, so a player can never learn whether their words named a secret): a
# suggestion's wait can only end in these, whatever the bot recorded.
SUGGEST_REASONS = REASONS - {"secret", "line_changed", "not_written"}


async def request(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    speaker: str,
    started_ms: int,
    words: str,
    *,
    now: int,
    enforce_plans: bool,
) -> dict[str, str]:
    """Queue a fix and answer `{id, mode}` (`mode`: "suggest" or "rewrite"). The words are tidied
    as Discord tidies them (`suggestions.tidy`); refusals are `bad_text`, `bad_choice` (a speaker
    or time that is not one), and the door's own (`not_yours`, `not_recorded`, `same`, `open`,
    `many`, `no_dm_screen`, `queue_full`). A session, line or campaign that is not there for this
    person is `not_found`."""
    who = transcripts._speaker_id(speaker)
    if started_ms < 0 or started_ms > 2**62:
        raise RecapError("bad_choice")
    text = tidy(words)
    if text is None:
        raise RecapError("bad_text")
    guild_id, campaign_id, sid, _began = await transcripts._session_id(
        db, session, ref, number, now=now, enforce_plans=enforce_plans
    )
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_line_fix_add(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, sid, who, started_ms, text),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"id": str(got["id"]), "mode": str(got["mode"])}


async def status(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    fix_id: str,
    *,
    now: int,
    enforce_plans: bool,
    moment: int | None = None,
) -> dict[str, Any]:
    """Where the person's own fix stands: the shared progress shape plus `result` ("sent" for a
    suggestion now with the DM, "changed" for a rewrite, else null), `mode`, and `undo` (a rewrite
    Discord's fix notes can still take back). Nobody else's fix, and never the words."""
    if not ID_PATTERN.match(fix_id):
        raise RecapError("not_found")
    guild_id, campaign_id, _sid, _began = await transcripts._session_id(
        db, session, ref, number, now=now, enforce_plans=enforce_plans
    )
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_line_fix_read(%s, %s, %s) AS r", (guild_id, campaign_id, fix_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    when = int(time.time()) if moment is None else moment
    allowed = SUGGEST_REASONS if got["mode"] == "suggest" else REASONS
    reason = got["error"] or None
    shape = progress.shape(
        got["stage"],
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=when,
        reason=reason if reason in allowed else ("failed" if reason else None),
    )
    done = got["state"] == "done"
    return {
        **shape,
        "mode": got["mode"],
        "result": got["result"] if done else None,
        "undo": bool(got["undo"]) if done else False,
    }
