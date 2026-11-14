"""The website's two lists of "who has played with whom" (#4226): a DM's own remembered players,
with "Forget this player", and a player's "DMs who remember you", with Clear. Four database doors
(0450) do the work; each is gated on the signed-in person themselves, so nobody reads another
person's list. Names are the Discord names last seen; no campaign, character or story is ever in
either list."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

_MAX_ID = 2**63 - 1


def _id(raw: str) -> int:
    """A Discord id from the address, or `not_found` (a bad one never reaches the database)."""
    # (the length is checked first: Python refuses to read a run of thousands of digits as a number)
    if len(raw) > 19 or not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= _MAX_ID:
        raise RecapError("not_found")
    return int(raw)


async def _door(db: Database, session: Session, call: str, *args: Any) -> Any:
    async with db.user(session.user_id, session=session.id_hash) as conn:
        cur = await conn.execute(f"SELECT {call} AS r", args)
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return got


async def remembered(db: Database, session: Session) -> dict[str, Any]:
    """`{items: [{player, name, since, last, campaigns}]}`, the latest first: the signed-in DM's."""
    got = await _door(db, session, "dmbot_web_dm_players_list()")
    return {
        "items": [
            {
                "player": str(r["player"]),
                "name": str(r["name"]),
                "since": int(r["since"]),
                "last": int(r["last"]),
                "campaigns": int(r["campaigns"]),
            }
            for r in got
        ]
    }


async def forget_player(db: Database, session: Session, player: str) -> dict[str, int]:
    """ "Forget this player": removes the one row; `gone` when there was none."""
    got = await _door(db, session, "dmbot_web_dm_players_forget(%s)", _id(player))
    if int(got["removed"]) == 0:
        raise RecapError("gone")
    return {"removed": int(got["removed"])}


async def remembered_by(db: Database, session: Session) -> dict[str, Any]:
    """`{items: [{dm, name, since, campaigns}]}`: the DMs who remember the signed-in person."""
    got = await _door(db, session, "dmbot_web_players_dms()")
    return {
        "items": [
            {
                "dm": str(r["dm"]),
                "name": str(r["name"]),
                "since": int(r["since"]),
                "campaigns": int(r["campaigns"]),
            }
            for r in got
        ]
    }


async def clear(db: Database, session: Session, dm: str) -> dict[str, int]:
    """ "Clear": one DM's memory of the signed-in person, or every DM's with `all`."""
    got = await _door(db, session, "dmbot_web_players_clear(%s)", 0 if dm == "all" else _id(dm))
    return {"removed": int(got["removed"])}
