"""DMbot answers "Who is this?" itself, only when it is sure (#3602, slice 4). A pure rule picks
the one case it may apply; the store's `join_by_ai` is the only door and re-checks every limit the
owner set at the moment of use.

- **Only "sure", only an existing entry.** "Likely" and "maybe" are asked, never applied; so is
  "new <kind>" (the DM gives a kind). Only the first, best-ranked answer counts.
- **Never a secret, hidden, limited, disguised or player entry, a split, a merge of two confirmed
  entries or a removal:** the store refuses them (`AI_JOIN_REFUSED`); the call here only reports
  that it was refused, as a count.
- **Waiting for the DM.** The joined spelling stays unconfirmed (hidden from players, the recap
  and table answers) until the DM confirms it, or Undo takes the change back.
- **Counts only** leave this module.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from dmbot.memory.models import MemoryRuleError
from dmbot.memory.who_answers import ENTITY, StoredAnswer

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

SURE = "sure"  # the only confidence word that may apply; the same word `who_reading` stores
JOINED, REFUSED, SKIPPED, FAILED = "joined", "refused", "skipped", "failed"

# Called with the waiting name's id and its ranked answers; returns one of the words above.
Apply = Callable[[str, list[StoredAnswer]], Awaitable[str]]


def may_apply(answers: list[StoredAnswer]) -> StoredAnswer | None:
    """The answer DMbot may apply by itself, or None (then the DM is asked as before): the best
    one, and only if it names an existing entry and DMbot is sure of it."""
    if not answers:
        return None
    best = answers[0]
    if best.pick != ENTITY or best.confidence != SURE or not best.target_id:
        return None
    return best


def joiner(memory: MemoryStore, guild_id: int, campaign_id: str) -> Apply:
    """The `Apply` for one campaign: `join_by_ai`, with a refusal or a failure turned into a word
    (never raised: the names after it still get their answers)."""

    async def apply(entity_id: str, answers: list[StoredAnswer]) -> str:
        best = may_apply(answers)
        if best is None or best.target_id is None:
            return SKIPPED
        try:
            await memory.join_by_ai(guild_id, campaign_id, best.target_id, entity_id)
        except MemoryRuleError:
            return REFUSED  # a rule, or the name changed since it was ranked
        except Exception as exc:  # only the type: nothing here holds a transcript word
            log.warning("Who is this: applying failed (%s)", type(exc).__name__)
            return FAILED
        return JOINED

    return apply
