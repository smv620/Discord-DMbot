"""An internal importance rank for every entry, 0 to 5 (#3172, owner decision 2026-11-01).

The levels, in the owner's words: 0 background, 1 minor, 2 recurring, 3 significant, 4 major,
5 player character. The rank only decides which details a recap or PlotBot picks; it states no
story fact, nobody sets it, and it is never shown to a player, a card or a recap (a "Major" beside
a hidden villain would be a spoiler). Nothing here asks the DM.

Where a rank comes from, strongest first:
- the FLOOR, worked out from what the code can count and never stored (so it cannot drift): a
  player character is 5; a player character's companion at least 3; an entry heard in two or more
  separate sessions at least 2; one spoken of in a kept private scene at least 2 (#4110); and the
  connection table (`CONNECTION_FLOORS`: the more entries it
  is joined to, the higher its lowest rank), the highest of them and never their sum;
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
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field

from dmbot.db import Conn, Database
from dmbot.memory.models import name_key

BACKGROUND, MINOR, RECURRING, SIGNIFICANT, MAJOR, PLAYER = range(6)
LEVEL_NAMES = ("Background", "Minor", "Recurring", "Significant", "Major", "Player character")
SESSIONS_FOR_RECURRING = 2  # separate sessions a character is heard in to be at least Recurring

SOURCE_PLOTBOT, SOURCE_RULE = "plotbot", "rule"
# Why a rank changed, a property of the assessment, never a word about the entry (#3172, owner
# 2026-11-01: "corrected" said nothing about the character). What the entry IS in the story is its
# roles (`memory/roles.py`). A database CHECK holds the same list; a test keeps the two equal.
READING, DM_CORRECTED, REVEALED, DEMOTED, RULE = (
    "reading", "dm_corrected", "revealed", "demoted", "rule",
)  # fmt: skip
# A being met in play that the story never named, once the DM names or joins it from the list of
# them (#3172 slice 5, #4045): it enters at rank 0 with this reason.
MET_IN_PLAY = "met_in_play"
# A rank PlotBot raised for a private scene between two player characters (#2996 rule 5).
PRIVATE_SCENE = "private_scene"
REASONS = (READING, DM_CORRECTED, REVEALED, DEMOTED, RULE, MET_IN_PLAY, PRIVATE_SCENE)
ROLE_REASONS = (READING, DM_CORRECTED, REVEALED, RULE)  # why a role was recorded (none is demoted)
# An old backup's `basis` code, as migration 0388 mapped the rows already stored.
OLD_BASIS = frozenset({
    "background", "met_a_pc", "recurring", "quest_target", "quest_giver", "mentor", "rival",
    "henchman", "mini_boss", "companion", "antagonist", "ruler_or_god", "lore_figure", "corrected",
})  # fmt: skip


def reason_of_old_basis(basis: str) -> str:
    """The reason an old `basis` code reads as (migration 0388): "corrected" was the DM's
    correction, every other code a reading. Pure."""
    return DM_CORRECTED if basis == "corrected" else READING


MAX_LINES = 20  # line numbers kept with one assessment


# Connections set a floor, never a +1 (owner, 2026-11-01): the more pairs of entries an entry is
# joined to, the higher the lowest rank it can have. A lookup table, so changing it needs no rebuild
# (the floor is computed on read) and counts never add up on top of each other. No count reaches
# Major or the player characters' 5: busy is not the same as carrying the plot, which only the
# story's reading says. (pairs needed, floor), smallest first.
CONNECTION_FLOORS = ((4, MINOR), (8, RECURRING), (12, SIGNIFICANT))
COMPANION_FLOOR = SIGNIFICANT  # a player character's companion (`travels_with`, #3176)
COMPANION_PREDICATE = "travels_with"


def connection_floor(pairs: int) -> int:
    """The lowest rank `pairs` connected entries give an entry, from `CONNECTION_FLOORS`. Pure."""
    out = BACKGROUND
    for needed, rank in CONNECTION_FLOORS:
        if pairs >= needed:
            out = rank
    return out


def floor(
    is_player_character: bool,
    sessions_heard: int,
    pairs: int = 0,
    companion: bool = False,
    private_scene: bool = False,
) -> int:
    """The lowest rank an entry can have, from facts the code counts: a player character is 5, a
    player character's companion at least Significant, one heard in two separate sessions at
    least Recurring, one spoken of in a kept private scene between two player characters at least
    Recurring (#2996 rule 5, #4110: the owner's "always matters, always"), and the connection
    table. The highest of them, never their sum. Pure."""
    if is_player_character:
        return PLAYER
    return max(
        RECURRING if sessions_heard >= SESSIONS_FOR_RECURRING else BACKGROUND,
        RECURRING if private_scene else BACKGROUND,
        COMPANION_FLOOR if companion else BACKGROUND,
        connection_floor(pairs),
    )


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
    reason: str
    source: str = SOURCE_PLOTBOT
    session_started_at: int | None = None
    lines: tuple[int, ...] = field(default=())

    def valid(self) -> bool:
        return (
            bool(self.entity_id)
            and BACKGROUND <= self.rank <= PLAYER
            and self.reason in REASONS
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


def _is_player(row: Mapping[str, object]) -> bool:
    return row["type"] == "character" and row["role"] == "player_character"


def links_from(
    relations: Iterable[tuple[str, str, str]],
    entities: Mapping[str, Mapping[str, object]],
    ended: Mapping[str, str | None],
) -> tuple[dict[str, int], set[str]]:
    """For each live entry, how many other live entries it is joined to by a current, confirmed
    connection, and which entries are a player character's companion. `relations`: (subject id,
    object id, predicate) of the connections that are confirmed, not ended, not a `plays` link and
    not made by The party (the caller filters those; the database reader does it in its query).

    A pair counts once however many connections join it, and a joined (merged) entry counts for
    the one it became, so the union of their connections is counted. Every source counts in full:
    a lore upload, play, and a secret or not yet revealed connection (the rank is never shown to a
    player, STORY_MEMORY 9c). A family link worked out from two stated ones counts once the DM has
    confirmed it, as any other. Pure."""
    pairs: set[frozenset[str]] = set()
    companions: set[str] = set()
    for subject, obj, predicate in relations:
        one, other = ended.get(subject), ended.get(obj)
        if one is None or other is None or one == other:
            continue
        if entities[one]["status"] != "confirmed" or entities[other]["status"] != "confirmed":
            continue
        pairs.add(frozenset((one, other)))
        if predicate == COMPANION_PREDICATE:
            for mine, theirs in ((one, other), (other, one)):
                if _is_player(entities[theirs]) and not _is_player(entities[mine]):
                    companions.add(mine)
    count: dict[str, int] = {}
    for pair in pairs:
        for entity_id in pair:
            count[entity_id] = count.get(entity_id, 0) + 1
    return count, companions


def newest_by_group(
    ended: Mapping[str, str | None], newest: Mapping[str, tuple[int, int]]
) -> dict[str, tuple[int, int]]:
    """Each live entry's newest assessment across its merged group, as (seq, rank). `newest`: the
    latest assessment of each entry by itself, as (seq, rank). Pure."""
    group: dict[str, tuple[int, int]] = {}
    for entity_id, at in ended.items():
        if (
            at is not None
            and entity_id in newest
            and newest[entity_id][0] > group.get(at, (0, 0))[0]
        ):
            group[at] = newest[entity_id]
    return group


def ranks_from(
    entities: Mapping[str, Mapping[str, object]],
    ended: Mapping[str, str | None],
    group: Mapping[str, tuple[int, int]],
    heard: Mapping[str, set[int]],
    links: Mapping[str, int],
    companions: Collection[str],
    private: Collection[str] = (),
) -> dict[str, int]:
    """The current rank of every live entry from the counted facts (entry id -> 0 to 5). `group`:
    the newest assessment of each live entry's merged group as (seq, rank); `heard`: the session
    times each entry was heard in; `private`: the entries said in a kept private scene. A merged
    name's sessions and scenes join the survivor's. Pure, so the
    website's door (which hands over only these inputs) and the bot's reader cannot disagree."""
    sessions: dict[str, set[int]] = {}
    for entity_id, at in ended.items():
        if at is not None:
            sessions.setdefault(at, set()).update(heard.get(entity_id, ()))
    in_scene = {ended[e] for e in private if ended.get(e) is not None}
    out: dict[str, int] = {}
    for entity_id, row in entities.items():
        if row["status"] == "merged":
            continue  # (a merged name has no rank of its own)
        latest = group.get(entity_id)
        out[entity_id] = effective(
            floor(
                _is_player(row),
                len(sessions.get(entity_id, ())),
                links.get(entity_id, 0),
                entity_id in companions,
                entity_id in in_scene,
            ),
            latest[1] if latest else None,
        )
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
        """The campaign's entries of every kind but the table's people (live and merged), who each
        ended up as, and for each live entry the newest assessment of its whole merged group as
        (seq, rank)."""
        cur = await conn.execute(
            "SELECT id, type, role, status, merged_into FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND type <> 'person'"
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
        group = newest_by_group(ended, newest)
        return entities, ended, group

    async def add(self, guild_id: int, campaign_id: str, assessments: Iterable[Assessment]) -> int:
        """Append these assessments and return how many were written. One that is not valid, names
        an entry the campaign does not have (or one that is not live), or says the rank the
        entry's whole merged group is already assessed at is skipped, so a re-run
        writes nothing twice (a repeat with another reason adds no row: the rank is what
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
                    continue  # (not a live entry of this campaign)
                if group.get(a.entity_id, (0, -1))[1] == a.rank:
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, reason,"
                    " source, session_started_at, lines, recorded_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, campaign_id, a.entity_id, a.rank, a.reason, a.source,
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
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        entities: Mapping[str, Mapping[str, object]],
        ended: Mapping[str, str | None],
    ) -> tuple[dict[str, int], set[str]]:
        """The campaign's current confirmed connections, counted by `links_from`."""
        cur = await conn.execute(
            "SELECT subject_id, object_id, predicate FROM memory_relations"
            " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
            " AND to_session_at IS NULL AND predicate <> 'plays' AND source <> 'party'",
            (guild_id, campaign_id),
        )
        rows = [
            (str(r["subject_id"]), str(r["object_id"]), str(r["predicate"]))
            for r in await cur.fetchall()
        ]
        return links_from(rows, entities, ended)

    async def ranks(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        """The current rank of every live entry of the campaign (entry id -> 0 to 5).

        A merged name counts for the entry it ended up in, however many merges that took:
        its sessions join the survivor's for the floor, and the newest assessment of the whole
        group is the current one (by `seq`, so a correction after a merge still lowers the
        rank)."""
        async with self._db.guild(guild_id) as conn:
            ranked, _ended = await self._ranks_in(conn, guild_id, campaign_id)
        return ranked

    async def _ranks_in(
        self, conn: Conn, guild_id: int, campaign_id: str
    ) -> tuple[dict[str, int], dict[str, str | None]]:
        entities, ended, group = await self._state(conn, guild_id, campaign_id)
        cur = await conn.execute(
            "SELECT entity_id, array_agg(DISTINCT session_started_at) AS sessions"
            " FROM memory_heard WHERE guild_id = %s AND campaign_id = %s GROUP BY entity_id",
            (guild_id, campaign_id),
        )
        heard = {str(r["entity_id"]): set(r["sessions"]) for r in await cur.fetchall()}
        links, companions = await self._links(conn, guild_id, campaign_id, entities, ended)
        from dmbot.memory.private_scene_store import entries as private_entries

        private = await private_entries(conn, guild_id, campaign_id)
        return ranks_from(entities, ended, group, heard, links, companions, private), ended

    async def by_name(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        """name key -> rank, for the name and every confirmed other name of each confirmed entry,
        so a recap can rank the names a beat lists (slice 4). Confirmed only, as the connection
        score reads names (`importance.by_name`), so a misheard name the DM never confirmed picks
        no beat. An other name of a merged entry counts for the one it ended up in. One snapshot,
        so a merge between the reads cannot pair a name with the wrong rank."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            ranked, ended = await self._ranks_in(conn, guild_id, campaign_id)
            if not ranked:
                return {}
            cur = await conn.execute(
                "SELECT id AS entry, name AS text FROM memory_entities WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'confirmed'"
                " UNION ALL SELECT entity_id AS entry, text FROM memory_aliases WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'confirmed'",
                (guild_id, campaign_id, guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return names_ranked(
            ((ended.get(str(r["entry"])) or str(r["entry"]), str(r["text"])) for r in rows),
            ranked,
        )


def names_ranked(names: Iterable[tuple[str, str]], ranks: Mapping[str, int]) -> dict[str, int]:
    """name key -> rank from (entry id, name) pairs and the entries' ranks; a name of an entry
    with no rank is left out, and a name two entries share takes the higher. Pure."""
    out: dict[str, int] = {}
    for entry, text in names:
        rank = ranks.get(entry)
        key = name_key(text)
        if rank is None or not key:
            continue
        out[key] = max(out.get(key, BACKGROUND), rank)
    return out


def beat_rank(who: Iterable[str], ranks: Mapping[str, int]) -> tuple[int, ...]:
    """How much a beat's names matter, for sorting beats (slice 4): the ranks of its names,
    highest first, so a beat with a Major entry outranks one whose best is Significant, and between
    two whose best is equal the one with more that matter wins. Player characters are left out:
    they are in nearly every beat, so they would make every beat equal. Background names (0) are
    left out too: every known name is at least Background, so counting them would let a beat with
    more names outrank the connection score, which breaks ties (Supervisor's ruling). A name with no
    rank adds nothing. Empty with no table."""
    if not ranks:
        return ()
    found = (ranks.get(name_key(w)) for w in dict.fromkeys(who))
    return tuple(
        sorted((r for r in found if r is not None and BACKGROUND < r < PLAYER), reverse=True)
    )


def counts(ranks: Mapping[str, int]) -> list[int]:
    """How many entries are at each rank, 0 to 5 (for a log line: counts only)."""
    out = [0] * (PLAYER + 1)
    for rank in ranks.values():
        out[max(BACKGROUND, min(PLAYER, rank))] += 1
    return out
