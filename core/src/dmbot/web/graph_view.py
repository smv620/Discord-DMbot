"""The story graph as one viewer knows it, worked out by the one gate (#3143).

The website's API reads the whole graph from one source door and the knowers from another, then
this module keeps only what the gate (`memory.gate`) lets the viewer know and draws the rest in the
shapes the graph doors used to send (the page does not change). Pure: no database, so every rule
here is tested without one. Nothing here decides who may know anything or what a name is: that is
`gate.visible`, `gate.name_entries` and `gate.split_connections`. The result is built only from
what the gate lets through, never by removing things from the full graph, so a mistake here can
hide too much but cannot show a name the gate does not.

A viewer is the party (what every player character knows), one character, or a player's own
characters. The DM's own full graph is not this: it comes straight from the graph door.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from dmbot.memory import gate

ALIAS = "alias:"  # the id of a node that stands for a secret other name


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


@dataclass(frozen=True, slots=True)
class Graph:
    """What one viewer may see: nodes by id (a split node's id starts with "alias:"), connections
    as the doors sent them (`a`, `b` already on the split node where one applies), and which
    connections are split."""

    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    split: frozenset[str]
    visibility: str
    can_peek: bool
    limited: bool = False  # the source was cut at its limits (a very large campaign)


def build(src: dict[str, Any], kn: dict[str, Any], viewer: gate.Viewer) -> Graph:
    """The graph `viewer` may see, from the source door's answer and the knowers door's."""
    seen = gate.visible(viewer, statements_of(kn))
    rows = gate.NameRows(
        {str(e["id"]): e for e in kn["entities"]},
        kn["aliasRows"],
        [(str(a), str(c), int(t)) for a, c, t in kn["learned"]],
    )
    ids = [str(n["id"]) for n in src["names"]]
    texts: dict[str, str] = {}
    gate.name_entries(seen, viewer, ids, rows, texts_out=texts)  # (for the other names' words)
    kinds = {str(n["id"]): str(n["kind"]) for n in src["names"]}
    mains = {str(n["id"]): str(n["name"]) for n in src["names"]}
    # a connection that has ended, or came from the story plan, is not on the NPC pages either
    live = gate.drawn_connections(
        seen,
        [
            link
            for link in src["links"]
            if not link["ended"]
            and not link["plan"]
            and seen.knows(gate.CONNECTION, str(link["id"]))
        ],
        kn["aliasRows"],
    )
    split = gate.split_connections(seen, live, kn["aliasRows"], texts)
    # an other name that facts are split onto is a node of its own for this viewer, so it does not
    # also name the person (or the one person would be drawn twice); a name nothing is split onto
    # for this viewer still names them
    named = gate.name_entries(seen, viewer, ids, rows, on_their_own=set(split.nodes))
    shown: dict[str, str] = {}  # entry id -> the name the viewer is shown
    for i in ids:
        name = named.get(i)
        if name is not None and seen.knows(gate.ENTRY, i):
            shown[i] = name
    nodes: dict[str, dict[str, Any]] = {
        i: {
            "id": i,
            "name": name,
            "kind": kinds[i],
            "revealed": True,
            "quests": None,
            "secret": None,
        }
        for i, name in shown.items()
    }
    edges: list[dict[str, Any]] = []
    touched: set[str] = set()
    for link in live:
        a, b = str(link["a"]), str(link["b"])
        via = split.links.get(str(link["id"]))
        if via is not None:
            entity = split.nodes[via][0]
            a = ALIAS + via if a == entity else a
            b = ALIAS + via if b == entity else b
        if any(not (end.startswith(ALIAS) or end in shown) for end in (a, b)):
            continue
        if via is not None:
            touched.add(via)
        # the DM's own note on a connection could name a main name (the players' graph has always
        # blanked it): it is sent only when both ends are shown by their main names
        changed = via is not None or any(
            end.startswith(ALIAS) or shown[end] != mains[end] for end in (a, b)
        )
        edges.append(
            {
                "id": link["id"], "a": a, "b": b, "predicate": link["predicate"],
                "detail": "" if changed else link["detail"], "directional": link["directional"],
                "secret": link["secret"], "at": link["at"], "held": None, "knownBy": None,
                "decided": None, "revealed": True, "pinned": False,
            }
        )  # fmt: skip
    for via in sorted(touched):
        entity, text = split.nodes[via]
        nodes[ALIAS + via] = {
            "id": ALIAS + via, "name": text, "kind": kinds.get(entity, "character"),
            "revealed": True, "quests": None, "secret": None,
        }  # fmt: skip
    return Graph(
        nodes,
        edges,
        frozenset(str(link["id"]) for link in live if str(link["id"]) in split.links),
        str(src["visibility"]),
        bool(src["isDm"]) or src["visibility"] in ("peek", "open"),
        bool(src.get("limited", False)),
    )


def _plain(graph: Graph) -> list[dict[str, Any]]:
    """The connections that join two real names (a split connection hangs off its own node)."""
    return [e for e in graph.edges if str(e["id"]) not in graph.split]


def _order(graph: Graph, i: str) -> tuple[str, str]:
    return (str(graph.nodes[i]["name"]).lower(), i)


def _degrees(graph: Graph) -> dict[str, int]:
    degree = {i: 0 for i in graph.nodes if not i.startswith(ALIAS)}
    for e in _plain(graph):
        degree[str(e["a"])] += 1
        degree[str(e["b"])] += 1
    return degree


def web_of(graph: Graph, center: str | None, hops: int, cap: int) -> dict[str, Any] | None:
    """The `/web` answer in the shape the graph door sent: the names within `hops` steps of
    `center` (the most connected name when none is given), nearest first, at most `cap`, and the
    connections among them, plus the split nodes they touch. None when `center` is a name this
    viewer is not shown (the caller says "not found")."""
    degree = _degrees(graph)
    start = center
    center_alias: str | None = None
    if start is not None and start.startswith(ALIAS):
        center_alias = start
        far = next(
            (
                str(e["b"]) if str(e["a"]) == start else str(e["a"])
                for e in sorted(graph.edges, key=lambda e: (e["at"], e["id"]))
                if str(e["id"]) in graph.split and start in (str(e["a"]), str(e["b"]))
            ),
            None,
        )
        start = far
        if start is None or start not in degree:
            return None
    if start is None:
        start = min(degree, key=lambda i: (-degree[i], *_order(graph, i)), default=None)
    elif start not in degree:
        return None
    head: dict[str, Any] = {
        "status": "ok", "canEdit": False, "seesAll": False, "view": "players",
        "visibility": graph.visibility, "canPeek": graph.can_peek,
    }  # fmt: skip
    if start is None:
        return {**head, "center": None, "names": 0, "nodes": [], "edges": [], "truncated": False}
    adjacent: dict[str, list[str]] = {i: [] for i in degree}
    for e in _plain(graph):
        adjacent[str(e["a"])].append(str(e["b"]))
        adjacent[str(e["b"])].append(str(e["a"]))
    depth = {start: 0}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        if depth[here] >= hops:
            continue
        for there in adjacent[here]:
            if there not in depth:
                depth[there] = depth[here] + 1
                queue.append(there)
    within = sorted(depth, key=lambda i: (depth[i], *_order(graph, i)))
    kept = within[: max(cap, 1)]
    keep = set(kept)
    edges = [e for e in _plain(graph) if str(e["a"]) in keep and str(e["b"]) in keep]
    virtual = [
        e
        for e in graph.edges
        if str(e["id"]) in graph.split
        and all(str(end).startswith(ALIAS) or str(end) in keep for end in (e["a"], e["b"]))
    ]
    alias_nodes = sorted(
        {str(end) for e in virtual for end in (e["a"], e["b"]) if str(end).startswith(ALIAS)},
        key=lambda i: (str(graph.nodes[i]["name"]), i),
    )
    return {
        **head,
        "center": center_alias or start,
        "names": len(within) + len(alias_nodes),
        "truncated": len(within) > max(cap, 1) or graph.limited,
        "nodes": [graph.nodes[i] for i in kept] + [graph.nodes[i] for i in alias_nodes],
        "edges": sorted([*edges, *virtual], key=lambda e: (e["at"], e["id"])),
    }


def overview_of_graph(graph: Graph, cap_names: int, cap_pairs: int) -> dict[str, Any]:
    """The `/web/overview` answer in the shape the overview door sent: every name the viewer is
    shown (at most `cap_names`, then the split nodes), and which two names each connection joins,
    once for each pair, in a fixed order."""
    real = sorted(
        (i for i in graph.nodes if not i.startswith(ALIAS)), key=lambda i: _order(graph, i)
    )
    aliases = sorted(
        (i for i in graph.nodes if i.startswith(ALIAS)),
        key=lambda i: (str(graph.nodes[i]["name"]), i),
    )
    pairs = sorted({tuple(sorted((str(e["a"]), str(e["b"])))) for e in graph.edges})
    cap = max(cap_pairs, 1)
    return {
        "status": "ok", "canEdit": False, "seesAll": False, "view": "players",
        "visibility": graph.visibility, "canPeek": graph.can_peek,
        "total": len(real) + len(aliases), "connections": len(graph.edges),
        "names": [graph.nodes[i] for i in real[: max(cap_names, 1)]]
        + [graph.nodes[i] for i in aliases],
        "pairs": [list(p) for p in pairs[:cap]], "pairsMore": len(pairs) > cap or graph.limited,
    }  # fmt: skip
