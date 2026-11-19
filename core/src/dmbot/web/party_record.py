"""The website's party record (#4778 part a2, party knowledge slices 2a and 4a; Supervisor's ruling
on #4537): when each player character was in the party, "Has left the party" with why, and "Back in
the party". One database door (`dmbot_web_party_record`, migration 0557) answers NULL to anyone who
is not a DM of the campaign. The changes go through the one queue door like every website action;
the bot runs the store's own `leave_party` and `back_in_party`, so each is one change with one Undo.
Names, ids, times and numbers only: no word of the story."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.party_record import ENDED
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ENTITY = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{characters: [{id, name, inParty, spans: [{from, to, ended, session}]}]}`, A to Z, each
    one's stretches oldest first (`to` and `ended` null while open; `session` the number of the
    session that stretch began at, or null). `not_allowed` for anyone who is not a DM of the
    campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_party_record(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "characters": [
            {
                "id": str(c["id"]),
                "name": str(c["name"]),
                "inParty": bool(c["inParty"]),
                "spans": [
                    {
                        "from": int(s["from"]),
                        "to": None if s["to"] is None else int(s["to"]),
                        "ended": None if s["ended"] is None else str(s["ended"]),
                        "session": None if s["session"] is None else int(s["session"]),
                    }
                    for s in c["spans"]
                ],
            }
            for c in got
        ]
    }


async def queue_leave(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Has left the party": `{"character": <32 hex>, "why": "left" | "retired" | "died" |
    "away"}`. The id of the answer the page polls; its change number is the Undo's."""
    who = body.get("character") if isinstance(body, dict) else None
    why = body.get("why") if isinstance(body, dict) else None
    if not isinstance(who, str) or not ENTITY.fullmatch(who) or why not in ENDED:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="party-leave", target="", text=f"{who} {why}",
        flag=False,
    )  # fmt: skip


async def queue_back(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Back in the party": `{"character": <32 hex>}`."""
    who = body.get("character") if isinstance(body, dict) else None
    if not isinstance(who, str) or not ENTITY.fullmatch(who):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="party-back", target="", text=who, flag=False
    )


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a leave or a return by the change number its answer carried. The bot reads the
    number again in this DM's own done answers before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="party-undo", target="", text=batch, flag=False
    )
