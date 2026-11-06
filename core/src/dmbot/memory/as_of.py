"""What a point of view believed at a game time (#3091, part 3): the "as of" reader.

"What did the party believe on Flamerule 10th?" is answered from each belief's game time
(`memory_beliefs.game_time`, game minutes since Day 1), next to the session-order readers, which
answer by the session a belief was formed in. A belief that has no game time is NEVER "believed at"
a game time: most beliefs have none (they were made after a session, or before the clock log), so
for those ask the session-order reader (`MemoryStore.party_view`, `truth_view`) instead. A game
time is only as good as the clock log that stamped it, and the answer can therefore be partial.

One timeline, as everywhere in the story graph: for each connection the answer is the belief of the
latest game time at or before the asked one, and beliefs at the same minute break by `seq` (the
order they were recorded). A flashback or time travel does not move anything.

The DM may ask for any point of view. A player is answered only from their OWN characters' points
of view, and only for connections the one gate (`gate.visible_to`) lets those characters know, so
a secret connection is never named. This returns ids and numbers, never a word of a line.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory import gate
from dmbot.memory._changes import Scope


@dataclass(frozen=True, slots=True)
class Believed:
    """One point of view's belief in one connection as of a game time."""

    perspective_id: str
    entity_id: str | None  # the character who believes it (None: The truth or The party)
    relation_id: str
    confidence: float
    status: str
    game_time: int  # when this belief was made, in game minutes
    seq: int


_SQL = """
SELECT DISTINCT ON (b.perspective_id, b.relation_id)
       b.perspective_id, p.entity_id, b.relation_id, b.confidence, b.status, b.game_time, b.seq
FROM memory_beliefs b
JOIN memory_perspectives p ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id
                          AND p.id = b.perspective_id
JOIN memory_relations r ON r.guild_id = b.guild_id AND r.campaign_id = b.campaign_id
                       AND r.id = b.relation_id AND r.status = ANY(%(statuses)s)
WHERE b.guild_id = %(g)s AND b.campaign_id = %(c)s
  AND b.perspective_id = ANY(%(views)s)
  AND b.game_time IS NOT NULL AND b.game_time <= %(at)s
ORDER BY b.perspective_id, b.relation_id, b.game_time DESC, b.seq DESC
"""


async def _read(
    scope: Scope, views: list[str], game_time: int, statuses: list[str]
) -> list[Believed]:
    cur = await scope.conn.execute(
        _SQL,
        {
            "g": scope.guild_id,
            "c": scope.campaign_id,
            "views": views,
            "at": game_time,
            "statuses": statuses,
        },
    )
    return [
        Believed(
            str(r["perspective_id"]),
            None if r["entity_id"] is None else str(r["entity_id"]),
            str(r["relation_id"]),
            float(r["confidence"]),
            str(r["status"]),
            int(r["game_time"]),
            int(r["seq"]),
        )
        for r in await cur.fetchall()
    ]


async def believed_at(scope: Scope, perspective_id: str, game_time: int) -> list[Believed]:
    """For the DM: what this point of view (any: The truth, The party, a character's) believed as
    of the game time, on any live connection, proposed or confirmed. Oldest connection first."""
    found = await _read(scope, [perspective_id], game_time, ["proposed", "confirmed"])
    return sorted(found, key=lambda x: (x.relation_id, x.seq))


async def my_characters_believed_at(
    scope: Scope, character_ids: list[str], game_time: int
) -> list[Believed]:
    """For a player: what their own characters believed as of the game time, on confirmed
    connections the gate lets those characters know. Nobody else's point of view, ever."""
    if not character_ids:
        return []
    cur = await scope.conn.execute(
        "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
        " AND entity_id = ANY(%s) AND kind IN ('pc', 'npc')",
        (scope.guild_id, scope.campaign_id, character_ids),
    )
    views = [str(r["id"]) for r in await cur.fetchall()]
    if not views:
        return []
    seen = await gate.visible_to(scope, gate.Viewer.player(character_ids))
    return sorted(
        (
            x
            for x in await _read(scope, views, game_time, ["confirmed"])
            if x.entity_id in set(character_ids) and seen.knows(gate.CONNECTION, x.relation_id)
        ),
        key=lambda x: (x.relation_id, x.perspective_id, x.seq),
    )
