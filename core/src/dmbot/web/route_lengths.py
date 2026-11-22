"""A route's stated length on the website (#4392, the web half of #4255): "🛣️ Length: two days by
cart" on a route connection and a route place, set, taken off or kept by the DM, as Discord's
"🛣️ Length" and "✅ Length is right". One database door (`dmbot_web_route_lengths`, migration 0589)
gives the lengths for the ids a page draws; it leaves out a length DMbot only read for anyone who
is not a DM of the campaign (players see one once the DM set or kept it). This module attaches
`length` to the page and queues the three actions through the one queue door; the bot runs the
store's own `set_route_length` and `keep_route_length`. Words and ids only."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import route_length
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

WORDS_MAX = route_length.WORDS_MAX  # the card's own limit
_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
ON = {"connection": "c", "place": "p"}


def attach(page: dict[str, Any], rows: list[dict[str, Any]], full: bool) -> None:
    """Marks each route (`route: true`, the DM's own view: a route with no length yet is a route
    all the same, so the page can offer 🛣️ Length) and adds `length` (`{words, read}`; `read` only
    for the DM's own view: DMbot read it and it is not yet the DM's) to each connection and each
    name of a graph page that has one, in place."""
    by_id = {(str(r["kind"]), str(r["id"])): r for r in rows}

    def one(kind: str, item: dict[str, Any]) -> None:
        row = by_id.get((kind, str(item["id"])))
        if row is None:
            return
        if full:
            item["route"] = True
        if row.get("words") is None:
            return
        length: dict[str, Any] = {"words": str(row["words"])}
        if full:
            length["read"] = row["from"] == "read"
        item["length"] = length

    for edge in page.get("edges", []):
        for link in edge["links"]:
            one("connection", link)
    for node in [*page.get("nodes", []), *page.get("tray", [])]:
        one("place", node)


async def with_route_lengths(
    db: Database, session: Session, ref: str, page: dict[str, Any], *, full: bool
) -> None:
    """Reads the door for this page's ids and adds each route's `length`. `full`: the DM's own view
    (anything else gets the words only, and the door sent it nothing DMbot only read)."""
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
            "SELECT dmbot_web_route_lengths(%s, %s, %s) AS r", (guild_id, campaign_id, ids)
        )
        found = await cur.fetchone()
    rows = found["r"] if found is not None and found["r"] is not None else []
    attach(page, rows, full)


async def queue_set(db: Database, session: Session, ref: str, body: object) -> str:
    """🛣️ Length: `{"on": "connection" | "place", "id": <32 hex>, "words": <at most 60 characters,
    empty takes it off>}`. The id of the answer the page polls at `.../web/answers/{id}`."""
    on = body.get("on") if isinstance(body, dict) else None
    target = body.get("id") if isinstance(body, dict) else None
    words = body.get("words", "") if isinstance(body, dict) else None
    if on not in ON or not isinstance(target, str) or not _ID.fullmatch(target):
        raise RecapError("bad_action")
    if not isinstance(words, str):
        raise RecapError("bad_action")
    clean = " ".join(words.split())
    if len(clean) > WORDS_MAX:
        raise RecapError("too_long")
    return await relationships._queue(
        db, session, ref, entity=None, action="route-length", target="",
        text=f"{ON[str(on)]} {target} {clean}".strip(), flag=False,
    )  # fmt: skip


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """✅ Length is right: `{"on": ..., "id": ...}`; DMbot's read becomes the DM's."""
    on = body.get("on") if isinstance(body, dict) else None
    target = body.get("id") if isinstance(body, dict) else None
    if on not in ON or not isinstance(target, str) or not _ID.fullmatch(target):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="route-length-keep", target="",
        text=f"{ON[str(on)]} {target}", flag=False,
    )  # fmt: skip


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a length change made from the page, by the change number its answer carried. The
    bot reads the number again in the caller's own done actions before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="route-undo", target="", text=batch, flag=False
    )
