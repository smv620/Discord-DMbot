"""Keeping every waiting connection of a backstory at once, and taking them back (#2642).

The DM wrote or uploaded the text, so alongside the one-by-one cards ("Keep this?") they may keep
all the connections that are ready in one press. Each is kept exactly as a single press keeps it:
the DM's word, confirmed, unrevealed (a backstory connection waits to be said at the table, #2404),
and secret when the text wrote it with a secret spelling or an end is audience-limited; pinned as
100% truth when the upload was said to be. Secret ones are never made public here. Every kept
connection is its own change in the campaign's log, and `undo_all` reverses them and puts them back
in the waiting list, so Undo is whole. The same functions serve the website's door.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.memory import kin
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, Waiting
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class KeptAll:
    kept: list[tuple[Waiting, int | None]] = field(default_factory=list)  # (link, batch)
    refused: int = 0  # the rules refused it: it stays waiting, to be answered by hand


async def keep_all(
    memory: MemoryStore,
    store: BackstoryLinks,
    guild_id: int,
    campaign_id: str,
    links: Sequence[Waiting],
) -> KeptAll:
    """Keep each of these ready connections. A refused one stays in the list."""
    done = KeptAll()
    for c in links:
        try:
            written = await memory.add_relation(
                guild_id, campaign_id, c.subject_id, c.predicate, c.object_id,
                source=DM, confidence=1.0, status=CONFIRMED, secret=c.secret, detail=c.detail,
                directional=kin.directional(c.detail),  # a family role keeps who holds it
                unrevealed=True,
            )  # fmt: skip
        except MemoryRuleError:
            done.refused += 1
            continue
        if c.truth:
            try:
                await memory.pin(guild_id, campaign_id, written.value[0].id, True, source=DM)
            except MemoryRuleError:  # kept all the same: pinning is one tap on its card
                log.info("A kept connection could not be pinned")
        await store.remove(guild_id, campaign_id, c)
        done.kept.append((c, written.batch))
    return done


async def undo_all(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str, done: KeptAll
) -> int:
    """Take back what `keep_all` kept: each is reversed and waits again. Returns how many went
    back; one that cannot be reversed (changed since) is left as it is."""
    back = 0
    for c, batch in reversed(done.kept):
        if batch is None:
            continue
        try:
            await memory.undo(guild_id, campaign_id, batch, source=DM)
        except MemoryRuleError:
            continue
        await store.add(
            guild_id,
            campaign_id,
            [NewLink(c.subject_id, c.predicate, c.object_id, c.quote, c.secret, c.detail, c.truth)],
        )
        back += 1
    return back
