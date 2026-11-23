"""The website's World notes (#4285, slice 1c's door; the website half): DMbot's guesses about
places, languages, roads and feelings, for a DM of the campaign only. One database door
(`dmbot_web_world_notes`, migration 0481) answers NULL to anyone else, so a player or a stranger
gets "not allowed" and never a line. This module shapes the door's lines, and queues the four
actions (`world-keep`, `world-not`, `world-undo` and `world-bias`, the step pick of a feeling)
through the one queue door as the connection edits do: the bot applies each in one write, so each
is one Undo. Words, ids and counts only: no story text."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.maps import disagree, measure, measure_words
from dmbot.memory import place_peoples, standing
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

LINE_ID = re.compile(r"^(place|language|bias):[0-9a-f]{32}$")
# ✅ Keep also takes a road's line (#4677): its length becomes the DM's own. A road has no 🚫.
KEEP_LINE_ID = re.compile(r"^(place|language|road|bias):[0-9a-f]{32}$")
BIAS_LINE = re.compile(r"^bias:[0-9a-f]{32}$")
KEEP_AT_MOST = 25  # Discord's menu limit: Keep all only keeps lines the DM was shown
_BATCH = re.compile(r"^[0-9]{1,15}$")


def _line(kind: str, r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(r["id"]),
        "entityId": str(r["entityId"]),
        "name": str(r["name"]),
        "from": str(r.get("from") or ""),
        "fromSession": int(r["fromSession"]) if r.get("fromSession") else None,
    }
    if kind == "language":
        # the door names a language line's word `value` (a place's is its land or climate)
        out["language"] = str(r.get("language") or r.get("value") or "")
        return out
    out["layer"] = str(r["layer"])
    out["value"] = str(r["value"])
    out["words"] = str(r.get("words") or "")  # the DM's own words, when the value is "other"
    # a culture line's named culture (#5062): its name, when the row holds one instead of a lineage
    out["culture"] = str(r["culture"]) if r.get("culture") else None
    out["how"] = str(r["how"]) if r.get("how") else None
    out["rulerId"] = str(r["rulerId"]) if r.get("rulerId") else None
    out["ruler"] = str(r["ruler"]) if r.get("ruler") else None
    return out


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{places: [...], languages: [...]}`, the lines of Discord's 🌍 World notes list.
    `not_allowed` for anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_world_notes(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    by_kind = {str(g["kind"]): g.get("lines") or [] for g in got.get("groups", [])}
    return {
        "places": [_line("place", r) for r in by_kind.get("place", [])],
        "languages": [_line("language", r) for r in by_kind.get("language", [])],
        "roads": await _roads(db, session, guild_id, campaign_id),
        "feelings": await _feelings(db, session, guild_id, campaign_id),
    }


def _step_of(score: int) -> str:
    """The step a stored score stands for (the nearest of the five, as `biases._step_of`)."""
    return min(standing.SCORE_OF, key=lambda step: abs(standing.SCORE_OF[step] - score))


async def _feelings(
    db: Database, session: Session, guild_id: int, campaign_id: str
) -> list[dict[str, Any]]:
    """The feelings DMbot read from the lore and the DM has not answered (#4932; Discord's
    "Feelings" group, `world_notes._biases`): the door gives the rows, and the step is worked out
    from the score (the nearest of the five, as `memory/biases.py` does) and the target worded from
    the lineage's plural or the culture's name, the words Discord uses. A feeling
    is `bias:<row id>`; ✅ Keep and 🚫 Not right take it as any line, ✏️ Change picks another
    step."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_world_biases(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    out: list[dict[str, Any]] = []
    for r in got:
        target = str(r["culture"]) or place_peoples.LINEAGES.get(str(r["lineage"]), ("", ""))[1]
        if not target:
            continue
        out.append(
            {
                "id": f"bias:{r['id']}",
                "entityId": str(r["holderId"]),
                "name": str(r["holder"]),
                "target": target,
                "step": _step_of(int(r["score"])),
            }
        )
    return out


async def _roads(
    db: Database, session: Session, guild_id: int, campaign_id: str
) -> list[dict[str, Any]]:
    """The road lengths DMbot read that a kept map's straight line disagrees with (#4677; Discord's
    "Roads" group, `world_notes._roads`): worked out now by the same pure check on the door's rows,
    and worded by the same function, so the two cannot drift. A road is `road:<id>`; ✅ Keep takes
    it. Only the DM's maps count, and a travel time is never compared. Empty with no kept map."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_world_roads(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if not got or not got.get("roads") or not got.get("maps"):
        return []
    maps = [
        measure.MapInfo(
            str(m["id"]),
            str(m["title"]),
            str(m["kind"]),
            m["width"],
            m["height"],
            m["scale"],
            str(m["unit"] or ""),
            float(m["north"]),
            bool(m["northKnown"]),
            int(m["createdAt"]),
        )
        for m in got["maps"]
    ]
    pins = [
        measure.Pin(str(p["map"]), str(p["place"]), float(p["x"]), float(p["y"]))
        for p in got.get("pins") or []
    ]
    out: list[dict[str, Any]] = []
    for r in got["roads"]:
        off = disagree.check_distance(maps, pins, str(r["a"]), str(r["b"]), float(r["amount"]))
        if off is None:
            continue
        out.append(
            {
                "id": f"road:{r['id']}",
                "name": f"{r['aName']} to {r['bName']}",
                "note": measure_words.road_note(
                    str(r["stated"]),
                    r["fromSession"] is not None,
                    disagree.amount(off),
                    off.map.title,
                ),
                "fromSession": int(r["fromSession"]) if r.get("fromSession") else None,
            }
        )
    return out


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """✅ Keep: the ids of the lines the DM was shown (one is a single line, many is Keep all)."""
    ids = body.get("ids") if isinstance(body, dict) else None
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= KEEP_AT_MOST
        or not all(isinstance(i, str) and KEEP_LINE_ID.fullmatch(i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-keep", target="", text=" ".join(ids),
        flag=False,
    )  # fmt: skip


async def queue_not(db: Database, session: Session, ref: str, body: object) -> str:
    """🚫 Not right: one line."""
    line = body.get("id") if isinstance(body, dict) else None
    if not isinstance(line, str) or not LINE_ID.fullmatch(line):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-not", target="", text=line, flag=False
    )


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a ✅ or 🚫 made from the page, by the change number its answer carried. The bot
    reads the number again in the caller's own done actions before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-undo", target="", text=batch, flag=False
    )


async def queue_bias(db: Database, session: Session, ref: str, body: object) -> str:
    """✏️ Change on a feeling (#4932): `{"id": "bias:<32 hex>", "step": <one of the five>}`; the bot
    keeps it at that step as the DM's own (`change_read_bias`, one change, so one Undo)."""
    line = body.get("id") if isinstance(body, dict) else None
    step = body.get("step") if isinstance(body, dict) else None
    if not isinstance(line, str) or not BIAS_LINE.fullmatch(line) or step not in standing.STEPS:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-bias", target="", text=f"{line} {step}",
        flag=False,
    )  # fmt: skip
