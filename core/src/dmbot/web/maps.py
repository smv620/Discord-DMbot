"""The website's Maps page (#4347): a campaign's maps, cut by the player rule in the database door
(`dmbot_web_maps`, migration 0487), and the presses queued through the one queue door as the World
notes and connection edits are (`maps-use`, `maps-show`, `maps-remove`, `maps-takeback`,
`maps-undo`). The bot applies each through the map store in one write, so each is one Undo, and
rechecks who may do it. Words, ids and the picture's open link only: no file key, no story text."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAP_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{canManage, maps: [...]}`. A map is `{id, title, addedBy, via, mine, openUrl}` and, only for
    a DM and for a person's own maps, `shown` and `state` ("usable", "waiting" or "declined"). The
    door decides who sees which map; this only shapes its rows. Anyone who is not in the
    campaign's server gets `not_allowed`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_maps(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    maps = []
    for m in got["maps"]:
        row: dict[str, Any] = {
            "id": str(m["id"]),
            "title": str(m["title"]),
            "addedBy": str(m["poster"]),
            "via": "discord" if m["via"] == "discord" else "website",
            "mine": bool(m["mine"]),
            "openUrl": f"/pictures/{m['code']}",  # (the site's own open link, as the gallery's)
        }
        if "state" in m:  # (the door sends these only to a DM and for the person's own maps)
            row["shown"] = bool(m["shown"])
            row["state"] = str(m["state"])
        maps.append(row)
    return {"canManage": bool(got["isDm"]), "maps": maps}


def _map_id(value: object) -> str:
    if not isinstance(value, str) or not MAP_ID.fullmatch(value):
        raise RecapError("not_found")
    return value


async def _queue(db: Database, session: Session, ref: str, action: str, text: str) -> str:
    return await relationships._queue(
        db, session, ref, entity=None, action=action, target="", text=text, flag=False
    )


async def queue_use(db: Database, session: Session, ref: str, map_id: str, body: object) -> str:
    """A DM's yes or no to "A map from <player>: use it?" (only a DM; the bot checks)."""
    yes = body.get("yes") if isinstance(body, dict) else None
    if not isinstance(yes, bool):
        raise RecapError("bad_action")
    return await _queue(db, session, ref, "maps-use", f"{_map_id(map_id)} {'yes' if yes else 'no'}")


async def queue_show(db: Database, session: Session, ref: str, map_id: str, body: object) -> str:
    """Show to the table, or back to the DMs only (only a DM; the bot checks)."""
    shown = body.get("shown") if isinstance(body, dict) else None
    if not isinstance(shown, bool):
        raise RecapError("bad_action")
    word = "shown" if shown else "hidden"
    return await _queue(db, session, ref, "maps-show", f"{_map_id(map_id)} {word}")


async def queue_remove(db: Database, session: Session, ref: str, map_id: str) -> str:
    """A DM removes a map; the picture stays (only a DM; the bot checks)."""
    return await _queue(db, session, ref, "maps-remove", _map_id(map_id))


async def queue_takeback(db: Database, session: Session, ref: str, map_id: str) -> str:
    """A poster takes their own map back; the picture stays (the bot checks it is theirs)."""
    return await _queue(db, session, ref, "maps-takeback", _map_id(map_id))


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a Maps press by the change number its answer carried. The bot reads the number
    again in the sender's own done Maps presses before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await _queue(db, session, ref, "maps-undo", batch)
