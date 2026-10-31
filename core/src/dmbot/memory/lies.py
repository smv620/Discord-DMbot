"""Who thinks a speaker lied (#3091, part 2): the beliefs `suspected_lie` and `known_lie`, read.

A lie is a belief about the speaker as well as the statement: "Cerric thinks Valyra lied about
this". The DM sees every one; a player sees only what their OWN characters think, and only when
their characters may know both the connection and the one who lied, through the one gate
(`gate.visible_to`), so a lie never names a secret entry or a connection the viewer cannot see.
The words are made by the caller from names the viewer may use; this module returns ids and
numbers, never a word of a line.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory import gate
from dmbot.memory._changes import Scope
from dmbot.memory.models import KNOWN_LIE, SUSPECTED_LIE


@dataclass(frozen=True, slots=True)
class Lie:
    """One current belief that someone lied. `believer`: the character who thinks so (None: The
    truth, which confirms it); `liar`: the character who lied, or None when who it was is no longer
    known (the entry was erased or merged into something that is not a character): the caller says
    "someone, no longer known"."""

    believer: str | None
    liar: str | None
    relation_id: str
    status: str
    confidence: float


_SQL = """
SELECT p.entity_id AS believer, p.kind AS view, b.relation_id, b.status, b.confidence,
       x.id AS liar
FROM memory_beliefs b
JOIN memory_perspectives p ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id
                          AND p.id = b.perspective_id
JOIN memory_relations r ON r.guild_id = b.guild_id AND r.campaign_id = b.campaign_id
                       AND r.id = b.relation_id AND r.status = ANY(%(statuses)s)
                       AND r.to_session_at IS NULL
JOIN memory_evidence e ON e.guild_id = b.guild_id AND e.campaign_id = b.campaign_id
                      AND e.belief_id = b.id AND e.kind = 'source'
LEFT JOIN memory_entities x ON x.guild_id = e.guild_id AND x.campaign_id = e.campaign_id
                           AND x.id = e.target_id AND x.type = 'character'
WHERE b.guild_id = %(g)s AND b.campaign_id = %(c)s AND b.status = ANY(%(lies)s)
  AND b.seq = (SELECT max(n.seq) FROM memory_beliefs n WHERE n.guild_id = b.guild_id
               AND n.campaign_id = b.campaign_id AND n.perspective_id = b.perspective_id
               AND n.relation_id = b.relation_id)
ORDER BY b.relation_id, p.entity_id NULLS FIRST, x.id NULLS FIRST
"""


async def _read(scope: Scope, statuses: list[str]) -> list[Lie]:
    cur = await scope.conn.execute(
        _SQL,
        {
            "g": scope.guild_id,
            "c": scope.campaign_id,
            "statuses": statuses,
            "lies": [SUSPECTED_LIE, KNOWN_LIE],
        },
    )
    return [
        Lie(
            None if r["believer"] is None else str(r["believer"]),
            None if r["liar"] is None else str(r["liar"]),
            str(r["relation_id"]),
            str(r["status"]),
            float(r["confidence"]),
        )
        for r in await cur.fetchall()
    ]


async def all_lies(scope: Scope) -> list[Lie]:
    """Every current lie, for the DM: on any live connection, proposed or confirmed."""
    return await _read(scope, ["proposed", "confirmed"])


async def lies_my_characters_think(scope: Scope, character_ids: list[str]) -> list[Lie]:
    """What the characters of one player think about who lied: only their own points of view, only
    on confirmed connections, and only when the gate lets them know the connection and the liar."""
    if not character_ids:
        return []
    seen = await gate.visible_to(scope, gate.Viewer.player(character_ids))
    mine = set(character_ids)
    return [
        lie
        for lie in await _read(scope, ["confirmed"])
        if lie.believer in mine
        and seen.knows(gate.CONNECTION, lie.relation_id)
        and (lie.liar is None or seen.knows(gate.ENTRY, lie.liar))
    ]
