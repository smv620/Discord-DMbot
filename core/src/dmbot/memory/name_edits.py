"""Who knows a secret other name (#2935): the one call the name card and the website share, as
`link_edits.set_who_knows` is for a connection.

An other name the DM keeps from the table is a secret spelling. "Only some characters" keeps it
secret and lets the picked player characters know it; "Everyone at the table" lets them go and
shows the name (what the old "Stop keeping it secret" did); "Only the DM" keeps it secret with no
one else knowing it (the old "Keep it secret").
"""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.memory.link_edits import EVERYONE, ONLY_THE_DM, SOME_CHARACTERS, WHO_KNOWS, keep_batch
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
    *,
    players_only: bool = False,
    made: list[int] | None = None,
) -> Written[object]:
    """Set who knows this other name. Nothing is written when the pick is refused; showing the
    name lets the characters who knew it go first and puts them back if it is refused. Returns
    the last change that wrote something; `made`, when given, gets every change's batch in order
    (keeping it secret and saying who knows it can be two, #4159)."""
    log = made if made is not None else []
    if who not in WHO_KNOWS:
        raise MemoryRuleError("Pick who knows it: only the DM, some characters, or everyone.")
    if who == SOME_CHARACTERS and not characters:
        raise MemoryRuleError(PICK_ONE)
    if who == EVERYONE:
        if not alias.secret:
            return Written(None, None)  # already everyone's
        before = (await memory.alias_knowers(guild_id, campaign_id, [alias.id])).get(alias.id, [])
        if before:
            keep_batch(
                log,
                await memory.set_alias_knowers(
                    guild_id, campaign_id, alias.id, [], source=DM, players_only=players_only
                ),
            )
        try:
            return keep_batch(
                log,
                await memory.update_alias(guild_id, campaign_id, alias.id, secret=False, source=DM),
            )
        except MemoryRuleError:
            if before:
                await memory.set_alias_knowers(
                    guild_id, campaign_id, alias.id, before, source=DM, players_only=players_only
                )
            raise
    chosen = list(characters) if who == SOME_CHARACTERS else []
    await memory.check_knowers(guild_id, campaign_id, chosen)
    hidden: Written[object] = Written(None, None)
    if not alias.secret:
        hidden = keep_batch(
            log,
            await memory.update_alias(guild_id, campaign_id, alias.id, secret=True, source=DM),
        )
    knew = keep_batch(
        log,
        await memory.set_alias_knowers(
            guild_id, campaign_id, alias.id, chosen, source=DM, players_only=players_only
        ),
    )
    # (keeping it secret with no one to know it is the only change: Undo takes that back, #4159)
    return knew if knew.batch is not None else hidden
