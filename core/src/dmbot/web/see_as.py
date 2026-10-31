"""The story graph seen as the party, a player character or an NPC, for the DM (#2999).

The DM's graph is the whole truth. "See as" draws it the way a character knows it, by the same
path every other look at the graph now takes (#3143): the API reads the graph's source and the
knowers, and `web/graph_view.py` keeps what the gate (`memory.gate`) lets that viewer know. Names
are the gate's (`gate.name_entries`), and so is the split of facts learned through a secret other
name onto a node of their own (`gate.split_connections`). This module only says who to look as and
answers the DM; it decides nothing about who may know what. Read-only; DM only.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory import gate
from dmbot.web import graph_view
from dmbot.web.recaps import RecapError
from dmbot.web.relationships import MAX_NODES, is_dm, page_of, refuse_bad, source_of
from dmbot.web.sessions import Session

PARTY = "party"  # the viewer that is every player character at once


def viewer_of(data: dict[str, Any], viewer: str) -> tuple[gate.Viewer, str]:
    """Who the DM asked to see as, and the words for it. A name that is not a character of this
    campaign is "not found" (nothing says whether it exists elsewhere)."""
    if viewer == PARTY:
        return gate.Viewer.the_party(), "The party"
    for v in data["viewers"]:
        if v["id"] == viewer:
            return gate.Viewer.character(viewer), str(v["name"])
    raise RecapError("not_found")


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
    # the DM check comes first, so a player who asks does not make the server read the whole graph
    if not await is_dm(db, session, ref):
        raise RecapError("not_allowed")
    refuse_bad(center, hops)
    src, known, pages = await source_of(db, session, ref)
    if not known["isDm"]:
        raise RecapError("not_allowed")
    who, words = viewer_of(known, viewer)
    graph = graph_view.build(src, known, who)
    # A DM looking as someone may ask for a centre that character is not shown: the picture is
    # then centred on what they do know, never an error (a player asking for such a name is told
    # "not found", as before).
    shaped = graph_view.web_of(graph, center, hops, MAX_NODES) or graph_view.web_of(
        graph, None, hops, MAX_NODES
    )
    assert shaped is not None  # (no centre asked for: always an answer)
    return {**page_of({**shaped, "pageIds": pages}), "seeAs": {"viewer": viewer, "name": words}}


VIEWERS_SHOWN = 25  # the most names offered at once; a search narrows a big cast


async def read_viewers(db: Database, session: Session, ref: str, query: str = "") -> dict[str, Any]:
    """The characters a DM may "see as", for the picker (#2999): `{viewers: [{id, name, kind}],
    total}`, player characters first, then by name, at most `VIEWERS_SHOWN`, narrowed by `query`
    (letters in the name, any case) for a big cast. The party is always offered by the page and
    is not listed here. DM only: anyone else is `not_allowed`, and nothing here says who plays
    whom."""
    if not await is_dm(db, session, ref):
        raise RecapError("not_allowed")
    _src, known, _pages = await source_of(db, session, ref)
    if not known["isDm"]:
        raise RecapError("not_allowed")
    needle = " ".join(query.split()).casefold()[:100]
    found = [v for v in known["viewers"] if needle in str(v["name"]).casefold()]
    return {
        "viewers": [
            {"id": str(v["id"]), "name": str(v["name"]), "kind": str(v["kind"])}
            for v in found[:VIEWERS_SHOWN]
        ],
        "total": len(found),
    }
