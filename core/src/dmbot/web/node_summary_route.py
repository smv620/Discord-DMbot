"""The click on a node of the story graph (#3236, slice 2b): the route's read.

It reads the graph source and the knowers (one statement), and the rank inputs, hands them to
`node_summary_run.summary_for` (pure, GraphEngineer's), and sends back four things only: `text`,
`more`, `needsPeek` and `version` (Supervisor's condition on #3236). The rank inputs and the graph
hold ids and names a player may not see, so none of them is in the answer, in an error, or in a log;
a node this person cannot see is `not_found`, the same as one that does not exist."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import gate
from dmbot.web import node_summary_run, rank_inputs, relationships
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

# An entry's id (an other name's node, `alias:…`, has no summary and is `not_found` too).
NODE_ID = re.compile(r"^[0-9a-f]{32}$")

# The keys of the answer, pinned by a test: nothing else may be added without a new review.
KEYS = ("text", "more", "needsPeek", "version")


async def read_node_summary(
    db: Database, session: Session, ref: str, node_id: str, *, peek: bool
) -> dict[str, Any]:
    """The summary of one node for the caller: the DM's full one, or a player's, built from what
    their characters know (behind the spoiler gate until `peek`)."""
    if not NODE_ID.match(node_id):
        raise RecapError("not_found")
    source, known, _pages = await relationships.source_of(db, session, ref)
    ranks = node_summary_run.ranks_of(await rank_inputs.read_rank_inputs(db, session, ref))
    clicker = (
        gate.Viewer.the_dm() if known["isDm"] else gate.Viewer.player(str(i) for i in known["mine"])
    )
    result = node_summary_run.summary_for(source, known, ranks, node_id, clicker=clicker, peek=peek)
    if result is None:
        raise RecapError("not_found")
    return {
        "text": result.text,
        "more": result.more,
        "needsPeek": result.needs_peek,
        "version": result.version,
    }
