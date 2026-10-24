"""Changing one connection that is already there: its words, who sees it, taking it back (#2323).

The name card and the website's relationship web edit connections the same way, so both call
these. Each is logged in the campaign's change log, so the history sees the same thing whichever
side the DM used. A removal is one change and Undo can put it back; new words are one change
too (the new connection and the old one ended together), so one Undo reverses either. The DM's words
are never logged here.
"""

from __future__ import annotations

from dmbot.memory import kin
from dmbot.memory.models import CONFIRMED, DM, REJECTED, Relation, Written
from dmbot.memory.store import MemoryStore


async def set_secret(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation, secret: bool
) -> Written[Relation]:
    """Hide the connection from players, or show it to them. Showing one the story was holding
    back (a backstory connection, #2404) also reveals it, in the same change: the DM's one
    "players can see it" switch, refused until both its names are shown."""
    reveal = False if link.unrevealed and not secret else None
    return await memory.update_relation(
        guild_id, campaign_id, link.id, source=DM, secret=secret, unrevealed=reveal
    )


async def remove(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation
) -> Written[Relation]:
    """Take the connection back. History is kept, and Undo can put it back (#1566)."""
    return await memory.update_relation(guild_id, campaign_id, link.id, source=DM, status=REJECTED)


async def confirm(
    memory: MemoryStore, guild_id: int, campaign_id: str, link: Relation
) -> Written[Relation]:
    """The DM says a connection PlotBot proposed is right (#2635): it stands from now on."""
    return await memory.update_relation(guild_id, campaign_id, link.id, source=DM, status=CONFIRMED)


async def change_words(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    predicate: str,
    detail: str,
    first: str | None = None,
) -> Written[Relation] | None:
    """New words between the same two names, keeping who sees it, in ONE change (so one Undo
    puts the old words back). None when the words are the ones it already has, capitals aside:
    nothing is written. The store does the work, for the website's edits too.

    A family role the DM types ("is the niece of") gives the connection a direction (#2457), even
    over an older connection that already says "niece" (its direction was arbitrary): `first` is
    the name the words are said from, who holds the role, and the connection is written in that
    order."""
    detail = detail[:100]
    directional = predicate == "kin_of" and kin.directional(detail)
    # Only a one-way role ("niece") has a side to be on; "cousin" reads the same both ways, so
    # retyping it must write nothing.
    swap = directional and first is not None and first == link.object_id
    if (predicate, detail.casefold()) == (link.predicate, link.detail.casefold()) and not (
        directional and (not link.directional or swap)
    ):
        return None
    written = await memory.change_relation_words(
        guild_id, campaign_id, link.id, predicate, detail, source=DM,
        directional=directional, swap=swap,
    )  # fmt: skip
    unchanged = written.value.id == link.id and written.value.directional == link.directional
    return None if unchanged and not swap else written
