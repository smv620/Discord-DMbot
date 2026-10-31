"""Which beliefs rest on something: the DM's finder for "what changes if Morn lied" (#3091).

A belief rests on an entry when its evidence has a `source` link to that entry, or when it comes,
through `derived_from`, from a belief that does (a revision, a retraction, a branch, or a belief
passed on from another point of view). Only the DM asks, nothing is changed here, and the answer
is ids and numbers: never a word of a line or of a source's speech, and never another campaign's
row (every statement is scoped to one campaign). The DM chooses what to change afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory._changes import Scope

DEPTH_MAX = 50  # a chain this long is a loop or a mistake; the walk stops


@dataclass(frozen=True, slots=True)
class Resting:
    """One current belief that rests on the thing asked about. `depth` 0 is a belief that cites
    the source itself; each step after is one `derived_from` link away from it."""

    belief_id: str
    perspective_id: str
    relation_id: str
    confidence: float
    status: str
    depth: int


_WALK = """
WITH RECURSIVE tree AS (
    {start}
  UNION
    SELECT b.id, t.depth + 1 FROM memory_beliefs b
    JOIN tree t ON b.derived_from = t.id
    WHERE b.guild_id = %(g)s AND b.campaign_id = %(c)s AND t.depth < %(max)s
), nearest AS (SELECT id, min(depth) AS depth FROM tree GROUP BY id)
SELECT b.id, b.perspective_id, b.relation_id, b.confidence, b.status, n.depth
FROM nearest n JOIN memory_beliefs b
  ON b.guild_id = %(g)s AND b.campaign_id = %(c)s AND b.id = n.id
WHERE b.seq = (SELECT max(c.seq) FROM memory_beliefs c WHERE c.guild_id = b.guild_id
               AND c.campaign_id = b.campaign_id AND c.perspective_id = b.perspective_id
               AND c.relation_id = b.relation_id)
  AND {keep}
ORDER BY n.depth, b.relation_id, b.perspective_id
"""

_FROM_SOURCE = (
    "SELECT b.id, 0 AS depth FROM memory_beliefs b JOIN memory_evidence e"
    " ON e.guild_id = b.guild_id AND e.campaign_id = b.campaign_id AND e.belief_id = b.id"
    " WHERE b.guild_id = %(g)s AND b.campaign_id = %(c)s AND e.kind = 'source'"
    " AND e.target_id = %(t)s"
)
_FROM_BELIEF = (
    "SELECT b.id, 0 AS depth FROM memory_beliefs b"
    " WHERE b.guild_id = %(g)s AND b.campaign_id = %(c)s AND b.id = %(t)s"
)


async def resting_on(
    scope: Scope, *, source_id: str | None = None, belief_id: str | None = None
) -> list[Resting]:
    """The current beliefs resting on this entry (`source_id`), or on this belief (`belief_id`,
    which itself is not listed: only what came from it). Nearest first, then by connection."""
    if (source_id is None) == (belief_id is None):
        raise ValueError("Ask about one entry or one belief.")
    start, keep = (_FROM_SOURCE, "TRUE") if source_id is not None else (_FROM_BELIEF, "n.depth > 0")
    cur = await scope.conn.execute(
        _WALK.format(start=start, keep=keep),
        {
            "g": scope.guild_id,
            "c": scope.campaign_id,
            "t": source_id if source_id is not None else belief_id,
            "max": DEPTH_MAX,
        },
    )
    return [
        Resting(
            str(r["id"]),
            str(r["perspective_id"]),
            str(r["relation_id"]),
            float(r["confidence"]),
            str(r["status"]),
            int(r["depth"]),
        )
        for r in await cur.fetchall()
    ]
