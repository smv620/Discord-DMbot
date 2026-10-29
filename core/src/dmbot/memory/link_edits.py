"""Changing one connection that is already there: its words, who sees it, taking it back (#2323).

The name card and the website's relationship web edit connections the same way, so both call
these. Each is logged in the campaign's change log, so the history sees the same thing whichever
side the DM used. A removal is one change and Undo can put it back; new words are one change
too (the new connection and the old one ended together), so one Undo reverses either. The DM's words
are never logged here.
"""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.memory import kin
from dmbot.memory.models import DM, REJECTED, MemoryRuleError, Relation, Written
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


ONLY_THE_DM, SOME_CHARACTERS, EVERYONE = "dm", "some", "everyone"
WHO_KNOWS = (ONLY_THE_DM, SOME_CHARACTERS, EVERYONE)
PICK_ONE = "Pick at least one character."


def who_knows_now(link: Relation, knowers: Sequence[str]) -> str:
    """Which of the three answers a connection is at now: shown to everyone, known by these
    characters, or only the DM's (#2935)."""
    if not (link.secret or link.unrevealed):
        return EVERYONE
    return SOME_CHARACTERS if knowers else ONLY_THE_DM


async def set_who_knows(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    link: Relation,
    who: str,
    characters: Sequence[str] = (),
) -> Written[object]:
    """ "Who knows this?" for one connection (#2935), the one call the name card and the website
    share. Only the DM (hidden from the party, no knowers), only these player characters (hidden
    from the party, each of those characters' own point of view holds it), or everyone at the table
    (their knowers let go, then shown to the players: the same as the old "show to players").
    The three keep the meaning of the old 🤫 and 👁️ switches. Returns the last change, which Undo
    reverses."""
    if who not in WHO_KNOWS:
        raise MemoryRuleError("Pick who knows it: only you, some characters, or everyone.")
    if who == SOME_CHARACTERS and not characters:
        raise MemoryRuleError(PICK_ONE)
    if who == EVERYONE:
        if not (link.secret or link.unrevealed):
            return Written(None, None)  # already everyone's: nothing to write
        before = (await memory.connection_knowers(guild_id, campaign_id, [link.id])).get(
            link.id, []
        )
        if before:
            await memory.set_connection_knowers(guild_id, campaign_id, link.id, [], source=DM)
        try:
            return await set_secret(memory, guild_id, campaign_id, link, False)
        except MemoryRuleError:  # (refused until its names are shown): put who knew it back
            if before:
                await memory.set_connection_knowers(
                    guild_id, campaign_id, link.id, before, source=DM
                )
            raise
    chosen = list(characters) if who == SOME_CHARACTERS else []
    await memory.check_knowers(guild_id, campaign_id, chosen)  # nothing is hidden on a bad pick
    if not (link.secret or link.unrevealed):
        await set_secret(memory, guild_id, campaign_id, link, True)
    return await memory.set_connection_knowers(guild_id, campaign_id, link.id, chosen, source=DM)


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
