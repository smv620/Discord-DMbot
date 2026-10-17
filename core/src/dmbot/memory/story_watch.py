"""Watch the story, the first check (docs/STORY_MEMORY.md, section 6, "5d slice 5"; #1729).

The DM's adventure says "A needs B alive" (a plan link, #1714). The DM said the book's B is the
table's B (#1715). The table says B is dead. DMbot tells the DM, privately, once. No AI: a few
reads of the database, and a table of conditions that cannot both be true (`clashes`, pure, no
database).

DMbot only points. It never says what to do, never changes the table's story and never tells a
player. A clash is a row in `plan_divergences` (one for each plan link and each condition it saw),
so it is shown once; "I know" ends it; an Undo of the condition, a condition that changed again or
a book link taken off marks it gone at the next check. A name the DM rejected, or merged into
another, never counts.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.adventure import AdventureError, AdventureStore
from dmbot.memory.book_links import chapter_of
from dmbot.memory.models import (
    ALIVE,
    CAPTURED,
    DEAD,
    DESTROYED,
    LOST,
    MISSING,
    TRANSFORMED,
    new_id,
)

QUOTE_SHOWN = 200  # the book's words, at most this many characters (the plan keeps as many)
SHOWN = 5  # clashes in one message; the rest wait for the next check
FOUND = "found"
PRESENT = "present"

# What the book needs, against what the table now says (all lower case). Any other `need` text is
# ignored, and so is "unknown": DMbot would be guessing.
CONTRADICTS: dict[str, frozenset[str]] = {
    ALIVE: frozenset({DEAD, MISSING, CAPTURED, TRANSFORMED}),
    PRESENT: frozenset({DEAD, MISSING, CAPTURED, TRANSFORMED}),
    FOUND: frozenset({LOST, DESTROYED}),
}
# "Dead" in the book against "alive" at the table only counts when the table said it after the plan
# was read: before that, the DM may simply have set the starting state the book describes.
AFTER_ONLY = {DEAD: frozenset({ALIVE})}


def clashes(need: str, condition: str, *, set_after_plan: bool) -> bool:
    """Can the book's `need` and the table's `condition` not both be true?"""
    word = re.sub(r"[^a-z]+", " ", need.lower()).strip()
    if word in CONTRADICTS:
        return condition in CONTRADICTS[word]
    return set_after_plan and condition in AFTER_ONLY.get(word, frozenset())


@dataclass(frozen=True, slots=True)
class Clash:
    """One clash, ready to be told in plain words."""

    id: str  # the divergence
    book_name: str  # who needs it (a chapter, a place or a person)
    chapter: str  # the chapter the book's A is in (empty when A is a chapter or none is known)
    book_needs: str  # the book's name for B
    table_name: str  # the table's name for B
    need: str
    condition: str
    session_started_at: int | None  # when the table said it, for "Session 4"


@dataclass(frozen=True, slots=True)
class Found:
    shown: tuple[Clash, ...]
    more: int  # clashes past what one message shows; they come with the next check


NONE = Found((), 0)

_CANDIDATES = """
    SELECT l.id AS link_id, l.need, l.created_at AS plan_at,
           a.name AS a_name, b.name AS b_name,
           CASE WHEN a.kind = 'chapter' THEN '' ELSE COALESCE({chapter}, '') END AS chapter,
           e.id AS entity_id, e.name AS entity_name,
           s.id AS state_id, s.condition, s.from_session_at, s.created_at AS state_at
    FROM plan_links l
    JOIN plan_same_as sa ON sa.guild_id = l.guild_id AND sa.campaign_id = l.campaign_id
        AND sa.plan_id = l.to_id AND sa.status = 'same'
    JOIN memory_entities e ON e.guild_id = sa.guild_id AND e.campaign_id = sa.campaign_id
        AND e.id = sa.entity_id AND e.status IN ('proposed', 'confirmed')
    JOIN memory_states s ON s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id
        AND s.entity_id = e.id AND s.closed_at IS NULL
    JOIN plan_nodes a ON a.guild_id = l.guild_id AND a.campaign_id = l.campaign_id
        AND a.id = l.from_id
    JOIN plan_nodes b ON b.guild_id = l.guild_id AND b.campaign_id = l.campaign_id
        AND b.id = l.to_id
    WHERE l.guild_id = %s AND l.campaign_id = %s{extra}
      AND NOT EXISTS (SELECT 1 FROM plan_divergences d WHERE d.guild_id = l.guild_id
          AND d.campaign_id = l.campaign_id AND d.plan_link_id = l.id AND d.state_id = s.id)
    ORDER BY l.created_at, l.id, e.id
"""
_GONE = """
    UPDATE plan_divergences d SET status = 'gone'
    WHERE d.guild_id = %s AND d.campaign_id = %s AND d.status = 'open'
      AND (NOT EXISTS (SELECT 1 FROM memory_entities e WHERE e.guild_id = d.guild_id
               AND e.campaign_id = d.campaign_id AND e.id = d.entity_id
               AND e.status IN ('proposed', 'confirmed'))
           OR NOT EXISTS (SELECT 1 FROM memory_states s WHERE s.guild_id = d.guild_id
               AND s.campaign_id = d.campaign_id AND s.id = d.state_id AND s.closed_at IS NULL)
           OR NOT EXISTS (SELECT 1 FROM plan_links l JOIN plan_same_as sa
               ON sa.guild_id = l.guild_id AND sa.campaign_id = l.campaign_id
                  AND sa.plan_id = l.to_id AND sa.status = 'same'
               WHERE l.guild_id = d.guild_id AND l.campaign_id = d.campaign_id
                 AND l.id = d.plan_link_id AND sa.entity_id = d.entity_id))
"""


class StoryWatch:
    """The divergence check for every campaign, one campaign at a time (server and campaign in
    every statement; row-level security does the rest)."""

    def __init__(self, db: Database, *, clock: Callable[[], float] | None = None) -> None:
        self._db = db
        self._clock = clock or time.time

    async def check(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        entity_id: str | None = None,
        state_id: str | None = None,
    ) -> Found:
        """Look for clashes not told yet, and keep them so they are told once. Only while Shared
        story is on and a DM said here that they have the right (as for the reader; the owner's
        plan is the caller's to check). Narrowed to one table entry or one condition after the DM
        sets it; the clashes that went away are marked either way. At most `SHOWN` come back and
        are kept; the rest are counted and come with a later check."""
        now = int(self._clock())
        extra, args = "", [guild_id, campaign_id]
        if entity_id is not None:
            extra += " AND e.id = %s"
            args.append(entity_id)
        if state_id is not None:
            extra += " AND s.id = %s"
            args.append(state_id)
        async with self._db.guild(guild_id) as conn:
            try:
                await AdventureStore.require_on(conn, guild_id, campaign_id)
            except AdventureError:
                return NONE
            # One check at a time for a campaign, so two at once can't split the clashes.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"dmbot.story_watch:{campaign_id}",),
            )
            await conn.execute(_GONE, (guild_id, campaign_id))
            cur = await conn.execute(_CANDIDATES.format(extra=extra, chapter=chapter_of("a")), args)
            due = [
                r
                for r in await cur.fetchall()
                if clashes(
                    r["need"], r["condition"], set_after_plan=int(r["state_at"]) > int(r["plan_at"])
                )
            ]
            shown: list[Clash] = []
            for r in due[:SHOWN]:
                cur = await conn.execute(
                    "INSERT INTO plan_divergences (guild_id, campaign_id, id, plan_link_id,"
                    " entity_id, state_id, status, created_at) VALUES (%s, %s, %s, %s, %s, %s,"
                    " 'open', %s) ON CONFLICT DO NOTHING RETURNING id",
                    (guild_id, campaign_id, new_id(), r["link_id"], r["entity_id"],
                     r["state_id"], now),
                )  # fmt: skip
                got = await cur.fetchone()
                if got is not None:  # (a second check at the same moment told it first)
                    shown.append(_clash(got["id"], r))
            return Found(tuple(shown), max(0, len(due) - SHOWN))

    async def know(self, guild_id: int, campaign_id: str, divergence_id: str) -> bool:
        """The DM's "I know": it is not shown again. False when it is already answered or gone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE plan_divergences SET status = 'known' WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND status = 'open' AND EXISTS (SELECT 1 FROM"
                " memory_states s WHERE s.guild_id = plan_divergences.guild_id AND"
                " s.campaign_id = plan_divergences.campaign_id AND s.id = plan_divergences.state_id"
                " AND s.closed_at IS NULL) RETURNING id",
                (guild_id, campaign_id, divergence_id),
            )
            return await cur.fetchone() is not None

    async def open_for(
        self, guild_id: int, campaign_id: str, node_ids: frozenset[str], *, verified: bool = False
    ) -> list[Clash]:
        """The clashes already told and not answered that involve these book entries (Guide me
        lists the ones for the chapter the table is in). At most `SHOWN`. `verified`: the caller has
        just checked that Shared story is on with a DM's confirmation, so it is not asked twice."""
        if not node_ids:
            return []
        async with self._db.guild(guild_id) as conn:
            if not verified:
                try:
                    await AdventureStore.require_on(conn, guild_id, campaign_id)
                except AdventureError:
                    return []
            cur = await conn.execute(
                "SELECT d.id, a.name AS a_name, b.name AS b_name, e.name AS entity_name, l.need,"
                " s.condition, s.from_session_at,"
                " CASE WHEN a.kind = 'chapter' THEN '' ELSE COALESCE("
                + chapter_of("a")
                + ", '') END AS chapter FROM plan_divergences d"
                " JOIN plan_links l ON l.guild_id = d.guild_id AND l.campaign_id = d.campaign_id"
                " AND l.id = d.plan_link_id"
                " JOIN plan_nodes a ON a.guild_id = l.guild_id AND a.campaign_id = l.campaign_id"
                " AND a.id = l.from_id"
                " JOIN plan_nodes b ON b.guild_id = l.guild_id AND b.campaign_id = l.campaign_id"
                " AND b.id = l.to_id"
                " JOIN memory_entities e ON e.guild_id = d.guild_id"
                " AND e.campaign_id = d.campaign_id AND e.id = d.entity_id"
                " JOIN memory_states s ON s.guild_id = d.guild_id AND s.campaign_id = d.campaign_id"
                " AND s.id = d.state_id AND s.closed_at IS NULL"
                " WHERE d.guild_id = %s AND d.campaign_id = %s AND d.status = 'open'"
                " AND (l.from_id = ANY(%s) OR l.to_id = ANY(%s))"
                " ORDER BY d.created_at, d.id LIMIT %s",
                (guild_id, campaign_id, list(node_ids), list(node_ids), SHOWN),
            )
            return [_clash(r["id"], r) for r in await cur.fetchall()]

    async def retract(self, guild_id: int, campaign_id: str, divergence_ids: list[str]) -> None:
        """Forget clashes that could not be posted (Discord was down), so the next check tells
        them again instead of losing them. Only ones still unanswered."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM plan_divergences WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'open' AND id = ANY(%s)",
                (guild_id, campaign_id, divergence_ids),
            )

    async def words(self, guild_id: int, campaign_id: str, divergence_id: str) -> str | None:
        """The book's words behind a clash, for the DM only: None when it is gone."""
        async with self._db.guild(guild_id) as conn:
            try:
                await AdventureStore.require_on(conn, guild_id, campaign_id)
            except AdventureError:
                return None  # the switch was turned off: the book's words go no further
            cur = await conn.execute(
                "SELECT l.quote FROM plan_divergences d JOIN plan_links l"
                " ON l.guild_id = d.guild_id AND l.campaign_id = d.campaign_id"
                " AND l.id = d.plan_link_id WHERE d.guild_id = %s AND d.campaign_id = %s"
                " AND d.id = %s AND d.status <> 'gone' AND EXISTS (SELECT 1 FROM memory_states s"
                " WHERE s.guild_id = d.guild_id AND s.campaign_id = d.campaign_id"
                " AND s.id = d.state_id AND s.closed_at IS NULL)",
                (guild_id, campaign_id, divergence_id),
            )
            row = await cur.fetchone()
            return None if row is None else str(row["quote"])[:QUOTE_SHOWN]

    async def count(self, guild_id: int, campaign_id: str, status: str) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM plan_divergences WHERE guild_id = %s"
                " AND campaign_id = %s AND status = %s",
                (guild_id, campaign_id, status),
            )
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])


def _clash(divergence_id: str, r: Any) -> Clash:
    return Clash(
        divergence_id, r["a_name"], r["chapter"], r["b_name"], r["entity_name"], r["need"],
        r["condition"], r["from_session_at"],
    )  # fmt: skip


__all__ = ["CONTRADICTS", "NONE", "SHOWN", "Clash", "Found", "StoryWatch", "clashes"]
