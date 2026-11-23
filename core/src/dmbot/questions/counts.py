"""Counting what waits for the DM, one number for each kind (#3435).

The one definition of "how many questions wait": the Discord list and the website's page both
read what this writes (`question_counts`), so they cannot disagree. Each kind counts what that
kind's own list shows (a test for every kind keeps them equal). Read only, apart from the
expiry of old "Did they mean…?" questions that their own list already does. A kind that cannot be
counted right now is left out of the result, so its old number stays; the log gets the error type
only. Counts, never a name or a word of a question.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import DM, PROPOSED
from dmbot.questions import kinds

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Counted:
    """One kind's count: how many wait, when the oldest was asked (None if unknown), and for a
    self-expiring kind how many answers were given so far."""

    n: int
    oldest: int | None = None
    settled: int | None = None


@dataclass(slots=True)
class Sources:
    """Where the counts come from; a store that is not there (a bot without that feature) leaves
    its kinds at zero."""

    db: Database
    memory: Any | None = None  # MemoryStore
    claims: Any | None = None  # ClaimStore, only when the claim reader is on
    maps: Any | None = None  # MapStore, with the kept pictures
    map_roads: bool = False  # World notes' road lengths a map disagrees with (DMBOT_MAP_MEASURE)
    backstory: Any | None = None  # BackstoryLinks
    heard: Any | None = None  # WaitingQuestionStore
    rule_sets: Any | None = None  # RuleSetStore
    packs: Any | None = None  # PackUpdateStore
    agreed: Callable[[int], Awaitable[Collection[int]]] | None = None  # who agreed to be recorded
    # who counts as recorded in one campaign's stored sessions (`ConsentStore.recorded_in`)
    recorded: Callable[[int, str], Awaitable[Collection[int]]] | None = None
    # the story-deeds switch and the plan's story memory, for one campaign (#5105): a lapsed plan
    # is offered no deeds, as the deed card and the after-session question already say
    deeds_on: Callable[[int, str], Awaitable[bool]] | None = None


async def count_kinds(
    src: Sources, guild_id: int, campaign_id: str, *, only: Collection[str] | None = None
) -> dict[str, Counted]:
    """The count of each kind (or just `only`); a kind that failed is missing."""
    out: dict[str, Counted] = {}
    for kind in kinds.ORDER:
        if only is not None and kind not in only:
            continue
        counter = _COUNTERS[kind]
        try:
            out[kind] = await counter(src, guild_id, campaign_id)
        except Exception as exc:
            log.warning("Questions: couldn't count %s (%s)", kind, type(exc).__name__)
    return out


async def _names(src: Sources, g: int, c: str) -> Counted:
    if src.memory is None:
        return Counted(0)
    return Counted(len(await src.memory.entities(g, c, statuses=[PROPOSED])))


async def _heard(src: Sources, g: int, c: str) -> Counted:
    if src.heard is None:
        return Counted(0, settled=0)
    return Counted(await src.heard.count(g, c), settled=await src.heard.settled_count(g, c))


async def _lookalikes(src: Sources, g: int, c: str) -> Counted:
    if src.memory is None:
        return Counted(0)
    return Counted(len(await src.memory.look_alikes(g, c)))


async def _connections(src: Sources, g: int, c: str) -> Counted:
    """Backstory connections (not "Which one?", its own kind), the names an AI read suggested for
    confirmed entries, unnamed people who may be a named one, the family links worked out, and
    the one-of-a-kind items two holders have (#4115)."""
    total = 0
    if src.backstory is not None:
        # (two reads: a "Which one?" added between them must not make this negative)
        total += max(
            0, int(await src.backstory.count(g, c)) - int(await src.backstory.count_which(g, c))
        )
    if src.memory is not None:
        total += len(await src.memory.suggested_names(g, c))
        total += len(await src.memory.same_person_offers(g, c))
        total += len(await src.memory.worked_out_waiting(g, c))
        total += len(await src.memory.hand_over_waiting(g, c, source=DM))  # (#4115)
    return Counted(total)


async def _which(src: Sources, g: int, c: str) -> Counted:
    if src.backstory is None:
        return Counted(0)
    return Counted(int(await src.backstory.count_which(g, c)))


async def _family(src: Sources, g: int, c: str) -> Counted:
    if src.claims is None:
        return Counted(0)
    # the card's own list (`ClaimStore.unplaced`): the claims of speakers no longer recorded are in
    # neither, so the message never says 1 over an empty card (#3463)
    opted_in = await src.agreed(g) if src.agreed is not None else None
    return Counted(int(await src.claims.unplaced_count(g, c, opted_in=opted_in)))


async def _claims(src: Sources, g: int, c: str) -> Counted:
    """Facts to check: the number the claims list shows (`listed_count`, the list's own split),
    without the claims kept for a "Which one?" answer, which are their own kind (`family`) and
    which that list also shows."""
    if src.claims is None or src.agreed is None:
        return Counted(0)
    opted_in = await src.agreed(g)
    listed = int(await src.claims.listed_count(g, c, opted_in=opted_in))
    return Counted(max(0, listed - int(await src.claims.unplaced_count(g, c, opted_in=opted_in))))


async def _readings(src: Sources, g: int, c: str) -> Counted:
    if src.claims is None or src.agreed is None:
        return Counted(0)
    return Counted(int(await src.claims.readings_waiting(g, c, opted_in=await src.agreed(g))))


async def _plot(src: Sources, g: int, c: str) -> Counted:
    """Open plot-check issues on the current text of each recap the DM has not yet posted or
    skipped, once its check is done (what the recap page blocks approval on)."""
    async with src.db.guild(g) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM plot_issues i"
            " JOIN recap_jobs j ON j.guild_id = i.guild_id AND j.campaign_id = i.campaign_id"
            "  AND j.session_at = i.session_at"
            " JOIN plot_checks k ON k.guild_id = i.guild_id AND k.campaign_id = i.campaign_id"
            "  AND k.session_at = i.session_at AND k.text_hash = i.text_hash AND k.state = 'done'"
            " WHERE i.guild_id = %s AND i.campaign_id = %s AND i.answer_kind IS NULL"
            " AND j.state = 'ready' AND j.status = 'draft'"
            " AND i.text_hash = encode(sha256(convert_to(j.title || E'\\n' || j.summary,"
            " 'UTF8')), 'hex')",
            (g, c),
        )
        row = await cur.fetchone()
    return Counted(int(row["n"]) if row else 0)


async def _recap_approve(src: Sources, g: int, c: str) -> Counted:
    """Finished recaps waiting for the DM to approve, post or skip (`waiting_drafts`' own rows),
    whose current text has not been approved."""
    async with src.db.guild(g) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n, min(session_at) AS oldest FROM recap_jobs"
            " WHERE guild_id = %s AND campaign_id = %s AND state = 'ready' AND status = 'draft'"
            " AND approved_hash IS DISTINCT FROM"
            " encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')",
            (g, c),
        )
        row = await cur.fetchone()
    n = int(row["n"]) if row else 0
    return Counted(n, int(row["oldest"]) if n and row and row["oldest"] is not None else None)


async def _rules(src: Sources, g: int, c: str) -> Counted:
    """A newer version of the house-rules set the campaign follows, and of each followed pack."""
    n = 0
    if src.rule_sets is not None and await src.rule_sets.pending_update(g, c, for_dmbot=True):
        n += 1
    if src.packs is not None:
        n += len(await src.packs.pending_pack_updates(g, c, for_dmbot=True))
    return Counted(n)


async def _traits(src: Sources, g: int, c: str) -> Counted:
    """A lineage or class the lore gives a character the campaign has (#3926): the rows the
    🧬 card shows (`TraitOfferStore.waiting`)."""
    from dmbot.memory.trait_offers import TraitOfferStore

    if src.memory is None:
        return Counted(0)
    return Counted(await TraitOfferStore(src.db).count(g, c))


async def _world(src: Sources, g: int, c: str) -> Counted:
    """DMbot's guesses about places and languages the DM hasn't checked (#4285): the lines the
    🌍 World notes screen shows (`MemoryStore.world_notes`), roads too while the map answers
    are on (#4670 4c)."""
    if src.memory is None:
        return Counted(0)
    notes = await src.memory.world_notes(g, c, with_roads=src.map_roads)
    return Counted(len(notes), min((n.made_at for n in notes), default=None))


async def _threads(src: Sources, g: int, c: str) -> Counted:
    """The story threads the lore and session readers suggested and the DM hasn't answered (#4453,
    #4522), and the progress lines on the threads the campaign has (5b): what the 📜 card shows
    (`ThreadSuggestionStore.waiting`, `ThreadProgressStore.waiting`), never one quoting someone no
    longer recorded."""
    from dmbot.memory.thread_progress import ThreadProgressStore
    from dmbot.memory.thread_suggestions import ThreadSuggestionStore

    if src.memory is None:
        return Counted(0)
    opted_in = await src.recorded(g, c) if src.recorded is not None else None
    new = await ThreadSuggestionStore(src.db).count(g, c, opted_in=opted_in)
    # (the progress lines on threads the campaign has: the same 📜 card's second stage, #4522 5b)
    moved = await ThreadProgressStore(src.db).count(g, c, opted_in=opted_in)
    return Counted(new + moved)


async def _secrets(src: Sources, g: int, c: str) -> Counted:
    """What the players know that sounded like a secret (#4524): the 🤫 questions the card shows
    (`MemoryStore.secret_questions_waiting`)."""
    if src.memory is None:
        return Counted(0)
    return Counted(await src.memory.secret_questions_count(g, c))


async def _joins(src: Sources, g: int, c: str) -> Counted:
    """Characters who joined the party, waiting for the DM's "what do they know?" (#4537 2b)."""
    if src.memory is None:
        return Counted(0)
    n, oldest = await src.memory.join_questions_waiting(g, c)
    return Counted(n, oldest)


async def _leaves(src: Sources, g: int, c: str) -> Counted:
    """A goodbye or a death, waiting for the DM's "still in the party?" (#4537 slice 6)."""
    if src.memory is None:
        return Counted(0)
    n, oldest = await src.memory.staying_questions_waiting(g, c)
    return Counted(n, oldest)


async def _reveals(src: Sources, g: int, c: str) -> Counted:
    """What the characters learned that waits for the DM's Keep or Not this (#3863): what the
    reveal card counts (`RevealProposals.count_waiting`), leaving out a proposal whose speaker is
    no longer recorded. With nobody known to be recorded it counts nothing, never everything."""
    from dmbot.memory.reveal_proposals import RevealProposals

    if src.memory is None or src.recorded is None:
        return Counted(0)
    opted_in = await src.recorded(g, c)
    return Counted(await RevealProposals(src.memory).count_waiting(g, c, opted_in=opted_in))


async def _deeds(src: Sources, g: int, c: str) -> Counted:
    """Deeds the party did that mattered to someone, waiting for the DM to record them (#3591):
    what the deed card counts (`DeedProposals.count_waiting`), leaving out a proposal whose
    speaker is no longer recorded. The speakers are the guild-wide `agreed` set, as the deed card's
    `consenting(guild_id)` uses (not the campaign's `recorded`, which reveals use).
    """
    from dmbot.memory.deed_proposals import DeedProposals

    if src.memory is None or src.agreed is None or src.deeds_on is None:
        return Counted(0)
    if not await src.deeds_on(g, c):
        return Counted(0)  # the switch is off, or the plan has no story memory
    return Counted(
        int(await DeedProposals(src.memory).count_waiting(g, c, opted_in=await src.agreed(g)))
    )


async def _maps(src: Sources, g: int, c: str) -> Counted:
    """Maps a player kept that no DM has answered yet (`MapStore.waiting_for_a_dm`)."""
    if src.maps is None:
        return Counted(0)
    waiting = await src.maps.waiting_for_a_dm(g, c)
    return Counted(len(waiting), min((m.created_at for m in waiting), default=None))


_COUNTERS: dict[str, Callable[[Sources, int, str], Awaitable[Counted]]] = {
    kinds.NAMES: _names,
    kinds.HEARD: _heard,
    kinds.LOOKALIKES: _lookalikes,
    kinds.CONNECTIONS: _connections,
    kinds.WHICH: _which,
    kinds.FAMILY: _family,
    kinds.TRAITS: _traits,
    kinds.WORLD: _world,
    kinds.THREADS: _threads,
    kinds.SECRETS: _secrets,
    kinds.JOINS: _joins,
    kinds.LEAVES: _leaves,
    kinds.REVEALS: _reveals,
    kinds.MAPS: _maps,
    kinds.CLAIMS: _claims,
    kinds.READINGS: _readings,
    kinds.DEEDS: _deeds,
    kinds.PLOT: _plot,
    kinds.RECAP_APPROVE: _recap_approve,
    kinds.RULES: _rules,
}
