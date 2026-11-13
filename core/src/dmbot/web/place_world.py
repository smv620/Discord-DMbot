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
from dmbot.memory import gate, map_facts
from dmbot.web import graph_view, relationships
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


async def read_table(db: Database, session: Session, ref: str, place_id: str) -> dict[str, Any]:
    """What the table may be told of one place's land, climate and ruler, for any member of the
    server (#4311). Two steps, neither of which decides who may know a place: the door gives only
    the rows the DM confirmed and showed; then the gate says which entries this caller knows (a DM
    here sees the party's view, as the story graph does), and `map_facts.for_players`, the one
    players' rule, keeps a row only when the place, and for a ruler its group, are known. Words
    only: no group the caller does not know is named, and an unconfirmed reading is never there.
    The door is open to every member of the server; a member outside the campaign has no characters
    here, so the gate shows them nothing and they get `not_found` like anyone who does not know the
    place. A ruler group merged into another is named as the one that survives: the merge moves
    its rows."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(place_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_place_world_table(%s, %s, %s) AS r,"
            " dmbot_web_knowers_data(%s, %s) AS k",
            (guild_id, campaign_id, place_id, guild_id, campaign_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    known = found["k"] if found else None
    if got is None or known is None:
        raise RecapError("not_found")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    seen = gate.visible(relationships.viewer_of(known), graph_view.statements_of(known))
    asked = {place_id} | {str(r["group"]) for r in got["rows"] if r.get("group")}
    knows = {i for i in asked if seen.knows(gate.ENTRY, i)}
    if place_id not in knows:
        # A place this caller does not know answers exactly as a missing id does, so the route
        # is no way to find out that a place exists.
        raise RecapError("not_found")
    facts = [
        map_facts.Fact(
            id=str(r["id"]),
            place_id=place_id,
            layer=str(r["layer"]),
            value=str(r["group"] if r["layer"] == map_facts.CONTROL else r["value"]),
            words=str(r["words"]),
            how=str(r["how"]),
            part=str(r["part"]),
            status=map_facts.CONFIRMED,  # (the door gave only confirmed rows)
            source="dm",
            table_knows=True,  # (and only shown ones)
        )
        for r in got["rows"]
        if not r[
            "part"
        ]  # (a row about only part of a place is not shown on the website: it is sent to no one)
    ]
    names = {str(r["group"]): str(r["groupName"] or "") for r in got["rows"] if r.get("group")}
    return {
        "rows": [
            {
                "layer": f.layer,
                "value": "" if f.layer == map_facts.CONTROL else f.value,
                "words": f.words,
                "groupName": names.get(f.value) if f.layer == map_facts.CONTROL else None,
                "how": f.how,
                "part": f.part,
            }
            for f in map_facts.for_players(facts, knows)
        ]
    }
