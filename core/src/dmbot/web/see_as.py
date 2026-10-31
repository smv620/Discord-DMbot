"""The story graph seen as the party, a player character or an NPC, for the DM (#2999).

The DM's graph is the whole truth. "See as" draws it the way a character knows it: the same
function the bot uses (`memory.gate.visible`) works out what that viewer may know, and this module
only removes the rest. Nothing is worked out twice: the rules live in the gate, the website's
database role has no right on the memory tables, and one DM-only door
(`dmbot_web_knowers_data`, migration 0309) hands over who knows what.

A name the viewer knows only through a connection or an other name is shown by the first other name
they know, or not at all (`gate.display_name`): knowing that a connection exists never reveals a
main name. The DM's own marks (secret, held back, pinned, DMbot's checks, who knows) are removed
from everything shown, so the picture is the viewer's, not the DM's. Read-only; DM only.

Not here yet: the player view splits a name into its own node when a viewer knows some connections
only through a secret other name. This view shows such a name once, by the other name.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory import gate
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.relationships import read_web
from dmbot.web.sessions import Session

PARTY = "party"  # the viewer that is every player character at once


def statements_of(data: dict[str, Any]) -> gate.Statements:
    """The door's lists as the gate's input, in the shape `gate.load` makes: only live statements
    count, and the same character listed twice for one statement keeps its last certainty."""
    live = set(data["liveEntries"])
    states = {i: e for i, e in data["states"].items()}
    connections = {i: (ends[0], ends[1]) for i, ends in data["connections"].items()}
    aliases = {i: tuple(ends) for i, ends in data["aliases"].items()}
    knowers: dict[tuple[str, str], dict[str, float]] = {}
    for kind, statement, character, sure in data["knowers"]:
        if (
            (kind == gate.ENTRY and statement in live)
            or (kind == gate.PROPERTY and statement in states)
            or (kind == gate.CONNECTION and statement in connections)
            or (kind == gate.ALIAS and statement in aliases)
        ):
            knowers.setdefault((kind, statement), {})[character] = float(sure)
    groups: dict[str, set[str]] = {}
    for member, group in data["groups"]:
        groups.setdefault(member, set()).add(group)
    return gate.Statements(
        knowers=knowers,
        groups={m: frozenset(g) for m, g in groups.items()},
        states=states,
        connections=connections,
        aliases=aliases,
        player_characters=frozenset(data["playerCharacters"]),
    )


def viewer_of(data: dict[str, Any], viewer: str) -> tuple[gate.Viewer, str]:
    """Who the DM asked to see as, and the words for it. A name that is not a character of this
    campaign is "not found" (nothing says whether it exists elsewhere)."""
    if viewer == PARTY:
        return gate.Viewer.the_party(), "The party"
    for v in data["viewers"]:
        if v["id"] == viewer:
            return gate.Viewer.character(viewer), str(v["name"])
    raise RecapError("not_found")


def narrow(
    graph: dict[str, Any], seen: gate.Visible, data: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The nodes and edges of the DM's graph that `seen` may know, with the DM's marks removed."""
    others: dict[str, list[tuple[str, str]]] = {}
    for alias_id, entity_id, text in data["aliasNames"]:
        others.setdefault(entity_id, []).append((alias_id, str(text)))
    shown: dict[str, str] = {}
    nodes = []
    for node in graph["nodes"]:
        node_id = str(node["id"])
        name = gate.display_name(seen, node_id, str(node["name"]), others.get(node_id, ()))
        if node_id not in seen.entries or name is None:
            continue
        shown[node_id] = name
        # `secret`, `quests` and the DM's `revealed` mean nothing to a character
        nodes.append({**node, "name": name, "revealed": True, "secret": None, "quests": None})
    edges = []
    for edge in graph["edges"]:
        if edge["a"] not in shown or edge["b"] not in shown:
            continue
        links = [
            {
                **{k: v for k, v in link.items() if k not in ("who", "knownBy")},
                "secret": False,
                "revealed": True,
                "pinned": False,
                "check": "",
            }
            for link in edge["links"]
            if seen.knows(gate.CONNECTION, str(link["id"]))
        ]
        if not links:
            continue
        label = edge["label"] if any(x["words"] == edge["label"] for x in links) else None
        edges.append(
            {
                **edge,
                "label": label if label is not None else links[-1]["words"],
                "more": len(links) - 1,
                "links": links,
            }
        )
    return nodes, edges


async def read_see_as(
    db: Database,
    session: Session,
    ref: str,
    viewer: str,
    *,
    center: str | None = None,
    hops: int = 2,
) -> dict[str, Any]:
    """The DM's story graph around `center`, drawn as `viewer` knows it: "party" (what every
    player character knows) or the id of a character of this campaign. Same shape as the plain
    graph (`read_web`), plus `seeAs: {viewer, name}`; `canEdit` is false, since this is a look, not
    a place to change anything. DM only: anyone else is `not_allowed`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_knowers_data(%s, %s) AS r", (guild_id, campaign_id)
        )
        data = (await cur.fetchone() or {}).get("r")
    if data is None:
        raise RecapError("not_allowed")
    who, words = viewer_of(data, viewer)
    graph = await read_web(db, session, ref, center=center, hops=hops, view="full")
    nodes, edges = narrow(graph, gate.visible(who, statements_of(data)), data)
    return {
        **graph,
        "nodes": nodes,
        "edges": edges,
        "canEdit": False,
        "seeAs": {"viewer": viewer, "name": words},
    }
