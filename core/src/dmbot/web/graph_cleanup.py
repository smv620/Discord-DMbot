"""The graph clean-up page's read (#3740): the characters and groups no chain links to the party.

The door (`dmbot_web_graph_cleanup`, DM-only) hands over the rows; the one rule that says who is
isolated (`memory/isolated.py`) runs here on them, the same function the Discord count runs, so the
page and the count never disagree. Each isolated group comes back with its entries (an unnamed
one labelled from its anchor's present name, as the Names page does), the links inside it (worded
as the map words them, the DM's view), and each entry's kept scene. Nothing is stored about who is
isolated: it is worked out on every load, so a group leaves the list as soon as any member is
linked to the party. Ids, names and scenes go to the page and no further; nothing is logged.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory import isolated
from dmbot.memory.models import PLAYER_CHARACTER
from dmbot.web import all_names, relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_ENTRIES = 300  # the page shows the largest groups first; past this many entries, a count


async def read_cleanup(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The isolated groups of the campaign `ref`, largest first: `groups` (each `entries` and
    `links`), `total` (every isolated entry) and `truncated`. `not_found` for anyone who is not a
    DM of the campaign (the door answers nothing), the same word as a campaign that is not there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_graph_cleanup(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found is not None else None
    if not isinstance(got, dict):
        raise RecapError("not_found")
    return shape(got)


def shape(got: dict[str, Any]) -> dict[str, Any]:
    """The door's answer as the page gets it (pure: the grouping is tested with no database)."""
    rows = {str(e["id"]): e for e in got.get("entries") or []}
    links = list(got.get("links") or [])
    groups = isolated.isolated_groups(
        (
            isolated.Entry(
                i, str(e["type"]), str(e["status"]), bool(e.get("unnamed")),
                e.get("role") == PLAYER_CHARACTER, bool(e.get("party")),
            )
            for i, e in rows.items()
        ),
        (
            isolated.Link(
                str(r["id"]), str(r["a"]), str(r["b"]), str(r["predicate"]), str(r["status"])
            )
            for r in links
        ),
    )  # fmt: skip
    total = sum(len(g) for g in groups)
    kept: list[list[str]] = []
    count = 0
    for group in groups:
        if count + len(group) > MAX_ENTRIES:
            if not kept:  # (one cluster bigger than the page: its first entries, and a count)
                kept.append(group[:MAX_ENTRIES])
                count = MAX_ENTRIES
            break
        kept.append(group)
        count += len(group)
    where = {i: n for n, group in enumerate(kept) for i in group}
    inside: list[list[dict[str, Any]]] = [[] for _ in kept]
    for r in links:  # (one pass: each link to the group holding both its ends)
        n = where.get(str(r["a"]))
        if n is not None and where.get(str(r["b"])) == n:
            inside[n].append(r)
    scenes = got.get("scenes") or {}
    shown: list[dict[str, Any]] = []
    for n, group in enumerate(kept):
        entries = []
        for i in group:
            e = rows[i]
            row: dict[str, Any] = {
                "id": i, "name": str(e["name"]), "kind": str(e["type"]),
                "waiting": e["status"] != "confirmed", "unnamed": bool(e.get("unnamed")),
                "unnamedWhat": e.get("unnamedWhat"), "unnamedAnchor": e.get("unnamedAnchor"),
            }  # fmt: skip
            all_names._label_unnamed(row)  # (labelled from the anchor's present name)
            if isinstance(scenes.get(i), str) and scenes[i].strip():
                row["scene"] = scenes[i]
            entries.append(row)
        edges = relationships.edges_from(
            inside[n], can_edit=True, is_dm=True, kinds={i: str(rows[i]["type"]) for i in group}
        )
        for edge in edges:
            for link in edge.get("links", []):
                # (the map's marks need reads this door does not make; the page does not show them)
                link.pop("revealed", None)
                link.pop("pinned", None)
        shown.append({"entries": entries, "links": edges})
    return {"groups": shown, "total": total, "truncated": count < total}
