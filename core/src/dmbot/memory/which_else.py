"""The answer "none of these" to a "Which one?" question (#2790), for Discord's card and the
website's button alike, so they cannot differ.

A question about a name ("Which Mara?") that gets "Neither" is dropped: the DM says the line is
wrong. A question about a pronoun ("she") never loses its connection: "Someone else" keeps it with
a person who is not named yet. That person is made first (the pick needs it to exist); if the pick
then does not land (the queue is full, or someone answered the question meanwhile) or raises, the
entry is undone so none is left behind with no connection. A failed undo is logged as a count.
"""

from __future__ import annotations

import logging

from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.backstory_links import DONE, BackstoryLinks, WhichWaiting
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.pronouns import is_pronoun
from dmbot.memory.store import MemoryStore
from dmbot.memory.who_resolve import SOMEONE

log = logging.getLogger(__name__)

COULD_NOT_KEEP = "could_not_keep"  # a result: the unnamed person could not be made; it waits


def asks_about_a_pronoun(question: WhichWaiting) -> bool:
    return is_pronoun(question.word.strip().removeprefix("the ").strip())


async def none_of_these(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str, q: WhichWaiting
) -> str:
    """Answer the question with none of its names. `DONE`, `GONE`, `FULL`, or `COULD_NOT_KEEP`."""
    if not asks_about_a_pronoun(q):
        return await store.pick_which(guild_id, campaign_id, q.id, None)
    kin_word = q.predicate == "kin_of" and bool(q.detail)
    try:
        added = await memory.add_unnamed(
            guild_id,
            campaign_id,
            what=q.detail if kin_word else SOMEONE,
            anchor_id=q.other_id if kin_word else None,
            role=(q.detail or q.predicate)[: unnamed_mod.MAX_ROLE],
        )
    except MemoryRuleError:
        return COULD_NOT_KEEP
    batch = added.batch if added.created else None  # (an entry that was already there stays)
    result = ""
    try:
        result = await store.pick_which(guild_id, campaign_id, q.id, added.entity.id, outside=True)
        return result
    finally:
        if result != DONE and batch is not None:
            try:
                await memory.undo(guild_id, campaign_id, batch)
            except Exception:  # (the pick's own result or exception is what the caller sees)
                log.warning("Who-is-it: 1 unnamed person could not be taken back")
