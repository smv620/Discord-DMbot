"""A road's or route place's condition on the website (#4451, the web half of #4258 slice 4a):
"🚧 Condition: Closed since session 5 (was open)" on a known road and on a route place, set, taken
off, kept and shown or hidden by the DM, as Discord's "🚧 Condition", "✅ Condition is right" and
"🙈 / 👁️". One database door (`dmbot_web_road_conditions`, migration 0591) gives the conditions for
the ids a page draws; it tells a player only of a condition the DM kept and showed. This module
attaches `condition` to the page and queues the three actions through the one queue door; the bot
runs the store's own `set_route_condition`, `keep_road_condition` and `show_road_condition`. The
page's Undo is the route length one (`route-undo`). Words and ids only."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import map_facts, route_condition
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

WORDS_MAX = map_facts.WORDS_MAX  # the card's own limit
_ID = re.compile(r"^[0-9a-f]{32}$")
ON = {"connection": "c", "place": "p"}
VALUES = (*route_condition.VALUES, "none")  # (`none` is "Not sure": take the condition off)
OTHER = "o"  # (the queue text's one letter for "Something else…", so the longest text fits 100)


def attach(page: dict[str, Any], rows: list[dict[str, Any]], full: bool) -> None:
    """Marks each road (`road: true`, the DM's own view: a road with no condition yet is a road all
    the same, so the page can offer 🚧 Condition) and adds `condition` (`{value, words, since,
    before}`; `read` and `shown` only for the DM's own view) to each connection and each name of a
    graph page that has one, in place. `before` is `{value, words}` of the one it ended."""
    by_id = {(str(r["kind"]), str(r["id"])): r for r in rows}

    def one(kind: str, item: dict[str, Any]) -> None:
        row = by_id.get((kind, str(item["id"])))
        if row is None:
            return
        if full:
            item["road"] = True
        if row.get("value") is None:
            return
        condition: dict[str, Any] = {
            "value": str(row["value"]),
            "words": str(row.get("words") or ""),
            "since": row.get("since"),
            "before": row.get("before"),
        }
        if full:
            condition["read"] = bool(row.get("read"))
            condition["shown"] = bool(row.get("shown"))
        item["condition"] = condition

    for edge in page.get("edges", []):
        for link in edge["links"]:
            one("connection", link)
    for node in [*page.get("nodes", []), *page.get("tray", [])]:
        one("place", node)


async def with_road_conditions(
    db: Database, session: Session, ref: str, page: dict[str, Any], *, full: bool
) -> None:
    """Reads the door for this page's ids and adds each road's `condition`. `full`: the DM's own
    view (anything else gets only what the DM kept and showed: the door sends nothing more)."""
    ids = sorted(
        {str(link["id"]) for edge in page.get("edges", []) for link in edge["links"]}
        | {str(n["id"]) for n in [*page.get("nodes", []), *page.get("tray", [])]}
    )
    if not ids:
        return
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_road_conditions(%s, %s, %s) AS r", (guild_id, campaign_id, ids)
        )
        found = await cur.fetchone()
    rows = found["r"] if found is not None and found["r"] is not None else []
    attach(page, rows, full)


def _of_road(body: object) -> dict[str, object]:
    """A road's id from a body that names none of its kind: only a connection has a read to keep
    and a table to be told."""
    return {"on": "connection", "id": body.get("id") if isinstance(body, dict) else None}


def _target(body: object) -> tuple[str, str]:
    on = body.get("on") if isinstance(body, dict) else None
    target = body.get("id") if isinstance(body, dict) else None
    if on not in ON or not isinstance(target, str) or not _ID.fullmatch(target):
        raise RecapError("bad_action")
    return ON[str(on)], target


async def queue_set(db: Database, session: Session, ref: str, body: object) -> str:
    """🚧 Condition: `{"on": "connection" | "place", "id": <32 hex>, "value": "open" | "hard" |
    "closed" | "gone" | "none" | "other", "words": <1 to 60 characters, only for "other">}`; `none`
    is "Not sure". The id of the answer the page polls at `.../web/answers/{id}`."""
    on, target = _target(body)
    value = body.get("value") if isinstance(body, dict) else None
    words = body.get("words", "") if isinstance(body, dict) else None
    if not isinstance(words, str) or not (value in VALUES or value == "other"):
        raise RecapError("bad_action")
    clean = " ".join(words.split())
    if value == "other":
        if not clean:
            raise RecapError("bad_action")
        if len(clean) > WORDS_MAX:
            raise RecapError("too_long")
        what = f"{OTHER} {clean}"
    elif clean:
        raise RecapError("bad_action")  # (words belong to "other" alone)
    else:
        what = str(value)
    return await relationships._queue(
        db, session, ref, entity=None, action="road-condition", target="",
        text=f"{on} {target} {what}", flag=False,
    )  # fmt: skip


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """✅ Condition is right: `{"id": <a road's 32 hex>}`; DMbot's read becomes the DM's. Only a
    road (a connection) has a read to keep."""
    _on, target = _target(_of_road(body))
    return await relationships._queue(
        db, session, ref, entity=None, action="road-condition-keep", target="",
        text=f"c {target}", flag=False,
    )  # fmt: skip


async def queue_show(db: Database, session: Session, ref: str, body: object) -> str:
    """👁️ Show to players / 🙈 Hide from players: `{"id": <a road's 32 hex>, "shown": bool}`."""
    shown = body.get("shown") if isinstance(body, dict) else None
    if not isinstance(shown, bool):
        raise RecapError("bad_action")
    _on, target = _target(_of_road(body))
    return await relationships._queue(
        db, session, ref, entity=None, action="road-condition-show", target="",
        text=f"c {target} {int(shown)}", flag=False,
    )  # fmt: skip
