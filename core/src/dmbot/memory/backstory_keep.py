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
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from dmbot.memory import kin, lore_knowledge
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, Waiting
from dmbot.memory.checks import conflicts
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PLAYER_CHARACTER,
    REJECTED,
    MemoryRuleError,
    Relation,
)
from dmbot.memory.ontology import Ontology
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class KeptAll:
    kept: list[tuple[Waiting, int | None]] = field(default_factory=list)  # (link, batch)
    # the batches that ended an old connection for each kept change (same order as `kept`)
    ended: list[list[int | None]] = field(default_factory=list)
    refused: int = 0  # the rules refused it: it stays waiting, to be answered by hand


def replaced_by(onto: Ontology, link: Waiting, existing: Sequence[Relation]) -> list[Relation]:
    """The live connections between the same two entries that `link` cannot hold beside (friend
    or ally against enemy): keeping it is a change, and these are what it ends (#3021). A
    compatible kind ("knows" beside "works for") replaces nothing. Pure, so the card, the summary
    count and both keep paths read it the same way."""
    pred = onto.predicates.get(link.predicate)
    pair = {link.subject_id, link.object_id}
    if pred is None:
        return []
    return [
        r
        for r in existing
        if r.to_session_at is None
        and r.status != REJECTED
        and {r.subject_id, r.object_id} == pair
        and conflicts(onto, pred, r.predicate)
    ]


async def changes_among(
    memory: MemoryStore, guild_id: int, campaign_id: str, links: Sequence[Waiting]
) -> dict[tuple[str, str, str], list[Relation]]:
    """Of these waiting connections, the ones that are changes: (subject, kind, object) -> the
    live connections each would end. Read again every time, never kept, so a connection the DM
    ended or added since is taken into account."""
    if not links:
        return {}
    onto = await memory.ontology(guild_id, campaign_id)
    existing = await memory.relations(guild_id, campaign_id, include_secret=True)
    found = {
        (c.subject_id, c.predicate, c.object_id): replaced_by(onto, c, existing) for c in links
    }
    return {k: v for k, v in found.items() if v}


async def player_character_ids(memory: MemoryStore, guild_id: int, campaign_id: str) -> list[str]:
    """The campaign's confirmed player characters (entry ids), all of them."""
    lookup = CampaignLookup.build(await memory.lookup_data(guild_id, campaign_id))
    return sorted(
        e.id
        for e in lookup.entities.values()
        if e.role == PLAYER_CHARACTER and e.status == CONFIRMED and not e.merged_into
    )


async def shown_entry_ids(memory: MemoryStore, guild_id: int, campaign_id: str) -> set[str]:
    """The entries the players are shown: confirmed, not held back from them, not a limited clue
    and not an unnamed person (the old readers' rule). Read once per batch."""
    data = await memory.lookup_data(guild_id, campaign_id)
    return {
        e.id
        for e in data.entities
        if e.status == CONFIRMED
        and not (e.hidden_from_players or e.audience_limited or e.unnamed or e.merged_into)
    }


def kept_unrevealed(c: Waiting, secret: bool, shown: set[str]) -> bool:
    """Does a connection kept from this waiting row still wait to be said at the table? Yes,
    unless the upload said the players know it (#2959: World general knowledge, or a smart guess
    the AI called common knowledge), it is not secret and both its ends are shown. The old flags
    are what the players see until the readers go through the who-knows lists (#2995 slice 3), so
    this is where an upload's choice reaches them; the list follows the flag by itself."""
    if not c.world or secret or c.secret:
        return True
    return not {c.subject_id, c.object_id} <= shown


async def mark_guess(
    memory: MemoryStore, guild_id: int, campaign_id: str, kind: str, written_ids: Sequence[str]
) -> None:
    """A statement kept because the AI guessed the players know it is recorded at 0.75, not as the
    DM's word. Any failure is logged: the connection is kept either way."""
    if not written_ids:
        return
    try:
        await memory.mark_guess(
            guild_id, campaign_id, kind, written_ids, lore_knowledge.GUESS_CERTAINTY, source=DM
        )
    except Exception:  # a database trouble: the knowing stays at sure, the keep stays made
        log.exception("A guess could not be recorded as a guess")


def change_start(old: Sequence[Relation], now: int) -> int:
    """When a change takes hold: now, or just after the latest start among the connections it
    ends (one that began later than now would end before it began). The old ones end and the new
    one starts at this same moment, so they never overlap."""
    return max([now, *((r.from_session_at or 0) + 1 for r in old)])


async def end_replaced(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    old: Sequence[Relation],
    start: int,
    ended: list[int | None],
) -> None:
    """End these connections as of `start` (never deleted, so the history and the timeline keep
    them). Each one's batch is appended to `ended` as it is done, so a caller whose later step
    fails can take back exactly what was ended (`undo_batches`)."""
    for r in old:
        written = await memory.update_relation(
            guild_id, campaign_id, r.id, source=DM, to_session_at=start
        )
        ended.append(written.batch)


async def undo_batches(
    memory: MemoryStore, guild_id: int, campaign_id: str, batches: Sequence[int | None]
) -> None:
    """Take back these writes, newest first. One that cannot be reversed (changed since) is
    logged and skipped: the rest still go back."""
    for batch in reversed(batches):
        if batch is None:
            continue
        try:
            await memory.undo(guild_id, campaign_id, batch, source=DM)
        except MemoryRuleError:
            log.info("A write of a change could not be taken back")


async def keep_all(
    memory: MemoryStore,
    store: BackstoryLinks,
    guild_id: int,
    campaign_id: str,
    links: Sequence[Waiting],
    progress: Callable[[int, int], Awaitable[None]] | None = None,
    *,
    keep_flags: bool = True,
) -> KeptAll:
    """Keep each of these ready connections. A refused one stays in the list. `progress(done,
    total)` is told before the first and after each one (kept or refused: tried), for the
    website's "12 of 50" (#2924); counts only. `keep_flags`: keeping a connection that already
    exists never makes it secret, so a secret spelling in the text never hides what the players
    were shown (#3025); False only when the DM pressed 🤫 Keep as secret, which is their own word.
    """
    done = KeptAll()
    total = len(links)
    if progress is not None:
        await progress(0, total)
    onto = await memory.ontology(guild_id, campaign_id) if links else None
    shown = await shown_entry_ids(memory, guild_id, campaign_id) if links else set()
    for tried, c in enumerate(links, 1):
        now = int(time.time())
        old = (
            replaced_by(onto, c, await memory.relations(guild_id, campaign_id, include_secret=True))
            if onto is not None
            else []
        )
        ended: list[int | None] = []
        start = change_start(old, now)
        try:
            await end_replaced(memory, guild_id, campaign_id, old, start, ended)
            written = await memory.add_relation(
                guild_id, campaign_id, c.subject_id, c.predicate, c.object_id,
                source=DM, confidence=1.0, status=CONFIRMED, secret=c.secret, detail=c.detail,
                directional=kin.directional(c.detail),  # a family role keeps who holds it
                unrevealed=kept_unrevealed(c, c.secret, shown), decided=c.decided or None,
                # the uploader's character knows it, unless it uses a secret name (#2935)
                known_by=[c.known_by] if c.known_by and not c.secret else (),
                from_session_at=start if old else None,  # (starts as the old one ends)
                keep_flags=keep_flags,
            )  # fmt: skip
        except Exception as exc:
            # the change is whole or not at all, whatever went wrong
            await undo_batches(memory, guild_id, campaign_id, ended)
            if not isinstance(exc, MemoryRuleError):
                raise
            done.refused += 1
            if progress is not None:
                await progress(tried, total)
            continue
        made = written.value[0]
        # (only a connection made just now: an older twin's rows are the DM's, not this guess's)
        if c.world == lore_knowledge.GUESS and not made.unrevealed and made.created_at >= now:
            await mark_guess(memory, guild_id, campaign_id, "connection", [made.id])
        if c.truth:
            try:
                await memory.pin(guild_id, campaign_id, written.value[0].id, True, source=DM)
            except MemoryRuleError:  # kept all the same: pinning is one tap on its card
                log.info("A kept connection could not be pinned")
        await store.remove(guild_id, campaign_id, c)
        done.kept.append((c, written.batch))
        done.ended.append(ended)
        if progress is not None:
            await progress(tried, total)
    return done


async def undo_all(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str, done: KeptAll
) -> int:
    """Take back what `keep_all` kept: each is reversed and waits again. Returns how many went
    back; one that cannot be reversed (changed since) is left as it is."""
    back = 0
    for (c, batch), ended in reversed(list(zip(done.kept, done.ended, strict=True))):
        if batch is None and not ended:
            continue
        # (the old connections come back whatever happens to the new one, and the link waits
        # again once the new one is gone: nothing is lost half way)
        if batch is not None:
            try:
                await memory.undo(guild_id, campaign_id, batch, source=DM)
            except MemoryRuleError:
                continue
        await undo_batches(memory, guild_id, campaign_id, ended)
        await store.add(
            guild_id,
            campaign_id,
            [
                NewLink(
                    c.subject_id,
                    c.predicate,
                    c.object_id,
                    c.quote,
                    c.secret,
                    c.detail,
                    c.truth,
                    known_by=c.known_by,
                    world=c.world,
                )
            ],
        )
        back += 1
    return back
