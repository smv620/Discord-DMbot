"""What a keep would replace, kept on each waiting connection for the website (#4441).

Discord's connection card reads the ontology directly (`backstory_keep.changes_among`), so it knows
that keeping an enemy ends an alliance. The website reads the waiting list through a database door
that cannot run the ontology, so the bot writes the answer onto each waiting row, every minute while
anything waits, and the door returns it as `now`. One rule for both: this module calls the very same
`changes_among` the card does, so the two can never differ except for the minute between looks. The
press itself reads everything again (`keep_all` finds what to end at that moment), so a stale line
can never end the wrong thing, only describe it a minute late. Words and flags only, never a name or
a quote; counts only in the log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dmbot.memory import backstory_keep as keep

if TYPE_CHECKING:
    from dmbot.memory.backstory_links import BackstoryLinks
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


async def refresh(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str
) -> int:
    """Write, for each waiting connection of this campaign, the held connections between its two
    names that a keep would end (a hand-over's ties are the other holder's, told on their own
    line). Returns how many rows changed."""
    onto = await memory.ontology(guild_id, campaign_id)
    symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
    waiting = [w for w in await store.waiting(guild_id, campaign_id, symmetric=symmetric) if w.id]
    changes = await keep.changes_among(memory, guild_id, campaign_id, waiting)
    wanted: dict[str, list[dict[str, object]]] = {}
    for w in waiting:
        pair = {w.subject_id, w.object_id}
        old = [
            r
            for r in changes.get((w.subject_id, w.predicate, w.object_id), [])
            if {r.subject_id, r.object_id} == pair
        ]
        wanted[w.id] = [
            {
                "predicate": r.predicate,
                "detail": r.detail,
                "hidden": bool(r.secret or r.unrevealed or r.assumed),
            }
            for r in old
        ]
    return await store.set_replaces(guild_id, campaign_id, wanted)


async def refresh_guild(memory: MemoryStore, store: BackstoryLinks, guild_id: int) -> int:
    """Every campaign of this server that has something waiting; one that fails is skipped (the
    others still go) and only its error class is logged."""
    changed = 0
    for campaign_id in await store.campaigns_waiting(guild_id):
        try:
            changed += await refresh(memory, store, guild_id, campaign_id)
        except Exception as exc:
            log.warning("What a keep would replace could not be read (%s)", type(exc).__name__)
    return changed
