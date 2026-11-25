"""The small, fast read of a running job's counts (#3534, part 3): an upload's or an import's stage
and counts and nothing else, for a DM of the campaign only, from `dmbot_web_wait_counts` (0630).

The page asks every half second while a job runs and its tab is visible, so this does no more than
one keyed read; the full progress answers (`lore.read`, `imports.progress`) stay what the page
shows between and after. Stages, counts and times only: never a word of a document, a transcript,
a name or a question.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

KINDS = ("lore", "import")
_ID = re.compile(r"[0-9a-f]{32}")


async def read(
    db: Database, session: Session, ref: str, kind: object, ident: object
) -> dict[str, Any]:
    """A lore upload: `{kind, state, stage, done, total, wordsDone, callsMade, namesFound,
    linksFound, createdAt, updatedAt, serverNow}`. An import: `{kind, serverNow, sessions: [{file,
    state, scanFinished, stepDone, stepTotal, wordsDone, callsMade, startedAt, updatedAt}]}`.
    Anyone but a DM of the campaign, a job that is not there, or a bad kind or id gets
    `not_found`."""
    if kind not in KINDS or not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_wait_counts(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, kind, ident),
        )
        found = await cur.fetchone()
    got = found["r"] if found is not None else None
    if not isinstance(got, dict):
        raise RecapError("not_found")
    return got
