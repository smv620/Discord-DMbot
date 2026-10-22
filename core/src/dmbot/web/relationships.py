"""The relationship web on the website (#2323): the names around one name and how they are
connected, for the page that draws them.

The website's database role has no right on the memory tables, so the data comes from one database
door (`dmbot_web_relationships`, schema.py). A DM of the campaign gets everything confirmed, secret
connections and names included (flagged); any other member of the server gets the players' view, the
same rule as the All names page; anyone not in the server, another campaign's ref or a name the
caller may not see is "not found". This module only shapes the door's rows: the connections between
two names become one edge with a label, and the words for each come from `memory/link_words.py`, the
same words as the Discord name card.

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.link_words import BOTH_WAYS, link_words
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_HOPS = 4
MAX_NODES = 1000  # the most names one answer draws, then `truncated`
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def label_of(links: list[dict[str, Any]]) -> dict[str, Any]:
    """The connection that names an edge: the latest confirmed one, and never a secret one while a
    known one exists. `links` are the door's, oldest first."""
    known = [x for x in links if not x["secret"]]
    return (known or links)[-1]


def edges_from(rows: list[dict[str, Any]], *, can_edit: bool) -> list[dict[str, Any]]:
    """The door's connections grouped one edge for each pair of names (`a` the smaller ID), each
    with its `label` (the words of the label connection), `more` (how many others there are) and
    every connection as `links`: {id, words, secret, direction}. `direction` says which way the
    words read between the pair: "a-b", "b-a", or "both" for the ones that read the same either
    way. A connection's `secret` is only sent to a DM (the door sends none to anyone else)."""
    pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        a, b = sorted((str(row["a"]), str(row["b"])))
        if row["predicate"] in BOTH_WAYS:
            direction = "both"
        else:
            direction = "a-b" if str(row["a"]) == a else "b-a"
        pairs.setdefault((a, b), []).append(
            {
                "id": row["id"],
                "words": link_words(str(row["predicate"]), str(row["detail"] or "")),
                "secret": bool(row["secret"]) if can_edit else False,
                "direction": direction,
                "at": int(row["at"]),
                "secretRow": bool(row["secret"]),
            }
        )
    out = []
    for (a, b), links in sorted(pairs.items()):
        links.sort(key=lambda x: (x["at"], x["id"]))
        chosen = label_of([{**x, "secret": x["secretRow"]} for x in links])
        out.append(
            {
                "a": a,
                "b": b,
                "label": chosen["words"],
                "more": len(links) - 1,
                "links": [{k: x[k] for k in ("id", "words", "secret", "direction")} for x in links],
            }
        )
    return out


def _refuse_bad(center: str | None, hops: int) -> None:
    if center is not None and not ID_PATTERN.match(center):
        raise RecapError("not_found")
    if not 1 <= hops <= MAX_HOPS:
        raise RecapError("bad_hops")


async def _door(
    db: Database,
    session: Session,
    ref: str,
    center: str | None,
    hops: int,
    *,
    size_only: bool,
) -> dict[str, Any]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    _refuse_bad(center, hops)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_relationships(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, center, hops, MAX_NODES, size_only),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return dict(got)


async def read_web(
    db: Database, session: Session, ref: str, *, center: str | None = None, hops: int = 2
) -> dict[str, Any]:
    """The names within `hops` steps of `center` (the most connected name when none is given) and
    the confirmed connections among them. Past `MAX_NODES` the nearest are kept and `truncated` is
    true."""
    got = await _door(db, session, ref, center, hops, size_only=False)
    nodes = [{k: v for k, v in n.items() if k != "secret" or v is not None} for n in got["nodes"]]
    return {
        "center": got["center"],
        "nodes": nodes,
        "edges": edges_from(got["edges"], can_edit=bool(got["canEdit"])),
        "truncated": bool(got["truncated"]),
        "maxHops": MAX_HOPS,
        "canEdit": bool(got["canEdit"]),
    }


async def read_size(
    db: Database, session: Session, ref: str, *, center: str | None = None, hops: int = 2
) -> dict[str, int]:
    """How many names are within `hops` steps, so the page can warn before drawing."""
    got = await _door(db, session, ref, center, hops, size_only=True)
    return {"names": int(got["names"])}
