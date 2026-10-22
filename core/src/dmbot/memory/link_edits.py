"""Changing one connection that is already there: its words, who sees it, taking it back (#2323).

The name card and the website's relationship web edit connections the same way, so both call
these. Each one is a single change in the campaign's change log, so Undo and the history see the
same thing whichever side the DM used. The DM's words are never logged here.
"""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED, DM, REJECTED, Relation, Written
from dmbot.memory.store import MemoryStore


async def set_secret(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation, secret: bool
) -> Written[Relation]:
    """Hide the connection from players, or show it to them."""
    return await memory.update_relation(guild_id, campaign_id, link.id, source=DM, secret=secret)


async def remove(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation
) -> Written[Relation]:
    """Take the connection back. History is kept, and Undo can put it back (#1566)."""
    return await memory.update_relation(guild_id, campaign_id, link.id, source=DM, status=REJECTED)


async def change_words(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    predicate: str,
    detail: str,
) -> bool:
    """New words between the same two names, keeping who sees it. The new connection is added
    first and the old one ended after, so a refusal (a rule, a name that went) leaves the old
    connection as it was. False when the words are the ones it already has."""
    if (predicate, detail) == (link.predicate, link.detail):
        return False
    await memory.add_relation(
        guild_id, campaign_id, link.subject_id, predicate, link.object_id, source=DM,
        confidence=1.0, status=CONFIRMED, detail=detail[:100], secret=link.secret,
    )  # fmt: skip
    await memory.update_relation(guild_id, campaign_id, link.id, source=DM, status=REJECTED)
    return True
