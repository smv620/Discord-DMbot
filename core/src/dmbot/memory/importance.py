"""How much a name matters to the story, from how connected it is in The truth (#2411).

A "patron at the inn" mentioned once is important when The truth ties him to many others, hidden
connections included. This only RANKS: it picks which beats and which names a recap or PlotBot
keeps. It never gives the writer a name or a fact, so a hidden connection never reaches a prompt
through it. The writer's names and facts still come only from what the players know.

The score is the number of live connections an entry has, plus a small share for the entries two
steps out, capped, so a hub cannot drown every other beat. It is computed in code from the
campaign's own connections and beliefs. It is cached per campaign in memory and refreshed after
each session; a restart simply computes it again on first use.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from dmbot.memory.models import name_key

log = logging.getLogger(__name__)

TWO_STEP = 0.25  # what an entry two steps out adds, against one for a direct connection
CAP = 20.0  # no single name outweighs a whole scene


def scores(edges: Iterable[tuple[str, str]]) -> dict[str, float]:
    """Each entry's score from its connections (pairs of entry ids). Pure."""
    near: dict[str, set[str]] = {}
    for a, b in edges:
        if a == b:
            continue
        near.setdefault(a, set()).add(b)
        near.setdefault(b, set()).add(a)
    out: dict[str, float] = {}
    for entry, direct in near.items():
        two = set().union(*(near[n] for n in direct)) - direct - {entry}
        out[entry] = min(CAP, len(direct) + TWO_STEP * len(two))
    return out


async def by_name(store: Any, guild_id: int, campaign_id: str) -> dict[str, float]:
    """name key → score, for every name and other name of an entry that has connections. Counts
    every live connection The truth believes (secret ones too), and not one it has ended or put at
    0.0 (a lie). The keys are the squashed forms `name_key` makes, as names are compared."""
    async with store._read(guild_id, campaign_id, snapshot=True) as scope:
        cur = await scope.conn.execute(
            "SELECT r.subject_id, r.object_id FROM memory_relations r"
            " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.status = 'confirmed'"
            " AND r.to_session_at IS NULL"
            " AND COALESCE((SELECT b.confidence FROM memory_beliefs b"
            "   JOIN memory_perspectives p ON p.guild_id = b.guild_id"
            "     AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
            "   WHERE b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id"
            "     AND b.relation_id = r.id AND p.kind = 'truth'"
            "   ORDER BY b.seq DESC LIMIT 1), 1.0) > 0",
            scope.ids,
        )
        pairs = [(str(r["subject_id"]), str(r["object_id"])) for r in await cur.fetchall()]
        if not pairs:
            return {}
        # A name merged into another is the same character: its connections count for the one
        # that stayed, which is the entry the names below belong to.
        cur = await scope.conn.execute(
            "SELECT id, merged_into FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND merged_into IS NOT NULL",
            scope.ids,
        )
        stayed = {str(r["id"]): str(r["merged_into"]) for r in await cur.fetchall()}
        ranked = scores((stayed.get(a, a), stayed.get(b, b)) for a, b in pairs)
        cur = await scope.conn.execute(
            "SELECT id AS entry, name AS text FROM memory_entities WHERE guild_id = %s"
            " AND campaign_id = %s AND status = 'confirmed'"
            " UNION ALL SELECT entity_id AS entry, text FROM memory_aliases WHERE guild_id = %s"
            " AND campaign_id = %s AND status = 'confirmed'",
            (*scope.ids, *scope.ids),
        )
        names = await cur.fetchall()
    out: dict[str, float] = {}
    for row in names:
        score = ranked.get(str(row["entry"]))
        if score is None:
            continue
        key = name_key(str(row["text"]))
        out[key] = max(out.get(key, 0.0), score)
    return out


def beat_score(who: Iterable[str], importance: Mapping[str, float]) -> float:
    """What the names said in a beat add to its score: the sum of their importance, so a scene with
    the story's hub in it outranks one with only minor names. 0.0 with no table."""
    if not importance:
        return 0.0
    return sum(importance.get(name_key(w), 0.0) for w in dict.fromkeys(who))


class ImportanceCache:
    """The scores per campaign, kept in memory and refreshed after each session. Campaigns never
    share an entry: the key is the server and the campaign."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._by_campaign: dict[tuple[int, str], dict[str, float]] = {}

    async def get(self, guild_id: int, campaign_id: str) -> Mapping[str, float]:
        """The scores; computed on first use. Never raises: with no scores the recap simply ranks
        as it did before."""
        key = (guild_id, campaign_id)
        if key not in self._by_campaign:
            await self.refresh(guild_id, campaign_id)
        return self._by_campaign.get(key, {})

    async def refresh(self, guild_id: int, campaign_id: str) -> None:
        try:
            self._by_campaign[(guild_id, campaign_id)] = await by_name(
                self._store, guild_id, campaign_id
            )
        except Exception:
            log.exception("Importance: couldn't read the campaign's connections")
            # Kept as no scores until the next session's refresh, so a failing read is not
            # repeated for every recap call.
            self._by_campaign.setdefault((guild_id, campaign_id), {})
