"""Keeps a campaign's borrowed relationship definitions in step with the set it follows (#1456).

One idempotent move: `reconcile` makes the definitions borrowed from shared sets match the
campaign's current follow, or none when it follows nothing. It is called after anything that
changes a follow (Follow, Unfollow and its Undo, an update taken or undone) and at every session's
start as a safety net, so a step that failed halfway, or a backup restored without the borrowed
definitions, is made right the next time. Cherry pick, which ends the follow and keeps some
definitions as the campaign's own, uses `keep_as_own`.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.memory import set_definitions as sd
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.rules import rule_sets, set_diff

log = logging.getLogger(__name__)


async def reconcile(
    memory: MemoryStore,
    sets: rule_sets.RuleSetStore,
    guild_id: int,
    campaign_id: str,
    *,
    source: str = "dm",
) -> sd.Synced:
    """Make the borrowed definitions match the followed house-rules set (or nothing). Never
    raises for a rule of the memory: what can't be added is reported in the result."""
    follows = [
        f for f in await sets.following(guild_id, campaign_id) if f.kind == rule_sets.HOUSE_RULES
    ]
    if follows:
        f = follows[0]
        wanted = list(set_diff.definitions_of(f.content).values())
        # Borrowed from a set the campaign no longer follows (it switched): those go first.
        for old in await sd.borrowed_set_ids(memory, guild_id, campaign_id):
            if old != f.set_id:
                await sd.release(memory, guild_id, campaign_id, old, source=source)
        return await sd.sync(memory, guild_id, campaign_id, f.set_id, f.name, wanted, source=source)
    gone = sd.Synced()
    for old in await sd.borrowed_set_ids(memory, guild_id, campaign_id):
        done = await sd.release(memory, guild_id, campaign_id, old, source=source)
        gone = sd.Synced(
            removed=gone.removed + done.removed,
            deprecated=gone.deprecated + done.deprecated,
            batch=done.batch,
        )
    return gone


async def keep_as_own(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    taken: rule_sets.Taken,
    *,
    source: str = "dm",
) -> sd.Synced:
    """After Cherry pick ended the follow: the definitions the DM kept become the campaign's
    own, the rest of the set's go (unused removed, used deprecated)."""
    return await sd.sync(
        memory,
        guild_id,
        campaign_id,
        taken.before.set_id,
        taken.before.name,
        list(taken.kept_definitions),
        source=source,
        then_own=True,
    )


async def safely(call: Any) -> sd.Synced | None:
    """Run one of the moves above for a button press: a rule of the memory or a database hiccup
    must not undo the DM's follow or take, which already happened. The next session start
    reconciles again. Returns None when it failed."""
    try:
        done: sd.Synced | None = await call  # `call` is Any: type it here, not at each caller
        return done
    except MemoryRuleError as exc:
        log.warning("Shared set definitions: the memory refused (%s)", exc)
    except Exception:
        log.exception("Shared set definitions: couldn't update the memory")
    return None
