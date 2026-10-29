"""Who knows a secret other name (#2935): the one call the name card and the website share, as
`link_edits.set_who_knows` is for a connection.

An other name the DM keeps from the table is a secret spelling. "Only some characters" keeps it
secret and lets the picked player characters know it; "Everyone at the table" lets them go and
shows the name (what the old "Stop keeping it secret" did); "Only the DM" keeps it secret with no
one else knowing it (the old "Keep it secret").
"""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.memory.link_edits import EVERYONE, ONLY_THE_DM, SOME_CHARACTERS, WHO_KNOWS
from dmbot.memory.models import DM, Alias, MemoryRuleError, Written
from dmbot.memory.store import MemoryStore

PICK_ONE = "Pick at least one character."


def who_knows_now(alias: Alias, knowers: Sequence[str]) -> str:
    """Which of the three answers an other name is at now."""
    if not alias.secret:
        return EVERYONE
    return SOME_CHARACTERS if knowers else ONLY_THE_DM


async def set_who_knows(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    alias: Alias,
    who: str,
    characters: Sequence[str] = (),
) -> Written[object]:
    """Set who knows this other name. Nothing is written when the pick is refused; showing the
    name lets the characters who knew it go first and puts them back if it is refused."""
    if who not in WHO_KNOWS:
        raise MemoryRuleError("Pick who knows it: only the DM, some characters, or everyone.")
    if who == SOME_CHARACTERS and not characters:
        raise MemoryRuleError(PICK_ONE)
    if who == EVERYONE:
        if not alias.secret:
            return Written(None, None)  # already everyone's
        before = (await memory.alias_knowers(guild_id, campaign_id, [alias.id])).get(alias.id, [])
        if before:
            await memory.set_alias_knowers(guild_id, campaign_id, alias.id, [], source=DM)
        try:
            return await memory.update_alias(
                guild_id, campaign_id, alias.id, secret=False, source=DM
            )
        except MemoryRuleError:
            if before:
                await memory.set_alias_knowers(guild_id, campaign_id, alias.id, before, source=DM)
            raise
    chosen = list(characters) if who == SOME_CHARACTERS else []
    await memory.check_knowers(guild_id, campaign_id, chosen)
    if not alias.secret:
        await memory.update_alias(guild_id, campaign_id, alias.id, secret=True, source=DM)
    return await memory.set_alias_knowers(guild_id, campaign_id, alias.id, chosen, source=DM)
