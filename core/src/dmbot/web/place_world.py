"""The website's read of a place's land, climate and ruler (#4287, slice 1): for a DM of the
campaign only. One database door (`dmbot_web_place_world`, 0463) answers NULL to anyone else, so
a player or a stranger gets "not allowed" and never a row; what players may see of these is a
separate piece (#4311) that never copies the players' rule into SQL. This module only shapes the
door's rows: a `{land, climate, ruler}` for the page's menus, each with who gave it, plus the
campaign's groups for the ruler menu. Words and counts only: no row holds a story sentence."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import map_facts
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


def _row(r: dict[str, Any]) -> dict[str, Any]:
    layer = str(r["layer"])
    return {
        "layer": layer,
        "value": str(r["value"]),
        "words": str(r["words"]),
        "group": str(r["group"]) if r.get("group") else None,
        "groupName": str(r["groupName"]) if r.get("groupName") else None,
        "how": str(r["how"]),
        "part": str(r["part"]),
        # "read" until the DM confirms it: the page marks it "(DMbot read this)"
        "read": str(r["status"]) != map_facts.CONFIRMED,
        "source": str(r["source"]),
        "shown": bool(r["tableKnows"]),
    }


async def read(db: Database, session: Session, ref: str, place_id: str) -> dict[str, Any]:
    """`{rows: [...], groups: [{id, name}]}` for one place. `not_allowed` for anyone who is not a DM
    of the campaign; `not_found` for a name that is not a confirmed place there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(place_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_place_world(%s, %s, %s) AS r", (guild_id, campaign_id, place_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    return {
        "level": got.get("level"),  # the place's size and form decide what may be set on it
        "form": got.get("form"),
        "rows": [_row(r) for r in got["rows"]],
        "groups": [{"id": str(g["id"]), "name": str(g["name"])} for g in got["groups"]],
    }
