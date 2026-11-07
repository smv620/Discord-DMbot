"""Where a connection came from, for the graph's line panel (#2946). The shape is the one posted on
#2934: `{type: "session", n}`, `{type: "upload", label}`, `{type: "dm"}` or `{type: "unknown"}`.

The DM also gets `quote` on the connection (the reader's own words for it, an upload's only).
The DM gets the full value. A player gets the session number, which they were at the table for,
and `unknown` for everything else: an upload's label is the DM's own name for it and can name a
private backstory, and "added by hand" tells a player how the DM works. Never a word of a transcript
or an upload. Worked out here, once, from the door's ids and numbers (`dmbot_web_link_sources`), and
added to the page the way the DM's know-words are; a connection the door does not know reads
`unknown`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import _guild_of, _scoped
from dmbot.web.sessions import Session

UNKNOWN: dict[str, Any] = {"type": "unknown"}


def source_value(row: Mapping[str, Any] | None, full: bool) -> dict[str, Any]:
    """One connection's source. `row`: the door's `{source, label, n}` for it, or None."""
    if row is None:
        return dict(UNKNOWN)
    label = str(row.get("label") or "")
    number = row.get("n")
    if full and label:
        return {"type": "upload", "label": label}
    if isinstance(number, int) and not label:
        return {"type": "session", "n": number}
    if full and row.get("source") == "dm":
        return {"type": "dm"}
    return dict(UNKNOWN)


def attach(page: dict[str, Any], rows: Mapping[str, Mapping[str, Any]], full: bool) -> None:
    """Adds `source` to every connection of a graph page, in place."""
    for edge in page.get("edges", []):
        for link in edge["links"]:
            row = rows.get(str(link["id"]))
            link["source"] = source_value(row, full)
            # the words an upload connection was read from: the DM's alone, and only when stored
            # (the door sends none to anyone else; this is the second lock, #3606)
            quote = str((row or {}).get("quote") or "")
            if full and quote:
                link["quote"] = quote


async def with_link_sources(
    db: Database, session: Session, ref: str, page: dict[str, Any], *, full: bool
) -> None:
    """Reads the door and adds each connection's `source` to `page`. `full`: the DM's own view
    (anything else gets the player's value). The rows go no further than this: they are not kept,
    logged or sent."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        # only this page's connections are read, not the whole campaign's on every load
        ids = sorted({str(link["id"]) for edge in page.get("edges", []) for link in edge["links"]})
        cur = await conn.execute(
            "SELECT dmbot_web_link_sources(%s, %s, %s) AS s", (guild_id, campaign_id, ids)
        )
        found = await cur.fetchone()
    # (a campaign that vanished meanwhile has no sources: every line reads `unknown`)
    links = found["s"]["links"] if found is not None and found["s"] is not None else []
    rows = {str(r["id"]): r for r in links}
    attach(page, rows, full)
