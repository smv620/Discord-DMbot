"""Changing one connection that is already there: its words, who sees it, taking it back (#2323).

The name card and the website's relationship web edit connections the same way, so both call
these. Each is logged in the campaign's change log, so the history sees the same thing whichever
side the DM used. A removal is one change and Undo can put it back; new words are one change
too (the new connection and the old one ended together), so one Undo reverses either. The DM's words
are never logged here.
"""

from __future__ import annotations

from dmbot.memory.models import DM, REJECTED, Relation, Written
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
) -> Written[Relation] | None:
    """New words between the same two names, keeping who sees it, in ONE change (so one Undo
    puts the old words back). None when the words are the ones it already has, capitals aside:
    nothing is written. The store does the work, for the website's edits too."""
    if (predicate, detail.casefold()) == (link.predicate, link.detail.casefold()):
        return None
    written = await memory.change_relation_words(
        guild_id, campaign_id, link.id, predicate, detail[:100], source=DM
    )
    return None if written.value.id == link.id else written
