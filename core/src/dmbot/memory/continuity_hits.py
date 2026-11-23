"""The continuity heads-ups worked out for the website on the DM's ask (#3695 half 1, 0605).

Discord's continuity card works its list out each time it opens (`ContinuityStore.check`, no AI).
The website cannot run that check, so the page asks the bot (`continuity-ask` on the queue), the
bot runs the same check with consent read at that moment, and keeps what it found here: one set for
each campaign, replaced whole on each ask, so asking again never adds a second row for a claim.

Pointers only, never words (GraphEngineer's answer (a) on #3695 and Supervisor's ruling): the claim,
the kind, the level, the entry, the earlier fact and its session, the character learning a secret,
whether the earlier fact is pinned. Words kept here would go stale the moment someone stops being
recorded or a fact changes; the read door works them out from these at read time, with consent read
then. An answered claim leaves the set at once (`drop`). Swept after a day. Never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory.continuity import Conflict

LIFE_S = 24 * 60 * 60  # the page asks again before it reads; a set older than a day is not wanted
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class Hit:
    """One heads-up as the table keeps it: pointers, no words."""

    claim_id: str
    kind: str
    level: str
    entity_id: str
    earlier_id: str | None
    earlier_session: int | None
    learner_id: str | None
    pinned: bool


def _id(value: str | None) -> str | None:
    """An entry's or fact's id as the table takes it (32 hex), else None (never a guess)."""
    return value if value is not None and len(value) == 32 and set(value) <= _HEX else None


def hit_of(conflict: Conflict) -> Hit | None:
    """The pointers of one heads-up the check found. `earlier_id` is the first fact a "the story
    changed" answer would end (a clash, a pinned fact), and None for the two death kinds: there the
    read door reads the entry's own open ending (dead or destroyed) by `entity_id`, as the card's
    line does. None when the claim or its entry has no id the table can hold (the page would
    have nothing to point at)."""
    claim_id, entity_id = _id(conflict.claim.id), _id(conflict.entity_id)
    if claim_id is None or entity_id is None:
        return None
    earlier = _id(conflict.clash_ids[0]) if conflict.clash_ids else None
    return Hit(
        claim_id=claim_id,
        kind=conflict.kind[:40],
        level=conflict.level[:20],
        entity_id=entity_id,
        earlier_id=earlier,
        earlier_session=conflict.earlier.session_started_at if conflict.earlier else None,
        learner_id=_id(conflict.learner_id),
        pinned=conflict.pinned,
    )


class ContinuityHits:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def replace(self, guild_id: int, campaign_id: str, hits: Sequence[Hit]) -> int:
        """The campaign's set becomes exactly `hits`, in this order (one row per claim and kind);
        returns how many are kept. One transaction: the page never reads half a set."""
        kept: list[Hit] = []
        seen: set[tuple[str, str]] = set()
        for h in hits:  # (the table's key: a claim and kind found twice is one heads-up)
            if (h.claim_id, h.kind) not in seen:
                seen.add((h.claim_id, h.kind))
                kept.append(h)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM continuity_hits WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            now = int(self._now())
            for position, h in enumerate(kept):
                await conn.execute(
                    "INSERT INTO continuity_hits (guild_id, campaign_id, claim_id, kind, level,"
                    " entity_id, earlier_id, earlier_session, learner_id, pinned, position,"
                    " asked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, campaign_id, h.claim_id, h.kind, h.level, h.entity_id,
                        h.earlier_id, h.earlier_session, h.learner_id, h.pinned, position, now,
                    ),
                )  # fmt: skip
        return len(kept)

    async def listed(self, guild_id: int, campaign_id: str) -> list[Hit]:
        """The campaign's set, in its order (for the bot and the tests; the page has its door)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT claim_id, kind, level, entity_id, earlier_id, earlier_session, learner_id,"
                " pinned FROM continuity_hits WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY position",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            Hit(
                r["claim_id"], r["kind"], r["level"], r["entity_id"], r["earlier_id"],
                r["earlier_session"], r["learner_id"], bool(r["pinned"]),
            )
            for r in rows
        ]  # fmt: skip

    async def has(self, guild_id: int, campaign_id: str, claim_id: str) -> bool:
        """Whether the last ask listed this claim: the page answers only its own lines, as the
        card does."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM continuity_hits WHERE guild_id = %s AND campaign_id = %s"
                " AND claim_id = %s LIMIT 1",
                (guild_id, campaign_id, claim_id),
            )
            return await cur.fetchone() is not None

    async def drop(self, guild_id: int, campaign_id: str, claim_id: str) -> None:
        """An answered claim leaves the set at once (its page line goes, whatever its kind)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM continuity_hits WHERE guild_id = %s AND campaign_id = %s"
                " AND claim_id = %s",
                (guild_id, campaign_id, claim_id),
            )

    async def sweep(self, guild_id: int) -> int:
        """Sets older than a day; how many rows (counts only in the log)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM continuity_hits WHERE guild_id = %s AND asked_at < %s",
                (guild_id, int(self._now()) - LIFE_S),
            )
            return int(cur.rowcount)
