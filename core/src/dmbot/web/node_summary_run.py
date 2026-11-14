"""The click on a node of the story graph (#3236, slice 2b): turn the graph one person may see into
the facts and neighbours `node_summary` words, and give back the text. Pure: the website's route
reads the two source doors and the rank inputs, and calls `summary_for`; nothing here touches a
database, so every rule is tested without one.

Who sees what is decided by the gate, through `graph_view.build`, never here:
- the **DM's** summary is built from the DM's full graph and all it holds;
- a **player's** summary is built from the graph the players' characters know together (the union of
  every player character's view), so it never holds a name or a connection no player character
  knows. The fact is marked `viewer_knows` only when the person who clicked has a character that
  knows it, which is what the spoiler gate (`needs_peek`) is worked out from;
- the rank orders and cuts both (owner, 2026-11-01), but it is never in the text, and the
  connection score a player's order uses is worked out from the connections that player's graph
  shows, never from a hidden one.

The route sends only `text`, `more`, `needs_peek` and `version`; the rank, the score and every id
stay here. While the spoiler gate is closed the text is empty and `more` is 0, so the number of
neighbours the caps left out (which counts what other characters know) is not sent either.

Accepted exposure (owner, 2026-11-01): the rank comes from the whole campaign, so it can decide
which of the entries a player may see are named first or cut. It orders and cuts what the player
is already allowed to see and is never in the text.

`clicker` is the DM (`Viewer.the_dm()`), a player (`Viewer.player(their characters)`), or the
table (`Viewer.the_party()`). A DM seeing as a non-player character is not a player's view and
gets None for what the players do not know.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dmbot.memory import gate, importance, importance_rank, node_summary
from dmbot.memory.node_summary import DM, PLAYER, Fact, Focus, Neighbour
from dmbot.memory.standing import DEED_VERB
from dmbot.web import graph_view

MAX_SECOND_HOP = 6  # connections read for each neighbour's own neighbours (the text names one)


@dataclass(frozen=True, slots=True)
class Result:
    """What the route sends: the text (empty while the spoiler gate is closed), how many
    neighbours the caps left out, whether the gate applies, and which version was built."""

    text: str
    more: int
    needs_peek: bool
    version: str  # "dm" or "players"


def ranks_of(inputs: Mapping[str, Any]) -> dict[str, int]:
    """The rank of every live entry from the rank-inputs door's answer (`dmbot_web_rank_inputs`):
    `entities` ({id, type, role, status, mergedInto}), `relations` ([subject, object, predicate]),
    `assessed` ([entity, seq, rank]), `heard` ([entity, session time]) and `private` (the entries
    said in a kept private scene, #4110; absent from an older door). The same functions the
    bot's reader uses, so the website and the bot cannot disagree. Used in Python only: the rank
    never goes to a browser."""
    entities: dict[str, Mapping[str, object]] = {
        str(e["id"]): {
            "type": e["type"], "role": e["role"], "status": e["status"],
            "merged_into": e.get("mergedInto"),
        }
        for e in inputs["entities"]
    }  # fmt: skip
    ended = importance_rank.roots(entities)
    newest: dict[str, tuple[int, int]] = {}
    for entity, seq, rank in inputs["assessed"]:
        if int(seq) > newest.get(str(entity), (0, 0))[0]:
            newest[str(entity)] = (int(seq), int(rank))
    heard: dict[str, set[int]] = {}
    for entity, at in inputs["heard"]:
        heard.setdefault(str(entity), set()).add(int(at))
    links, companions = importance_rank.links_from(
        ((str(a), str(b), str(p)) for a, b, p in inputs["relations"]), entities, ended
    )
    return importance_rank.ranks_from(
        entities, ended, importance_rank.newest_by_group(ended, newest), heard, links, companions,
        [str(e) for e in inputs.get("private", ())],  # (#4110: said in a kept private scene)
    )  # fmt: skip


def _when(value: object) -> int | None:
    """A connection's `at` as a number to sort by. The source door sends the creation time as a
    number or as an ISO time string; anything else sorts as unknown."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except ValueError:
        return None


def _edges(graph: graph_view.Graph) -> list[dict[str, Any]]:
    return [e for e in graph.edges if str(e["id"]) not in graph.split]


def deed_facts(
    edges: list[dict[str, Any]], nodes: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """A deed the DM recorded is `doer —did→ deed —affected→ viewer` (#1732). The summary reads it
    as what it means, one fact between the doer and each viewer ("rescued by the party"), and
    never names the deed entry itself as a neighbour (#3236 slice 3). The kind (attack, rescue…)
    is the `did` connection's detail and the effect ("helped", "hurt") is the `affected` one's.
    Both connections are the DM's alone, so a player's graph holds neither and this changes
    nothing for them."""
    deeds = {i for i, n in nodes.items() if n.get("kind") == "deed"}
    if not deeds:
        return edges
    doers: dict[str, list[dict[str, Any]]] = {}  # a deed may have several doers (the party)
    for e in edges:
        if e["predicate"] == "did" and str(e["b"]) in deeds:
            doers.setdefault(str(e["b"]), []).append(e)
    out = [e for e in edges if str(e["a"]) not in deeds and str(e["b"]) not in deeds]
    for e in edges:
        if e["predicate"] != "affected":
            continue
        for doing in doers.get(str(e["a"]), []):
            out.append(
                {
                    **e,
                    "id": f"{doing['id']}:{e['id']}",
                    "a": doing["a"],
                    "b": e["b"],
                    "predicate": "did",
                    "detail": str(doing["detail"] or ""),  # the deed's kind
                    # (read only by `_facts`: how it landed, as the one word the summary uses)
                    "effect": DEED_VERB.get(str(e["detail"] or ""), ""),
                    "at": doing["at"] if doing.get("at") is not None else e.get("at"),
                }
            )
    return out


def _facts(
    edges: list[dict[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]],
    here: str,
    other: str,
    clicker_knows: set[str] | None,
) -> tuple[Fact, ...]:
    """The facts joining `here` and `other`. `clicker_knows`: the connection ids the person who
    clicked may see (None for the DM, who sees all)."""
    out: list[Fact] = []
    for e in edges:
        a, b = str(e["a"]), str(e["b"])
        if {a, b} != {here, other}:
            continue
        kind = str(e["predicate"])
        out.append(
            Fact(
                kind=kind,
                outward=a == here,
                detail=str(e.get("effect") or e["detail"] or ""),
                deed=str(e["detail"] or "") if kind == "did" else "",
                session=_when(e.get("at")),
                subject_kind=str(nodes[a]["kind"]),
                route_state=str(e.get("route_state") or ""),  # (a journey or a plan, #4173 6d)
                # (meaningful for the player version only: that graph holds just what players know)
                players_know=True,
                viewer_knows=clicker_knows is None or str(e["id"]) in clicker_knows,
            )
        )
    return tuple(out)


def summary_for(
    src: dict[str, Any],
    kn: dict[str, Any],
    ranks: Mapping[str, int],
    node_id: str,
    *,
    clicker: gate.Viewer,
    peek: bool,
) -> Result | None:
    """The summary of `node_id` for `clicker`, or None when this person's graph has no such node
    (the route answers `not_found`, the same as for a node that does not exist, so nothing hints at
    a hidden one). `ranks`: entry id -> rank from `importance_rank.ranks_from`. `peek`: a player
    chose to read past the spoiler gate (ignored for the DM)."""
    if node_id.startswith(graph_view.ALIAS):
        return None  # (an other name's node is drawn on the graph; it has no summary of its own)
    audience = DM if clicker.dm else PLAYER
    if audience == DM:
        graph = graph_view.build(src, kn, gate.Viewer.the_dm())
        clicker_knows: set[str] | None = None
    else:
        graph = graph_view.build(src, kn, gate.Viewer.player(kn["playerCharacters"]))
        clicker_knows = None
    node = graph.nodes.get(node_id)
    if node is None or (audience == PLAYER and node["kind"] == "deed"):
        return None  # (a deed is the DM's: a player's click on one finds nothing)
    if audience == PLAYER:
        # (a connection split onto an other name in the clicker's graph but plain in the union
        # counts as unknown to them: the gate may close for it, the safe direction)
        clicker_knows = {str(e["id"]) for e in _edges(graph_view.build(src, kn, clicker))}
    edges = deed_facts(_edges(graph), graph.nodes)
    scores = importance.scores((str(e["a"]), str(e["b"])) for e in edges)
    near: dict[str, set[str]] = {}
    for e in edges:
        near.setdefault(str(e["a"]), set()).add(str(e["b"]))
        near.setdefault(str(e["b"]), set()).add(str(e["a"]))

    def entry(entity: str, here: str, further: tuple[Neighbour, ...] = ()) -> Neighbour:
        info = graph.nodes[entity]
        return Neighbour(
            id=entity,
            name=str(info["name"]),
            kind=str(info["kind"]),
            rank=int(ranks.get(entity, 0)),
            score=float(scores.get(entity, 0.0)),
            facts=_facts(edges, graph.nodes, here, entity, clicker_knows),
            players_know=True,
            further=further,
        )

    neighbours: list[Neighbour] = []
    for other in sorted(near.get(node_id, set()) - {node_id}):
        if other not in graph.nodes:
            continue
        # the second hop: the entries joined to this neighbour, the best of which the text names
        farther = sorted(
            (far for far in near.get(other, set()) - {node_id, other} if far in graph.nodes),
            key=lambda far: (-int(ranks.get(far, 0)), -scores.get(far, 0.0), far),
        )
        second = [entry(far, other) for far in farther[:MAX_SECOND_HOP]]
        neighbours.append(entry(other, node_id, tuple(second)))
    focus = Focus(node_id, str(node["name"]), str(node["kind"]))
    built = node_summary.summarize(focus, neighbours, audience)
    gated = audience == PLAYER and built.needs_peek and not peek
    return Result(
        "" if gated else built.text,
        0 if gated else built.more,
        built.needs_peek,
        "dm" if audience == DM else "players",
    )
