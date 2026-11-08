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

import math
import time
from typing import Any

from dmbot.db import Database
from dmbot.memory import graph_review, isolated
from dmbot.memory.models import PLAYER_CHARACTER
from dmbot.web import all_names, name_checks, relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_ENTRIES = 300  # the page shows the largest groups first; past this many entries, a count


async def read_cleanup(
    db: Database, session: Session, ref: str, *, now: float | None = None
) -> dict[str, Any]:
    """The isolated groups of the campaign `ref`, largest first: `groups` (each `entries` and
    `links`), `total` (every isolated entry) and `truncated`, with `review`, the careful check's
    status line (#3741). `not_found` for anyone who is not a DM of the campaign (the door answers
    nothing), the same word as a campaign that is not there. `now` is the server's clock, so the
    page never works out a time itself."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_graph_cleanup(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
        cur = await conn.execute(
            "SELECT dmbot_web_graph_review_status(%s, %s) AS r", (guild_id, campaign_id)
        )
        status = (await cur.fetchone() or {}).get("r")
    got = found["r"] if found is not None else None
    if not isinstance(got, dict):
        raise RecapError("not_found")
    return {
        **shape(got),
        "review": review_line(status, time.time() if now is None else now),
    }


def review_line(status: object, now: float) -> dict[str, Any]:
    """The careful check's status for the clean-up page (#3741; Supervisor's ruling 2), counts and
    times only: `off` (the campaign does not share its story), `idle` (nothing fixed yet),
    `waiting` with `minutesLeft` until the check and `fixes`, `running` (the bot is checking now,
    with `fixes`), `done` with `missed`, or `stopped` when the last check gave up after its tries
    (CLAUDE.md: a wait that fails says so). Pure."""
    if not isinstance(status, dict) or not status.get("on"):
        return {"state": "off"}
    waiting = status.get("waiting")
    if isinstance(waiting, dict):
        fixes = int(waiting.get("fixes") or 0)
        if waiting.get("checkingSince"):
            return {"state": "running", "fixes": fixes}
        due = int(waiting.get("lastFixAt") or 0) + graph_review.WAIT_S
        return {"state": "waiting", "minutesLeft": max(0, math.ceil((due - now) / 60)),
                "fixes": fixes}  # fmt: skip
    last = status.get("last")
    if isinstance(last, dict):
        if last.get("stopped"):
            return {"state": "stopped"}
        return {"state": "done", "missed": int(last.get("missed") or 0)}
    return {"state": "idle"}


async def read_places(db: Database, session: Session, ref: str, entity_id: str) -> dict[str, Any]:
    """ "Where was it said?" for one character or group of the campaign (#3767): `places` (up to
    three windows of lines, newest first) and `morePlaces`, made ready as the Names page's are
    (`name_checks._with_places`: a line of someone who does not agree now is `{"removed": true}`).
    `not_found` for anyone who is not a DM of the campaign, and for an id that is not a live
    character or group of it. Words are read now and go to the page only; nothing is logged."""
    guild_id, campaign_id = _parse(ref)
    ent = name_checks._entity_id(entity_id)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_entity_places(%s, %s, %s) AS r", (guild_id, campaign_id, ent)
        )
        found = (await cur.fetchone() or {}).get("r")
    if not isinstance(found, dict) or ent not in found:
        raise RecapError("not_found")
    one = found[ent]
    card = name_checks._with_places({"name": str(one.get("name") or "")}, one)
    return {"places": card.get("places", []), "morePlaces": int(card.get("morePlaces", 0))}


def shape(got: dict[str, Any]) -> dict[str, Any]:
    """The door's answer as the page gets it (pure: the grouping is tested with no database)."""
    rows = {str(e["id"]): e for e in got.get("entries") or []}
    links = list(got.get("links") or [])
    entries_in = [
        isolated.Entry(
            i, str(e["type"]), str(e["status"]), bool(e.get("unnamed")),
            e.get("role") == PLAYER_CHARACTER, bool(e.get("party")),
        )
        for i, e in rows.items()
    ]  # fmt: skip
    groups = isolated.isolated_groups(
        entries_in,
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
    # (no kept player character: nothing can be isolated, and the page says why the list is empty,
    # #3767 item 2; the rule's own test, so the message and the list agree)
    no_pc = not isolated.has_player_character(entries_in)
    return {
        "groups": shown, "total": total, "truncated": count < total, "noPlayerCharacter": no_pc,
    }  # fmt: skip
