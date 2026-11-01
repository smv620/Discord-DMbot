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

from dmbot.db import Conn, Database

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


# The connection nudge (slice 2): connections only nudge, by at most one rank, and never past Major.
# A pair of characters or groups joined by a connection the players are shown counts once; one made
# in play counts 1, one that came from a lore upload (world knowledge, not seen in play) a quarter
# as much, so a lorebook cannot make everyone in it important. Six in play lifts a rank.
NUDGE_AT = 6.0
LORE_WEIGHT = 0.25


def nudged(rank: int, play: int, lore: int) -> int:
    """`rank` lifted by one when the connections reach `NUDGE_AT` (`play` in play, `lore` from a
    lore upload at a quarter each). A player character (5) and a Major (4) never move: the nudge
    stops short of Major from below, so it can raise a Significant to Major and no further. Pure."""
    if rank >= MAJOR or play + lore * LORE_WEIGHT < NUDGE_AT:
        return rank
    return rank + 1


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


def roots(entities: Mapping[str, Mapping[str, object]]) -> dict[str, str | None]:
    """For every entry, the live character it ended up as (itself when it is live), or None (a
    cycle, or one that is gone). A merge leaves older merges pointing at the entry that merged
    later (`MemoryStore.resolve` follows the chain), so the walk follows it too. `entities`: entry
    id -> a row with `status` and `merged_into`. Pure; the one walk both the reader and the writer
    use, so they cannot disagree about who a name is."""
    out: dict[str, str | None] = {}
    for entity_id in entities:
        seen: set[str] = set()
        current: str | None = entity_id
        found: str | None = None
        while current is not None and current not in seen:
            seen.add(current)
            row = entities.get(current)
            if row is None:
                break
            if row["status"] != "merged":
                found = current
                break
            nxt = row["merged_into"]
            current = str(nxt) if nxt is not None else None
        out[entity_id] = found
    return out


class ImportanceStore:
    """Reads and appends assessments. The caller has already checked who may ask; the rows are
    this campaign's only (row-level security and composite keys)."""

    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    @staticmethod
    async def _state(
        conn: Conn, guild_id: int, campaign_id: str
    ) -> tuple[dict[str, Mapping[str, object]], dict[str, str | None], dict[str, tuple[int, int]]]:
        """The campaign's characters (live and merged), who each ended up as, and for each live
        character the newest assessment of its whole merged group as (seq, rank)."""
        cur = await conn.execute(
            "SELECT id, role, status, merged_into FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND type = 'character'"
            " AND status IN ('proposed', 'confirmed', 'merged')",
            (guild_id, campaign_id),
        )
        entities: dict[str, Mapping[str, object]] = {str(r["id"]): r for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT DISTINCT ON (entity_id) entity_id, seq, rank FROM memory_importance"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY entity_id, seq DESC",
            (guild_id, campaign_id),
        )
        newest = {
            str(r["entity_id"]): (int(r["seq"]), int(r["rank"])) for r in await cur.fetchall()
        }
        ended = roots(entities)
        group: dict[str, tuple[int, int]] = {}
        for entity_id, at in ended.items():
            if (
                at is not None
                and entity_id in newest
                and newest[entity_id][0] > group.get(at, (0, 0))[0]
            ):
                group[at] = newest[entity_id]
        return entities, ended, group

    async def add(self, guild_id: int, campaign_id: str, assessments: Iterable[Assessment]) -> int:
        """Append these assessments and return how many were written. One that is not valid, names
        a character the campaign does not have (or an entry that is not a live character), or says
        the rank the character's whole merged group is already assessed at is skipped, so a re-run
        writes nothing twice (a repeat with another reason code adds no row: the rank is what
        counts). The comparison is with the group's newest, as `ranks()` reads it, so a correction
        back to a rank an older twin once had is still written."""
        wanted = [a for a in assessments if a.valid()]
        if not wanted:
            return 0
        written = 0
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            entities, ended, group = await self._state(conn, guild_id, campaign_id)
            top = max((seq for seq, _ in group.values()), default=0)
            for a in wanted:
                row = entities.get(a.entity_id)
                if row is None or row["status"] == "merged" or ended.get(a.entity_id) is None:
                    continue  # (not a live character of this campaign)
                if group.get(a.entity_id, (0, -1))[1] == a.rank:
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, basis,"
                    " source, session_started_at, lines, recorded_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, campaign_id, a.entity_id, a.rank, a.basis, a.source,
                        a.session_started_at, list(a.lines[:MAX_LINES]), now,
                    ),
                )  # fmt: skip
                if cur.rowcount:
                    top += 1
                    group[a.entity_id] = (top, a.rank)
                    written += 1
        return written

    @staticmethod
    async def _links(
        conn: Conn, guild_id: int, campaign_id: str, ended: Mapping[str, str | None]
    ) -> dict[str, tuple[int, int]]:
        """For each live character, how many other characters or groups it is joined to by a
        connection the players are shown, as (made in play, from a lore upload). A pair counts once
        (in play when any of its connections was), and a merged name counts for the one it became.
        A secret or not yet revealed connection counts for nothing: a hidden fact adds no rank."""
        cur = await conn.execute(
            "SELECT r.subject_id, r.object_id, bool_or(r.source <> 'background') AS in_play"
            " FROM memory_relations r"
            " JOIN memory_entities a ON a.guild_id = r.guild_id AND a.campaign_id = r.campaign_id"
            "  AND a.id = r.subject_id AND a.type IN ('character', 'faction')"
            " AND a.status IN ('confirmed', 'merged')"
            " JOIN memory_entities b ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id"
            "  AND b.id = r.object_id AND b.type IN ('character', 'faction')"
            " AND b.status IN ('confirmed', 'merged')"
            " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.status = 'confirmed'"
            " AND NOT r.secret AND NOT r.unrevealed AND r.predicate <> 'plays'"
            " GROUP BY r.subject_id, r.object_id",
            (guild_id, campaign_id),
        )
        pairs: dict[frozenset[str], bool] = {}
        for r in await cur.fetchall():
            # (a group is not a character, so it is not in `ended`: it stands for itself)
            ids = (str(r["subject_id"]), str(r["object_id"]))
            one, other = (ended.get(i, i) for i in ids)
            if one is None or other is None or one == other:
                continue
            key = frozenset((one, other))
            pairs[key] = pairs.get(key, False) or bool(r["in_play"])
        out: dict[str, list[int]] = {}
        for pair, in_play in pairs.items():
            for entity_id in pair:
                out.setdefault(entity_id, [0, 0])[0 if in_play else 1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    async def ranks(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        """The current rank of every live character of the campaign (entry id -> 0 to 5).

        A merged name counts for the character it ended up in, however many merges that took:
        its sessions join the survivor's for the floor, and the newest assessment of the whole
        group is the current one (by `seq`, so a correction after a merge still lowers the
        rank)."""
        async with self._db.guild(guild_id) as conn:
            entities, ended, group = await self._state(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "SELECT entity_id, array_agg(DISTINCT session_started_at) AS sessions"
                " FROM memory_heard WHERE guild_id = %s AND campaign_id = %s GROUP BY entity_id",
                (guild_id, campaign_id),
            )
            heard = {str(r["entity_id"]): set(r["sessions"]) for r in await cur.fetchall()}
            links = await self._links(conn, guild_id, campaign_id, ended)
        sessions: dict[str, set[int]] = {}
        for entity_id, at in ended.items():
            if at is not None:
                sessions.setdefault(at, set()).update(heard.get(entity_id, ()))
        out: dict[str, int] = {}
        for entity_id, row in entities.items():
            if row["status"] == "merged":
                continue  # (a merged name has no rank of its own)
            player = row["role"] == "player_character"
            latest = group.get(entity_id)
            base = effective(
                floor(player, len(sessions.get(entity_id, ()))), latest[1] if latest else None
            )
            play, lore = links.get(entity_id, (0, 0))
            out[entity_id] = nudged(base, play, lore)
        return out


def counts(ranks: Mapping[str, int]) -> list[int]:
    """How many characters are at each rank, 0 to 5 (for a log line: counts only)."""
    out = [0] * (PLAYER + 1)
    for rank in ranks.values():
        out[max(BACKGROUND, min(PLAYER, rank))] += 1
    return out
