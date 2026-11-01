"""An internal importance rank for characters, 0 to 5 (#3172, owner decision 2026-11-01).

The levels, in the owner's words: 0 background, 1 minor, 2 recurring, 3 significant, 4 major,
5 player character. The rank only decides which details a recap or PlotBot picks; it states no
story fact, nobody sets it, and it is never shown to a player, a card or a recap (a "Major" beside
a hidden villain would be a spoiler). Nothing here asks the DM.

Where a rank comes from, strongest first:
- the FLOOR, worked out from what the code can count and never stored (so it cannot drift): a
  player character is 5; a character heard in two or more separate sessions is at least 2;
- PlotBot's reading of the story, one assessment at a time in `memory_importance` (append-only,
  like the rest of the graph); the newest is current, and the rank is never read below the floor.

A merged name's assessments and sessions count for the one that stayed. This is a different thing
from `memory/importance.py` (#2411), which scores a NAME by how connected it is and only orders
beats; this rank says how much a CHARACTER matters to the story and is what slice 4 sorts recaps by,
with that score as the tie-break inside a rank. This is the data and the reader (slice 1);
PlotBot's call, the connection nudge and the recap use come in later slices.
Counts and fixed codes only: no word of a transcript is stored.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from dmbot.db import Database

BACKGROUND, MINOR, RECURRING, SIGNIFICANT, MAJOR, PLAYER = range(6)
LEVEL_NAMES = ("Background", "Minor", "Recurring", "Significant", "Major", "Player character")
SESSIONS_FOR_RECURRING = 2  # separate sessions a character is heard in to be at least Recurring

SOURCE_PLOTBOT, SOURCE_RULE = "plotbot", "rule"
# The reason codes (a database CHECK holds the same list; a test keeps the two equal).
BASES = (
    "background", "met_a_pc", "recurring", "quest_target", "quest_giver", "mentor", "rival",
    "henchman", "mini_boss", "companion", "antagonist", "ruler_or_god", "lore_figure", "corrected",
)  # fmt: skip
MAX_LINES = 20  # line numbers kept with one assessment


def floor(is_player_character: bool, sessions_heard: int) -> int:
    """The lowest rank a character can have, from facts the code counts. Pure."""
    if is_player_character:
        return PLAYER
    return RECURRING if sessions_heard >= SESSIONS_FOR_RECURRING else BACKGROUND


def effective(floor_rank: int, assessed: int | None) -> int:
    """The current rank: the newest assessment, never below the floor. Pure."""
    return max(floor_rank, assessed if assessed is not None else BACKGROUND)


def level_name(rank: int) -> str:
    """The owner's word for a rank (for logs and tools; never for a player)."""
    return LEVEL_NAMES[max(BACKGROUND, min(PLAYER, rank))]


@dataclass(frozen=True, slots=True)
class Assessment:
    """One assessment of one character."""

    entity_id: str
    rank: int
    basis: str
    source: str = SOURCE_PLOTBOT
    session_started_at: int | None = None
    lines: tuple[int, ...] = field(default=())

    def valid(self) -> bool:
        return (
            bool(self.entity_id)
            and BACKGROUND <= self.rank <= PLAYER
            and self.basis in BASES
            and self.source in (SOURCE_PLOTBOT, SOURCE_RULE)
        )


class ImportanceStore:
    """Reads and appends assessments. The caller has already checked who may ask; the rows are
    this campaign's only (row-level security and composite keys)."""

    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    async def add(self, guild_id: int, campaign_id: str, assessments: Iterable[Assessment]) -> int:
        """Append these assessments and return how many were written. One that is not valid, names
        a character the campaign does not have (or an entry that is not a character), or says the
        rank the character is already assessed at is skipped, so a re-run writes nothing twice (a
        repeat with another reason code adds no row: the rank is what counts)."""
        wanted = [a for a in assessments if a.valid()]
        if not wanted:
            return 0
        written = 0
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (entity_id) entity_id, rank FROM memory_importance"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY entity_id, seq DESC",
                (guild_id, campaign_id),
            )
            latest = {str(r["entity_id"]): int(r["rank"]) for r in await cur.fetchall()}
            for a in wanted:
                if latest.get(a.entity_id) == a.rank:
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, basis,"
                    " source, session_started_at, lines, recorded_at)"
                    " SELECT %s, %s, e.id, %s, %s, %s, %s, %s, %s FROM memory_entities e"
                    " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = %s"
                    " AND e.type = 'character' AND e.merged_into IS NULL"
                    " AND e.status IN ('proposed', 'confirmed')",
                    (
                        guild_id, campaign_id, a.rank, a.basis, a.source, a.session_started_at,
                        list(a.lines[:MAX_LINES]), now, guild_id, campaign_id, a.entity_id,
                    ),
                )  # fmt: skip
                if cur.rowcount:
                    latest[a.entity_id] = a.rank
                    written += 1
        return written

    async def ranks(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        """The current rank of every live character of the campaign (entry id -> 0 to 5).

        A merged name counts for the character it ended up in, however many merges that took
        (A into B, then B into C leaves A pointing at B, as `MemoryStore.resolve` follows): its
        sessions join the survivor's for the floor, and the newest assessment of the whole group
        is the current one (by `seq`, so a correction after a merge still lowers the rank)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, role, status, merged_into FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND type = 'character'"
                " AND status IN ('proposed', 'confirmed', 'merged')",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
            cur = await conn.execute(
                "SELECT DISTINCT ON (entity_id) entity_id, seq, rank FROM memory_importance"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY entity_id, seq DESC",
                (guild_id, campaign_id),
            )
            newest = {
                str(r["entity_id"]): (int(r["seq"]), int(r["rank"])) for r in await cur.fetchall()
            }
            cur = await conn.execute(
                "SELECT entity_id, array_agg(DISTINCT session_started_at) AS sessions"
                " FROM memory_heard WHERE guild_id = %s AND campaign_id = %s GROUP BY entity_id",
                (guild_id, campaign_id),
            )
            heard = {str(r["entity_id"]): set(r["sessions"]) for r in await cur.fetchall()}
        by_id = {str(r["id"]): r for r in rows}

        def root(entity_id: str) -> str | None:
            """The live character an entry ended up as, or None (a cycle, or one that is gone)."""
            seen: set[str] = set()
            current: str | None = entity_id
            while current is not None and current not in seen:
                seen.add(current)
                row = by_id.get(current)
                if row is None:
                    return None
                if row["status"] != "merged":
                    return current
                current = row["merged_into"]
            return None

        assessed: dict[str, tuple[int, int]] = {}
        sessions: dict[str, set[int]] = {}
        for entity_id in by_id:
            at = root(entity_id)
            if at is None:
                continue
            if entity_id in newest and newest[entity_id][0] > assessed.get(at, (0, 0))[0]:
                assessed[at] = newest[entity_id]
            sessions.setdefault(at, set()).update(heard.get(entity_id, ()))
        out: dict[str, int] = {}
        for entity_id, row in by_id.items():
            if row["status"] == "merged":
                continue  # (a merged name has no rank of its own)
            player = row["role"] == "player_character"
            latest = assessed.get(entity_id)
            out[entity_id] = effective(
                floor(player, len(sessions.get(entity_id, ()))), latest[1] if latest else None
            )
        return out


def counts(ranks: Mapping[str, int]) -> list[int]:
    """How many characters are at each rank, 0 to 5 (for a log line: counts only)."""
    out = [0] * (PLAYER + 1)
    for rank in ranks.values():
        out[max(BACKGROUND, min(PLAYER, rank))] += 1
    return out
