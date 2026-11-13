"""Where the story is, kept from session to session (`memory_story_chain`, migration 0464; #4173
slice 4). After a session or an import, `StoryChains.read_session` walks the session's own lines
(`session_chain.walk`) from the chains the last session left, keeps the chains it ends with, and
queues each new place's assumed parent the slice 3 way: a waiting link with the mark, never a
question, kept by itself once both places are kept (`backstory_keep.keep_assumed`).

- **Whose chain a line is.** The party's (`MAIN`) while it is together. While split (the game
  clock, `game_clock_log`): a player's line is their group's (`timebot.stamp`'s rule, by the
  characters they play); a DM's line is the group whose clock the DM last moved, the only record of
  the group the DM picked that a past or imported session has; anything else is nobody's and
  skipped (Supervisor's ruling, 2026-11-13 04:14: never guessed). A group's chain is keyed by its
  split's start, so a position a later split reuses never inherits it. At Rejoin the party keeps
  only the places every group still shares (`session_chain.rejoined`).
- **Order.** A session older than the chains' last one never moves them (an import of an old file
  after newer play): it is left out, counted in the log.
- **Ids only.** The lines are read in memory and never kept; the table holds entry ids, and the log
  gets counts.
"""

from __future__ import annotations

import bisect
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dmbot.db import Database
from dmbot.memory import route_words
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError
from dmbot.memory.scene_moves import Move
from dmbot.memory.session_chain import MAIN, Leg, Line, rejoined, walk
from dmbot.memory.story_chain import Place

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

MARK = "mark"  # the one row that says when the last session was walked
TOWN = "town"  # the party's last town, kept for the next session's first journey (#4338)
MAX_RANK = 20  # the table's own limit: a chain is at most this deep (a plane down to a room is 8)


@dataclass(frozen=True, slots=True)
class Said:
    """One line the reader may read (the caller applied consent and left out the sidebar, the
    off-topic lines and last time's recap): when it began (Unix ms), who said it, its words."""

    started_ms: int
    user_id: int
    text: str
    number: int = 0  # its number in the session (as the story extractor numbers it)


@dataclass(frozen=True, slots=True)
class Result:
    guessed: int = 0  # assumed parents queued (new ones only)
    skipped_older: bool = False  # an older session than the chains': left out
    journeys: int = 0  # the party's journeys written (#4173 slice 6d part 3)
    journeys_unkept: int = 0  # journeys left out: an end the DM hasn't kept yet
    journeys_refused: int = 0  # journeys the store refused (a place changed meanwhile)


class _Clock:
    """The game clock's log, oldest first: each row's time and group position (None: the main
    clock, the party together), and each player character's placements."""

    def __init__(
        self,
        rows: Sequence[tuple[int, int | None]],
        placed: dict[str, list[tuple[int, int | None]]],
    ) -> None:
        self._at = [at for at, _ in rows]
        self._pos = [pos for _, pos in rows]
        self._placed = {c: ([a for a, _ in r], [p for _, p in r]) for c, r in placed.items()}

    def state(self, at: int) -> tuple[int | None, int | None]:
        """(the split's start, the group whose clock the DM moved last) at this moment, or (None,
        None) while the party is together. At the split itself every group's clock is written in
        the same second (migration 0305's triggers): that is no group picked, so it is None until
        one group's clock moves on its own (the reviewer on slice 4)."""
        i = bisect.bisect_right(self._at, at) - 1
        if i < 0 or self._pos[i] is None:
            return None, None
        start = i
        while start > 0 and self._pos[start - 1] is not None:
            start -= 1
        picked = self._pos[i] if self._at[i] > self._at[start] else None
        return self._at[start], picked

    def groups_at(self, split_start: int) -> set[int]:
        """The group positions the split began with."""
        return {
            p
            for a, p in zip(self._at, self._pos, strict=True)
            if a == split_start and p is not None
        }

    def group(self, character_id: str, at: int) -> int | None:
        rows = self._placed.get(character_id)
        if rows is None:
            return None
        i = bisect.bisect_right(rows[0], at) - 1
        return rows[1][i] if i >= 0 else None


def chain_key(split_start: int, position: int) -> str:
    return f"s{split_start}g{position}"


def lines_with_chains(
    said: Sequence[Said],
    dm_ids: Collection[int],
    clock: _Clock,
    plays: dict[int, list[str]],
    last_split: int | None = None,
    moves: Mapping[int, tuple[Move, ...]] | None = None,
) -> list[tuple[Line | None, int | None]]:
    """Each line with its chain, and the split it ended (for Rejoin): (line, split start that
    ended just before it, or None). `last_split`: the split the stored chains were left in, when
    the last session ended with the party apart (a Rejoin between sessions is seen at the first
    line)."""
    out: list[tuple[Line | None, int | None]] = []
    for s in said:
        at = s.started_ms // 1000
        split_start, picked = clock.state(at)
        ended = last_split if last_split is not None and split_start != last_split else None
        last_split = split_start
        dm = s.user_id in dm_ids
        if split_start is None:
            key: str | None = MAIN
        elif dm:
            key = chain_key(split_start, picked) if picked is not None else None
        else:
            groups = {g for c in plays.get(s.user_id, []) if (g := clock.group(c, at)) is not None}
            key = chain_key(split_start, groups.pop()) if len(groups) == 1 else None
        found = (moves or {}).get(s.number, ()) if dm else ()
        out.append((Line(s.text, dm, key, found), ended))
    return out


def readable_lines(
    lines: Sequence[Any],
    recap: Collection[int],
    agreed: Collection[int],
) -> list[Said]:
    """The lines the walk may read, exactly as the story extractor reads them: `extractor.readable`
    (no sidebar, no off-topic line), last time's recap read aloud left out, and only people who
    agree now, the DM too (a DM who stopped recording is not read either)."""
    from dmbot.memory import extractor

    return [
        Said(s.started_ms, s.user_id, s.text, s.number)
        for s in extractor.readable(lines)
        if s.number not in recap and s.user_id in agreed
    ]


class StoryChains:
    def __init__(
        self, db: Database, links: BackstoryLinks, memory: MemoryStore | None = None
    ) -> None:
        self._db = db
        self._links = links
        self._memory = memory  # (writes the party's journeys; None writes none)

    async def read_session(
        self,
        guild_id: int,
        campaign_id: str,
        session_started_at: int,
        said: Sequence[Said],
        dm_ids: Collection[int],
        label: str,
        agreed: Callable[[], Awaitable[Collection[int]]],
        moves: Mapping[int, tuple[Move, ...]] | None = None,
    ) -> Result:
        """Walk one session from the chains as they were, keep the chains it ends with, and queue
        its assumed parents, all under the campaign's own lock, so two walks (a session's end and
        an import's) never give one place two guesses. `said`: lines of people who agreed (the
        DM's included only if the DM agreed); `agreed()` is asked again before anything is kept,
        and a line of someone who stopped meanwhile is taken out and the walk made again."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"dmbot.story_chain:{guild_id}:{campaign_id}",),
            )
            chains, last, town = await _load(conn, guild_id, campaign_id)
            if last is not None and session_started_at < last:
                return Result(0, skipped_older=True)
            found = await _places(conn, guild_id, campaign_id)
            routes = await _routes(conn, guild_id, campaign_id)
            stated = await _stated(conn, guild_id, campaign_id)
            until = (said[-1].started_ms // 1000) if said else session_started_at
            clock = await _clock(conn, guild_id, campaign_id, until)
            plays = await _plays(conn, guild_id, campaign_id)
            walked, pairs, legs, town_after = _walk_all(
                said, dm_ids, clock, plays, chains, found, stated, moves, routes, town
            )
            still = set(await agreed())  # (consent again, after the reads: #4153's rule)
            if any(s.user_id not in still for s in said):
                said = [s for s in said if s.user_id in still]
                walked, pairs, legs, town_after = _walk_all(
                    said, dm_ids, clock, plays, chains, found, stated, moves, routes, town
                )
            await _save(conn, guild_id, campaign_id, walked, session_started_at, town_after)
            added = 0
            if pairs:  # (inside the lock: the next walk sees them as waiting)
                added = await self._links.add(
                    guild_id,
                    campaign_id,
                    [NewLink(a, "located_in", b, "", upload=label, assumed=True) for a, b in pairs],
                )
            if last == session_started_at:
                legs = []  # (the same session walked again: its journeys are written already)
            ends = {e for leg in legs for e in (leg.start, leg.end)}
            kept = await _kept(conn, guild_id, campaign_id, ends)
        # (consent again before anything is written from the DM's lines: a DM who stopped
        # meanwhile takes the journeys with them)
        speakers = {s.user_id for s in said if s.user_id in dm_ids}
        if legs and not speakers <= set(await agreed()):
            legs = []
        written, unkept, refused = await self._journeys(
            guild_id, campaign_id, session_started_at, legs, kept
        )
        return Result(added, journeys=written, journeys_unkept=unkept, journeys_refused=refused)

    async def _journeys(
        self, guild_id: int, campaign_id: str, at: int, legs: Sequence[Leg], kept: set[str]
    ) -> tuple[int, int, int]:
        """The party's journeys (Supervisor's ruling on part 3, 2026-11-13): written straight away
        as the DM's own travelled routes, never queued, since only the DM's narration moves the
        party's chain; each its own change, so one Undo takes it back. Only between two places the
        DM has kept (one still proposed is left out and counted). After the chain's lock, so the
        memory store's own write never waits on it. A journey said twice in one session is one
        (`checks.duplicate_of`: one per pair, means and session). Written as the DM's (source
        "dm", as slice 3's assumed parents are): the ruling counts the DM's narration as the DM's
        word, so the change log names the DM. One the store refuses (a place rejected meanwhile)
        is counted and the rest go on."""
        if self._memory is None:
            return 0, 0, 0
        written = unkept = refused = 0
        for leg in legs:
            if not {leg.start, leg.end} <= kept:
                unkept += 1
                continue
            try:
                await self._memory.add_relation(
                    guild_id, campaign_id, leg.start, route_words.ROUTE_TO, leg.end, source=DM,
                    confidence=1.0, status=CONFIRMED, detail=leg.means,
                    route_state=route_words.TRAVELLED, from_session_at=at,
                )  # fmt: skip
            except MemoryRuleError:
                refused += 1
                continue
            written += 1
        return written, unkept, refused


def _walk_all(
    said: Sequence[Said],
    dm_ids: Collection[int],
    clock: _Clock,
    plays: dict[int, list[str]],
    chains: dict[str, list[str]],
    found: Sequence[Place],
    stated: Collection[str],
    moves: Mapping[int, tuple[Move, ...]] | None = None,
    routes: Sequence[Place] = (),
    town: str | None = None,
) -> tuple[dict[str, list[str]], list[tuple[str, str]], list[Leg], str | None]:
    """The whole session, Rejoins included: the chains it ends with, the guesses it made, the
    party's journeys and the town it was last in. `town`: the one the last session left (#4338),
    so a session that starts on the road writes its first arrival as a journey."""
    given = set(stated)
    pairs: list[tuple[str, str]] = []
    legs: list[Leg] = []
    batch: list[Line] = []
    left_in = max((int(k[1:].split("g")[0]) for k in chains if k != MAIN), default=None)
    for line, ended in lines_with_chains(said, dm_ids, clock, plays, left_in, moves):
        if ended is not None:  # (Rejoin: walk what came before, then join the groups)
            walked = walk(batch, found, chains, given, routes, town)
            chains, batch, town = walked.chains, [], walked.town
            pairs += walked.pairs
            legs += walked.legs
            given |= {p for p, _ in walked.pairs}
            prefix = f"s{ended}g"
            groups = {k: v for k, v in chains.items() if k.startswith(prefix)}
            # (a group the DM never moved is still where the split began: the party's chain as
            # it was; only those count it, so two groups that both reached a town keep it)
            began = clock.groups_at(ended)
            start = chains.get(MAIN, [])
            idle = [start for pos in began if chain_key(ended, pos) not in groups]
            if groups:
                chains = {k: v for k, v in chains.items() if not k.startswith(prefix)}
                chains[MAIN] = rejoined([*groups.values(), *idle])
        if line is not None:
            batch.append(line)
    walked = walk(batch, found, chains, given, routes, town)
    return walked.chains, pairs + walked.pairs, legs + walked.legs, walked.town


async def _load(
    conn: Any, guild_id: int, campaign_id: str
) -> tuple[dict[str, list[str]], int | None, str | None]:
    """The chains, with places that are gone or turned down left out, when the last session
    was walked (the 'mark' row, so it holds even when every chain ended empty), and the party's
    last town (the 'town' row, left out the same way when its place is gone)."""
    cur = await conn.execute(
        "SELECT max(updated_at) AS last FROM memory_story_chain"
        " WHERE guild_id = %s AND campaign_id = %s",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    last = int(row["last"]) if row and row["last"] is not None else None
    cur = await conn.execute(
        "SELECT chain, entity_id FROM memory_story_chain c JOIN memory_entities e"
        " ON (e.guild_id, e.campaign_id, e.id) = (c.guild_id, c.campaign_id, c.entity_id)"
        " WHERE c.guild_id = %s AND c.campaign_id = %s AND e.status IN ('confirmed', 'proposed')"
        " ORDER BY chain, rank",
        (guild_id, campaign_id),
    )
    chains: dict[str, list[str]] = {}
    town: str | None = None
    for r in await cur.fetchall():
        if r["chain"] == TOWN:
            town = str(r["entity_id"])
        else:
            chains.setdefault(str(r["chain"]), []).append(str(r["entity_id"]))
    return chains, last, town


async def _save(
    conn: Any,
    guild_id: int,
    campaign_id: str,
    chains: dict[str, list[str]],
    at: int,
    town: str | None = None,
) -> None:
    await conn.execute(
        "DELETE FROM memory_story_chain WHERE guild_id = %s AND campaign_id = %s",
        (guild_id, campaign_id),
    )
    rows: list[tuple[int, str, str, int, str | None, int]] = [
        (guild_id, campaign_id, MARK, 0, None, max(at, 1))
    ]
    if town is not None:
        rows.append((guild_id, campaign_id, TOWN, 0, town, max(at, 1)))
    for key, ids in chains.items():
        rows += [
            (guild_id, campaign_id, key, rank, entity_id, max(at, 1))
            for rank, entity_id in enumerate(ids[: MAX_RANK + 1])
        ]
    async with conn.cursor() as cursor:
        await cursor.executemany(
            "INSERT INTO memory_story_chain (guild_id, campaign_id, chain, rank, entity_id,"
            " updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )


async def _places(conn: Any, guild_id: int, campaign_id: str) -> list[Place]:
    """The campaign's places with a known size that are areas (not water or a route), each with its
    name and its other names: proposed ones too, so a guess can wait for its names (slice 3)."""
    cur = await conn.execute(
        "SELECT e.id, e.name, e.place_level, COALESCE(array_agg(a.text)"
        " FILTER (WHERE a.text IS NOT NULL), '{}') AS others FROM memory_entities e"
        " LEFT JOIN memory_aliases a ON (a.guild_id, a.campaign_id, a.entity_id)"
        " = (e.guild_id, e.campaign_id, e.id) AND a.status <> 'rejected'"
        " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.type = 'place'"
        " AND e.status IN ('confirmed', 'proposed') AND e.place_level IS NOT NULL"
        " AND e.form IS NULL GROUP BY e.id, e.name, e.place_level",
        (guild_id, campaign_id),
    )
    return [
        Place(str(r["id"]), str(r["place_level"]), (str(r["name"]), *map(str, r["others"])))
        for r in await cur.fetchall()
    ]


async def _routes(conn: Any, guild_id: int, campaign_id: str) -> list[Place]:
    """The campaign's named route places ("the Eastway"), each with its spellings: being on one
    closes the town level (#4173 slice 6d). Kept ones only: a road the DM hasn't kept moves
    nothing."""
    cur = await conn.execute(
        "SELECT e.id, e.name, COALESCE(array_agg(a.text)"
        " FILTER (WHERE a.text IS NOT NULL), '{}') AS others FROM memory_entities e"
        " LEFT JOIN memory_aliases a ON (a.guild_id, a.campaign_id, a.entity_id)"
        " = (e.guild_id, e.campaign_id, e.id) AND a.status <> 'rejected'"
        " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.type = 'place'"
        " AND e.status = 'confirmed' AND e.form = 'route' GROUP BY e.id, e.name",
        (guild_id, campaign_id),
    )
    return [
        Place(str(r["id"]), "", (str(r["name"]), *map(str, r["others"])))
        for r in await cur.fetchall()
    ]


async def _kept(conn: Any, guild_id: int, campaign_id: str, ids: set[str]) -> set[str]:
    """Which of these places the DM has kept (a journey is written only between two of them)."""
    if not ids:
        return set()
    cur = await conn.execute(
        "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND id = ANY(%s) AND status = 'confirmed'",
        (guild_id, campaign_id, sorted(ids)),
    )
    return {str(r["id"]) for r in await cur.fetchall()}


async def _stated(conn: Any, guild_id: int, campaign_id: str) -> set[str]:
    """The places with a parent place already: live (said or assumed) or waiting (either kind)."""
    cur = await conn.execute(
        "SELECT r.subject_id AS id FROM memory_relations r JOIN memory_entities o"
        " ON (o.guild_id, o.campaign_id, o.id) = (r.guild_id, r.campaign_id, r.object_id)"
        " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.predicate = 'located_in'"
        " AND r.status <> 'rejected' AND r.to_session_at IS NULL AND o.type = 'place'"
        " UNION SELECT l.subject_id FROM backstory_links l JOIN memory_entities o"
        " ON (o.guild_id, o.campaign_id, o.id) = (l.guild_id, l.campaign_id, l.object_id)"
        " WHERE l.guild_id = %s AND l.campaign_id = %s AND l.predicate = 'located_in'"
        " AND o.type = 'place'",
        (guild_id, campaign_id, guild_id, campaign_id),
    )
    return {str(r["id"]) for r in await cur.fetchall()}


async def _clock(conn: Any, guild_id: int, campaign_id: str, until: int) -> _Clock:
    cur = await conn.execute(
        "SELECT at, position FROM game_clock_log WHERE guild_id = %s AND campaign_id = %s"
        " AND at <= %s ORDER BY at, seq",
        (guild_id, campaign_id, until),
    )
    rows = [
        (int(r["at"]), None if r["position"] is None else int(r["position"]))
        for r in await cur.fetchall()
    ]
    cur = await conn.execute(
        "SELECT character_id, at, position FROM game_clock_group_log WHERE guild_id = %s"
        " AND campaign_id = %s AND at <= %s ORDER BY at, seq",
        (guild_id, campaign_id, until),
    )
    placed: dict[str, list[tuple[int, int | None]]] = {}
    for r in await cur.fetchall():
        position = None if r["position"] is None else int(r["position"])
        placed.setdefault(str(r["character_id"]), []).append((int(r["at"]), position))
    return _Clock(rows, placed)


async def _plays(conn: Any, guild_id: int, campaign_id: str) -> dict[int, list[str]]:
    """Each player's confirmed player characters (who plays them, as `timebot.stamp` reads it)."""
    cur = await conn.execute(
        "SELECT id, played_by FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND type = 'character' AND role = 'player_character' AND status = 'confirmed'"
        " AND played_by IS NOT NULL",
        (guild_id, campaign_id),
    )
    out: dict[int, list[str]] = {}
    for r in await cur.fetchall():
        out.setdefault(int(r["played_by"]), []).append(str(r["id"]))
    return out


async def stored_vectors(
    db: Database, guild_id: int, session_id: str, model: str, user_ids: Collection[int]
) -> dict[tuple[int, int], list[float]]:
    """The numbers PlotBot's search already keeps for these people's lines in one session (one per
    line: a long line's first part), by (started_ms, user_id). Nothing is sent anywhere."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT ON (l.id) l.started_ms, l.user_id, v.vector FROM transcript_lines l"
            " JOIN memory_vectors v ON v.guild_id = l.guild_id AND v.line_id = l.id"
            " AND v.owner_kind = 'line' AND v.model = %s"
            " WHERE l.guild_id = %s AND l.session_id = %s AND l.user_id = ANY(%s::bigint[])"
            " ORDER BY l.id, v.owner_id",
            (model, guild_id, session_id, list(user_ids)),
        )
        return {
            (int(r["started_ms"]), int(r["user_id"])): [float(x) for x in r["vector"]]
            for r in await cur.fetchall()
        }
