"""The website's "Let me choose" for the join question (#4799, party knowledge slice 4c, web half):
the sessions and story threads that have something to give a character who joined, and the pick.

The choices are the graph's own logic (`MemoryStore.join_choices`), so the bot works them out when
asked (`join-choices` on the queue) and keeps them for this DM (`web_join_choices`, migration
0569, swept after an hour); `read` gives them back through the DM-only door
`dmbot_web_join_choices`, with when they were worked out so the page can offer "Look again". A pick
(`queue_pick`) goes through the queue to `MemoryStore.join_choose`, which checks everything again,
so a stale page can never give more than the bot would now. Ids, counts and the story threads'
names only: no transcript or card word."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


async def read(db: Database, session: Session, ref: str, group: str) -> dict[str, Any]:
    """`{madeAt, choices}`: `choices` is null until the bot has worked them out for this DM (ask
    with `queue_read`), else `{members: [{id, name}], sessions: [{at, number, counts}], threads:
    [{id, name, counts}], sessionsInAll, threadsInAll, nobody}`; `counts` maps a member's id to how
    many things the pick would give them. `not_allowed` for anyone who is not a DM."""
    if not _ID.fullmatch(group):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_join_choices(%s, %s, %s) AS r", (guild_id, campaign_id, group)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    choices = got["choices"]
    if choices is None:
        return {"madeAt": None, "choices": None}
    names = choices.get("names", {})
    return {
        "madeAt": int(got["madeAt"]),
        "choices": {
            # (a character deleted since is dropped: there is nothing to name or give to)
            "members": [
                {"id": str(m), "name": str(names[m])} for m in choices["members"] if m in names
            ],
            "sessions": [
                {"at": int(s["at"]), "number": int(s["number"]), "counts": s["counts"]}
                for s in choices["sessions"]
            ],
            "threads": [
                {"id": str(t["id"]), "name": str(t["name"]), "counts": t["counts"]}
                for t in choices["threads"]
            ],
            "sessionsInAll": int(choices["sessionsInAll"]),
            "threadsInAll": int(choices["threadsInAll"]),
            "nobody": bool(choices["nobody"]),
        },
    }


async def queue_read(db: Database, session: Session, ref: str, body: object) -> str:
    """Ask the bot to work the choices out: `{"group": <the question's group>}`. The id of the
    answer the page polls (it is done when they are kept; then `read` has them)."""
    group = body.get("group") if isinstance(body, dict) else None
    if not (isinstance(group, str) and _ID.fullmatch(group)):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="join-choices", target="", text=group, flag=False
    )


async def queue_pick(db: Database, session: Session, ref: str, body: object) -> str:
    """One pick: `{"group", "session": <the session's start>}` or `{"group", "thread": <id>}`.
    The id of the answer the page polls."""
    group = body.get("group") if isinstance(body, dict) else None
    at = body.get("session") if isinstance(body, dict) else None
    thread = body.get("thread") if isinstance(body, dict) else None
    if not (isinstance(group, str) and _ID.fullmatch(group)):
        raise RecapError("bad_action")
    if isinstance(at, int) and not isinstance(at, bool) and thread is None and 0 < at < 10**15:
        pick = f"s{at}"
    elif isinstance(thread, str) and _ID.fullmatch(thread) and at is None:
        pick = f"t{thread}"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="join-choose", target="", text=f"{group} {pick}",
        flag=False,
    )  # fmt: skip
