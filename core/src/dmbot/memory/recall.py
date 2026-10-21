"""The recall service (#1350, docs/STORY_MEMORY.md section 7): what DMbot may tell a helper
about the story, assembled in code so the AI only reads it.

Given some starting names (`seeds`), it walks one or two steps out over the facts the DM
**confirmed** that are true at the story time asked about, ranks them by how recent they are and
how often their names are heard, keeps what the audience may see, adds a few quoted lines as
evidence, and cuts the result to a size budget. Every item carries the id of the entry, fact or
transcript line it comes from, and `check_citations` confirms an answer cites only those.

Who may see what is decided here, before anything is returned (never by the prompt):

- `dm_screen`: everything, secrets included.
- `players`: no secret facts, no secret names, no clue only some people know, no entry notes
  (the DM's own description of an entry), no conditions (alive, dead) and nothing from the
  story plan.
- `transcript`: always empty. Nothing from here ever goes into a transcript.

Evidence is quoted only from speakers who are recorded *now*: the caller passes who that is (or
a function that says, asked again after the transcript was read), so a person who stops being
recorded vanishes from the next recall. Sidebar lines (the DM's questions to DMbot, a table
question, and DMbot's answers) are never quoted, and the caller's reader must say so for every
line it returns (there is no default).

Every read goes through one campaign (its server and its id are in every query, and row-level
security backs that up), so another campaign or server never shows up, even with the same names.
No AI, no embeddings and no graph database: a few indexed lookups per step, so one recall on a
campaign of thousands of entries takes milliseconds.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory._changes import Scope
from dmbot.memory.continuity import _predicate_words
from dmbot.memory.kinds import KIND_LABELS
from dmbot.memory.models import DM_ONLY_TYPES, ROLE_LABELS, name_key
from dmbot.memory.ontology import Ontology
from dmbot.memory.store import MemoryStore, _load_ontology

log = logging.getLogger(__name__)

DM_SCREEN, PLAYERS, TRANSCRIPT = "dm_screen", "players", "transcript"
AUDIENCES = (DM_SCREEN, PLAYERS, TRANSCRIPT)

DEFAULT_BUDGET = 4000  # characters; the backend setting DMBOT_RECALL_BUDGET overrides it
MAX_HOPS = 2
EVIDENCE_PER_FACT = 3
QUOTE_CHARS = 160
ITEM_OVERHEAD = 40  # an item's id and punctuation count against the budget too
SESSION_SECONDS = 30 * 86400  # a fact a month older than the newest scores half as recent
# What one recall will read, whatever the story holds: a hub with thousands of facts or a group
# with hundreds of members must not turn one question into a scan. The newest facts win, and
# `Recall.truncated` says when this was reached.
MAX_FACTS_ROUND = 100
MAX_FACTS_TOTAL = 200
MAX_FRONTIER = 60  # entries expanded in one round (those of the best-ranked facts)
MAX_ROUNDS = 5  # groups cost no step, so a walk can need a few rounds more than its steps
EVIDENCE_SHARE = 4  # a quarter of the budget is kept for evidence when it can be had
QUOTED_FACTS = 10  # evidence is looked up for this many of the best facts
LINES_TIMEOUT_S = 2.0  # a hung transcript read costs the quotes, never the recall
# A group or a thread holds its members together: walking through one costs no extra step.
CLUSTER_TYPES = frozenset({"faction", "thread"})
# The story plan is a separate layer (docs/STORY_MEMORY.md section 6) that does not exist as
# stored facts yet. Anything that says it comes from it is held back from the players, so
# turning the layer on later cannot leak the book.
PLAN_SOURCES = frozenset({"plan", "story_plan"})
_ID = re.compile(r"^[0-9a-f]{32}$")
_LIVE = ("proposed", "confirmed")

ENTRY, FACT = "entry", "fact"

# The roster (a list of the people and groups the table may know, #1761): who is in a group, and
# who is closely tied to whom.
ROSTER_MAX = 1000  # entries read at most; the channel shows far fewer
ROSTER_TIES_MAX = 5  # shown for each entry, then "+N more"
ROSTER_GROUP_LINK = "member_of"
ROSTER_TIES = ("kin_of", "ally_of", "enemy_of", "serves", "sworn_to")
ROSTER_NAME_KINDS = ("full", "short", "nickname", "title")  # never "misheard": a matching aid


@dataclass(frozen=True, slots=True)
class AsOf:
    """The story time facts must be true at: which session (its start, Unix seconds) and, once
    TimeBot has it, the game time. Neither: the story as it stands now."""

    session_at: int | None = None
    game_time: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One transcript line, as the caller's reader gives it. `sidebar` has no default on
    purpose: the reader must say whether this is one of the DM's questions to DMbot, a table
    question or DMbot's answer, none of which is ever quoted."""

    ref: str
    text: str
    speaker_id: int | None
    sidebar: bool
    session_started_at: int | None = None
    at_ms: int | None = None


# Reads transcript lines of ONE campaign by the references facts and claims cite. The caller
# owns the transcript, so recall does not reach into it; it names the server and campaign so a
# reference that exists in two of them can never come back from the wrong one. A missing line
# is simply not quoted.
Lines = Callable[[int, str, Sequence[str]], Awaitable[Mapping[str, Line]]]
# Who is recorded now: a fixed set, or a function asked again after the transcript was read.
OptedIn = Collection[int] | Callable[[], Awaitable[Collection[int]]]


async def _agreed(opted_in: OptedIn) -> frozenset[int]:
    if callable(opted_in):
        return frozenset(await opted_in())
    return frozenset(opted_in)


@dataclass(frozen=True, slots=True)
class Quote:
    line_ref: str  # the id an answer may cite
    text: str
    session_started_at: int | None
    at_ms: int | None


@dataclass(frozen=True, slots=True)
class Item:
    kind: str  # ENTRY or FACT
    id: str  # the entry's or the fact's id: what an answer cites
    text: str  # plain words, ready to read
    score: float
    hops: int  # steps from a seed: 0 is a seed itself
    secret: bool = False  # only ever True for the DM screen
    quotes: tuple[Quote, ...] = ()

    @property
    def size(self) -> int:
        return ITEM_OVERHEAD + len(self.text) + sum(len(q.text) + 20 for q in self.quotes)


@dataclass(frozen=True, slots=True)
class Recall:
    audience: str
    as_of: AsOf
    items: tuple[Item, ...] = ()
    seeds: tuple[str, ...] = ()  # the entries the seeds named, in the order given
    cut: int = 0  # items left out to stay in the budget
    size: int = 0
    truncated: bool = False  # the walk hit its limits (MAX_FACTS_*, MAX_ROUNDS) and stopped

    @property
    def ids(self) -> frozenset[str]:
        """Every id an answer may cite: the entries and facts and the quoted lines."""
        found = {i.id for i in self.items}
        found.update(q.line_ref for i in self.items for q in i.quotes)
        return frozenset(found)


def check_citations(answer_ids: Iterable[str], recall: Recall) -> tuple[str, ...]:
    """The ids an answer cited that it was not given (empty: it cited only what it was given),
    in the order cited, each once."""
    given = recall.ids
    out: dict[str, None] = {}
    for cited in answer_ids:
        if cited not in given:
            out[cited] = None
    return tuple(out)


def _true_at(as_of: AsOf) -> tuple[str, list[int]]:
    """The SQL that keeps a fact only if it holds at `as_of`, and its values. A fact that has
    ended by session is not current, whatever the game time asked about."""
    clauses, values = [], []
    if as_of.session_at is None:
        clauses.append("to_session_at IS NULL")
    else:
        clauses.append("(from_session_at IS NULL OR from_session_at <= %s)")
        clauses.append("(to_session_at IS NULL OR to_session_at > %s)")
        values += [as_of.session_at, as_of.session_at]
    if as_of.game_time is not None:
        clauses.append("(from_game_time IS NULL OR from_game_time <= %s)")
        clauses.append("(to_game_time IS NULL OR to_game_time > %s)")
        values += [as_of.game_time, as_of.game_time]
    return " AND ".join(clauses), values


def _recency(from_at: int | None, reference: int | None) -> float:
    """1.0 for the newest, easing toward 0 for the old; a fact with no time is middling."""
    if from_at is None or reference is None:
        return 0.5
    return 1.0 / (1.0 + max(0, reference - from_at) / SESSION_SECONDS)


@dataclass(frozen=True, slots=True)
class _Fact:
    id: str
    subject_id: str
    object_id: str
    predicate: str
    detail: str
    from_at: int | None
    secret: bool
    mention_ids: tuple[str, ...]
    hops: int
    near: str  # the end already reached when this fact was found
    far: str


def _fact_of(r: Mapping[str, Any], hops: int, near: str, far: str) -> _Fact:
    return _Fact(
        str(r["id"]), str(r["subject_id"]), str(r["object_id"]), str(r["predicate"]),
        str(r["detail"]), r["from_session_at"], bool(r["secret"]),
        tuple(str(m) for m in r["mention_ids"] or ()), hops, near, far,
    )  # fmt: skip


@dataclass(slots=True)
class _Walk:
    seeds: list[str] = field(default_factory=list)
    entities: dict[str, dict[str, object]] = field(default_factory=dict)
    cost: dict[str, int] = field(default_factory=dict)  # steps from a seed, for reached entries
    facts: dict[str, _Fact] = field(default_factory=dict)
    truncated: bool = False

    def is_cluster(self, entity_id: str) -> bool:
        return self.entities[entity_id]["type"] in CLUSTER_TYPES


Sources = dict[str, list[tuple[str, int | None, tuple[int, ...] | None]]]


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One confirmed NPC or group as the players may know it. No description (the players'
    audience never reads the DM's note), no condition, no standing."""

    id: str
    name: str
    kind: str  # "NPC", "god", "group", ...
    is_group: bool
    also_called: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()  # the groups it is in (names)
    groups_more: int = 0
    ties: tuple[str, ...] = ()  # closely tied (names)
    ties_more: int = 0


class RecallService:
    """Assembles the context for one question. One campaign at a time."""

    def __init__(self, memory: MemoryStore, lines: Lines | None = None) -> None:
        self._memory = memory
        self._lines = lines
        # The campaign's terms, kept until its memory next changes (every write bumps the
        # version the read scope reports): two fewer round trips on a busy table.
        self._ontologies: dict[tuple[int, str], tuple[int, Ontology]] = {}

    async def recall(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        seeds: Sequence[str],
        audience: str,
        as_of: AsOf | None = None,
        budget: int = DEFAULT_BUDGET,
        opted_in: OptedIn = (),
        hops: int = MAX_HOPS,
        confirmed_only: bool = False,
    ) -> Recall:
        """What may be said about `seeds` (entry ids or names) to `audience` at `as_of`, cut to
        `budget` characters. `opted_in` are the people recorded now (or a function that says,
        asked again before any quote is attached): only they are quoted or counted.
        `confirmed_only`: for the DM screen, leave out the entries the DM has not confirmed yet
        (a proposed one), as the players' view does; a recap rests on confirmed memory alone."""
        if audience not in AUDIENCES:
            raise ValueError(f"unknown audience {audience!r}")
        as_of = as_of or AsOf()
        if audience == TRANSCRIPT:  # decided before anything is read
            return Recall(audience, as_of)
        hops = max(0, min(hops, MAX_HOPS))
        agreed = await _agreed(opted_in)
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            walk = await self._walk(scope, seeds, audience, as_of, hops, confirmed_only)
            reached = sorted(walk.cost)
            heard = await self._heard(scope, reached, agreed)
            notes = await self._conditions(scope, reached, as_of) if audience == DM_SCREEN else {}
            onto = await self._ontology(scope)
            found = _Found(walk, heard, notes, onto, as_of, audience)
            # Rank and cut first: the items come before their evidence, and evidence is looked
            # up only for the best few facts, so it can fill what is left but never crowd out
            # an item.
            reserve = budget // EVIDENCE_SHARE if self._lines is not None and agreed else 0
            first = _cut(audience, as_of, tuple(walk.seeds), found.items(), budget - reserve)
            wanted = [i.id for i in first.items if i.kind == FACT][:QUOTED_FACTS]
            sources = (
                await self._sources(scope, [walk.facts[f] for f in wanted])
                if self._lines is not None and agreed and wanted
                else {}
            )
        quotes: dict[str, tuple[Quote, ...]] = {}
        if sources and self._lines is not None:
            quotes = await self._quotes(guild_id, campaign_id, sources, agreed, opted_in)
        items, size = _fill(first.items, quotes, budget - first.size)
        return Recall(
            audience, as_of, items, first.seeds, first.cut, first.size + size, walk.truncated
        )

    async def _ontology(self, scope: Scope) -> Ontology:
        key = scope.ids
        held = self._ontologies.get(key)
        if held is not None and held[0] == scope.version:
            return held[1]
        onto = await _load_ontology(scope)
        self._ontologies[key] = (scope.version, onto)
        return onto

    async def roster(self, guild_id: int, campaign_id: str) -> list[RosterEntry]:
        """The confirmed NPCs and groups the players may see, groups first, then by name. The
        same rules as the players' audience, not a second copy: `_entries` drops an entry that is
        not confirmed, whose own name is secret, a clue only some know, a story thread or a deed,
        and a relation counts only when it is confirmed, true now, not secret and not from the
        story plan. Never player characters or the party. Conditions (dead, missing) and standing
        are other tables and never read here."""
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await self._ontology(scope)
            kinds = [k for k in onto.types if onto.is_any(k, ("character", "faction"))]
            cur = await scope.conn.execute(
                "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed' AND type = ANY(%s) AND NOT party"
                " AND role IS DISTINCT FROM 'player_character' ORDER BY id",
                (*scope.ids, kinds),
            )
            candidates = [str(r["id"]) for r in await cur.fetchall()]
            shown = await self._entries(scope, candidates, PLAYERS)
            if len(shown) > ROSTER_MAX:  # (cut after the filter: hidden entries use no slot)
                shown = {i: shown[i] for i in sorted(shown)[:ROSTER_MAX]}
            if not shown:
                return []
            ids = sorted(shown)
            cur = await scope.conn.execute(
                "SELECT entity_id, text, kind FROM memory_aliases WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = ANY(%s) AND status = 'confirmed'"
                " AND NOT secret AND kind = ANY(%s) ORDER BY created_at, id",
                (*scope.ids, ids, list(ROSTER_NAME_KINDS)),
            )
            aliases: dict[str, list[str]] = {}
            for r in await cur.fetchall():
                aliases.setdefault(str(r["entity_id"]), []).append(str(r["text"]))
            cur = await scope.conn.execute(
                "SELECT subject_id, predicate, object_id, source FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
                " AND to_session_at IS NULL AND NOT secret AND predicate = ANY(%s)"
                " AND subject_id = ANY(%s) AND object_id = ANY(%s)"
                " ORDER BY from_session_at DESC NULLS LAST, id",
                (*scope.ids, [ROSTER_GROUP_LINK, *ROSTER_TIES], ids, ids),
            )
            relations = [r for r in await cur.fetchall() if str(r["source"]) not in PLAN_SOURCES]
        names = {i: str(e["name"]) for i, e in shown.items()}
        groups: dict[str, list[str]] = {}
        ties: dict[str, list[str]] = {}
        for r in relations:
            a, b, predicate = str(r["subject_id"]), str(r["object_id"]), str(r["predicate"])
            if a == b:
                continue
            if predicate == ROSTER_GROUP_LINK:
                if onto.is_a(str(shown[b]["type"]), "faction"):
                    groups.setdefault(a, []).append(names[b])
            else:
                ties.setdefault(a, []).append(names[b])
                ties.setdefault(b, []).append(names[a])
        out: list[RosterEntry] = []
        for entity_id, e in shown.items():
            kind_key = str(e["type"])
            is_group = onto.is_a(kind_key, "faction")
            role = e.get("role")
            kind = (
                "group" if is_group
                else ROLE_LABELS.get(str(role), "") if role else ""
            ) or onto.types[kind_key].label  # fmt: skip
            own = names[entity_id]
            others = [t for t in dict.fromkeys(aliases.get(entity_id, [])) if t != own]
            g = list(dict.fromkeys(groups.get(entity_id, [])))
            t = list(dict.fromkeys(ties.get(entity_id, [])))
            out.append(
                RosterEntry(
                    id=entity_id,
                    name=own,
                    kind=kind,
                    is_group=is_group,
                    also_called=tuple(others),
                    groups=tuple(g[:ROSTER_TIES_MAX]),
                    groups_more=max(0, len(g) - ROSTER_TIES_MAX),
                    ties=tuple(t[:ROSTER_TIES_MAX]),
                    ties_more=max(0, len(t) - ROSTER_TIES_MAX),
                )
            )
        out.sort(key=lambda x: (not x.is_group, name_key(x.name), x.id))
        return out

    # ---- the walk --------------------------------------------------------------------

    async def _walk(
        self,
        scope: Scope,
        seeds: Sequence[str],
        audience: str,
        as_of: AsOf,
        hops: int,
        confirmed_only: bool = False,
    ) -> _Walk:
        walk = _Walk()
        ids = await self._seed_ids(scope, seeds, audience, confirmed_only)
        walk.entities.update(await self._entries(scope, ids, audience, confirmed_only))
        walk.seeds = [i for i in ids if i in walk.entities]
        walk.cost = {i: 0 for i in walk.seeds}
        when, values = _true_at(as_of)
        secret = " AND NOT secret" if audience == PLAYERS else ""
        frontier = set(walk.seeds)
        for _ in range(MAX_ROUNDS):
            if not frontier:
                break
            room = MAX_FACTS_TOTAL - len(walk.facts)
            if room <= 0:
                walk.truncated = True
                break
            limit = min(MAX_FACTS_ROUND, room)
            ends = sorted(frontier)
            cur = await scope.conn.execute(
                "SELECT id, subject_id, predicate, object_id, detail, from_session_at,"
                " to_session_at, secret, source, mention_ids FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
                f" AND (subject_id = ANY(%s) OR object_id = ANY(%s)) AND {when}{secret}"
                " ORDER BY from_session_at DESC NULLS LAST, id LIMIT %s",
                (*scope.ids, ends, ends, *values, limit),
            )
            rows = await cur.fetchall()
            if len(rows) == limit:
                walk.truncated = True
            if audience == PLAYERS:
                rows = [r for r in rows if str(r["source"]) not in PLAN_SOURCES]
            unknown = {
                end
                for r in rows
                for end in (str(r["subject_id"]), str(r["object_id"]))
                if end not in walk.entities
            }
            walk.entities.update(
                await self._entries(scope, sorted(unknown), audience, confirmed_only)
            )
            frontier = self._step(walk, rows, frontier, hops)
        else:
            walk.truncated = walk.truncated or bool(frontier)
        return walk

    @staticmethod
    def _step(
        walk: _Walk, rows: Sequence[Mapping[str, Any]], frontier: set[str], hops: int
    ) -> set[str]:
        """Take one round's facts into the walk and return the entries to expand next. Costs
        are read from the round's start, never from what this round has just set, so the
        result does not depend on the order the database returned the rows in."""
        before = dict(walk.cost)
        reached: dict[str, int] = {}
        for r in rows:
            sub, obj = str(r["subject_id"]), str(r["object_id"])
            if sub not in walk.entities or obj not in walk.entities:
                continue  # an end that is no live entry (a person, or one hidden from the audience)
            best: tuple[int, str, str] | None = None
            for near, far in ((sub, obj), (obj, sub)):
                if near in frontier:
                    reach = before[near] + (0 if walk.is_cluster(near) else 1)
                    if best is None or (reach, near) < (best[0], best[1]):
                        best = (reach, near, far)
            if best is None or best[0] > hops:
                continue
            reach, near, far = best
            old = walk.facts.get(str(r["id"]))
            if old is None or reach < old.hops:
                walk.facts[str(r["id"])] = _fact_of(r, reach, near, far)
            if far not in before or reach < before[far]:
                reached[far] = min(reach, reached.get(far, reach))
        nxt: set[str] = set()
        for entity_id, cost in reached.items():  # in the order the facts came: best first
            walk.cost[entity_id] = cost
            # An entry at the last step can add nothing, unless it is a group: its members come
            # with it at no step.
            if cost < hops or walk.is_cluster(entity_id):
                if len(nxt) < MAX_FRONTIER:
                    nxt.add(entity_id)
                else:
                    walk.truncated = True
        return nxt

    async def _seed_ids(
        self, scope: Scope, seeds: Sequence[str], audience: str, confirmed_only: bool = False
    ) -> list[str]:
        """The entries the seeds mean, in the order given: an id as it is, a name through the
        entries' names and the other names it goes by. The players' audience only trusts names
        the DM has confirmed and has not made secret."""
        ids: dict[str, None] = {}
        by_key: dict[str, list[str]] = {}
        keys = sorted({name_key(n) for n in seeds if not _ID.match(n) and name_key(n)})
        if keys:
            # Every entry's own name is its first alias, so one indexed lookup finds names and
            # other names alike.
            if audience == PLAYERS:
                hide = " AND NOT secret AND status = 'confirmed'"
            elif confirmed_only:
                hide = " AND status = 'confirmed'"
            else:
                hide = " AND status <> 'rejected'"
            cur = await scope.conn.execute(
                "SELECT key, entity_id FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                f" AND key = ANY(%s){hide}",
                (*scope.ids, keys),
            )
            for r in await cur.fetchall():
                by_key.setdefault(str(r["key"]), []).append(str(r["entity_id"]))
        for seed in seeds:
            found = [seed] if _ID.match(seed) else by_key.get(name_key(seed), [])
            for entity_id in found:
                ids.setdefault(entity_id)
        return list(ids)

    async def _entries(
        self, scope: Scope, ids: Sequence[str], audience: str, confirmed_only: bool = False
    ) -> dict[str, dict[str, object]]:
        """The live entries among `ids`. For the players: confirmed ones only, and never an
        entry whose own name the DM has made secret (its identity is the secret), whether it
        came by name or by id."""
        if not ids:
            return {}
        live = ["confirmed"] if audience == PLAYERS or confirmed_only else list(_LIVE)
        # An entry's note is the DM's own: the players' audience never even reads it.
        note = "description" if audience == DM_SCREEN else "'' AS description"
        # A clue only some people know (#1649) is the DM's alone: the players' audience gets
        # neither the clue nor, because a fact needs both ends, anyone's `knows` to it.
        # Story threads (#1662) are the DM's plot notes, and so are deeds (#1732): the same.
        hide_clue = (
            " AND NOT audience_limited AND NOT type = ANY(%s)" if audience == PLAYERS else ""
        )
        cur = await scope.conn.execute(
            f"SELECT id, type, name, {note}, role FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)"
            f" AND status = ANY(%s) AND type <> 'person'{hide_clue}",
            (*scope.ids, list(ids), live, *([list(DM_ONLY_TYPES)] if hide_clue else [])),
        )
        rows = {str(r["id"]): dict(r) for r in await cur.fetchall()}
        if audience == PLAYERS and rows:
            cur = await scope.conn.execute(
                "SELECT entity_id, key FROM memory_aliases WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = ANY(%s) AND secret",
                (*scope.ids, list(rows)),
            )
            for r in await cur.fetchall():
                entry = rows.get(str(r["entity_id"]))
                if entry is not None and name_key(str(entry["name"])) == str(r["key"]):
                    del rows[str(r["entity_id"])]
        return rows

    async def _heard(
        self, scope: Scope, ids: Sequence[str], agreed: Collection[int]
    ) -> dict[str, int]:
        """How often each name was said, by people recorded now: counts, never words."""
        if not ids or not agreed:
            return {}
        cur = await scope.conn.execute(
            "SELECT entity_id, sum(times)::int AS times FROM memory_heard"
            " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)"
            " AND speaker_id = ANY(%s) GROUP BY entity_id",
            (*scope.ids, list(ids), list(agreed)),
        )
        return {str(r["entity_id"]): int(r["times"]) for r in await cur.fetchall()}

    async def _conditions(self, scope: Scope, ids: Sequence[str], as_of: AsOf) -> dict[str, str]:
        """What each entry was (dead, lost, ...) at `as_of`; with no time, what it is now (the
        open condition). DM screen only."""
        if not ids:
            return {}
        cur = await scope.conn.execute(
            "SELECT DISTINCT ON (entity_id) entity_id, condition FROM memory_states"
            " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)"
            " AND CASE WHEN %s::bigint IS NULL THEN closed_at IS NULL"
            " ELSE (from_session_at IS NULL OR from_session_at <= %s) END"
            " ORDER BY entity_id, from_session_at DESC NULLS LAST, created_at DESC, id",
            (*scope.ids, list(ids), as_of.session_at, as_of.session_at),
        )
        return {str(r["entity_id"]): str(r["condition"]) for r in await cur.fetchall()}

    async def _sources(self, scope: Scope, facts: Sequence[_Fact]) -> Sources:
        """For each fact, where it was said: (line reference, session, speakers who said it)
        from its mentions and from the claim it was confirmed from.
        Today the quoted lines come only from the claim (the extractor path, off until #1282):
        nothing writes `memory_mentions` (#1740), so `mention_ids` are empty on a live table and
        a fact the DM typed in by hand has no quoted lines until the extractor is on."""
        out: Sources = {}
        mention_ids = sorted({m for f in facts for m in f.mention_ids})
        by_mention: dict[str, tuple[str, int | None]] = {}
        if mention_ids:
            cur = await scope.conn.execute(
                "SELECT id, line_ref, session_started_at FROM memory_mentions"
                " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)",
                (*scope.ids, mention_ids),
            )
            by_mention = {
                str(r["id"]): (str(r["line_ref"]), r["session_started_at"])
                for r in await cur.fetchall()
            }
        for f in facts:
            for m in f.mention_ids:
                if m in by_mention:
                    ref, session = by_mention[m]
                    out.setdefault(f.id, []).append((ref, session, None))
        cur = await scope.conn.execute(
            "SELECT fact_id, lines, speaker_ids, session_started_at FROM memory_claims"
            " WHERE guild_id = %s AND campaign_id = %s AND fact_id = ANY(%s)"
            " AND status = 'confirmed'",
            (*scope.ids, [f.id for f in facts]),
        )
        for r in await cur.fetchall():
            speakers = tuple(int(s) for s in r["speaker_ids"] or ())
            for ref in r["lines"] or []:
                out.setdefault(str(r["fact_id"]), []).append(
                    (str(ref), r["session_started_at"], speakers)
                )
        return out

    async def _quotes(
        self,
        guild_id: int,
        campaign_id: str,
        sources: Sources,
        agreed: frozenset[int],
        opted_in: OptedIn,
    ) -> dict[str, tuple[Quote, ...]]:
        """The quotes each fact may carry: from lines of people recorded now, newest first, at
        most a few."""
        assert self._lines is not None
        wanted = sorted({ref for refs in sources.values() for ref, _, _ in refs})
        if not wanted:
            return {}
        try:
            lines = await asyncio.wait_for(
                self._lines(guild_id, campaign_id, wanted), LINES_TIMEOUT_S
            )
        except Exception:  # a reader that fails or hangs costs the quotes, nothing else
            log.warning("Recall: the transcript could not be read; no quotes this time")
            return {}
        # Who is recorded is asked again now, after the awaits: someone who stopped meanwhile
        # is not quoted.
        agreed = agreed & await _agreed(opted_in)
        out: dict[str, tuple[Quote, ...]] = {}
        for fact_id, refs in sources.items():
            quotes: dict[str, Quote] = {}
            for ref, session, speakers in refs:
                line = lines.get(ref)
                if line is None or line.sidebar or line.speaker_id not in agreed:
                    continue
                if speakers is not None and (not speakers or not set(speakers) <= agreed):
                    continue  # the claim it came from cites someone who is not recorded now
                text = " ".join(line.text.split())[:QUOTE_CHARS]
                if text:
                    quotes[ref] = Quote(ref, text, line.session_started_at or session, line.at_ms)
            newest = sorted(
                quotes.values(), key=lambda q: (q.session_started_at or 0, q.at_ms or 0, q.line_ref)
            )[::-1]
            if newest:
                out[fact_id] = tuple(newest[:EVIDENCE_PER_FACT])
        return out


def _replace_quotes(item: Item, quotes: tuple[Quote, ...]) -> Item:
    return Item(item.kind, item.id, item.text, item.score, item.hops, item.secret, quotes)


@dataclass(slots=True)
class _Found:
    walk: _Walk
    heard: dict[str, int]
    conditions: dict[str, str]
    onto: Ontology
    as_of: AsOf
    audience: str

    def items(self) -> list[Item]:
        walk = self.walk
        reference = self.as_of.session_at
        if reference is None:
            starts = [f.from_at for f in walk.facts.values() if f.from_at is not None]
            reference = max(starts) if starts else None
        items: list[Item] = []
        best: dict[str, float] = {}
        for fid, f in walk.facts.items():
            heard = self.heard.get(f.near, 0) + self.heard.get(f.far, 0)
            score = (
                (1.0 / max(1, f.hops))
                * (0.5 + 0.5 * _recency(f.from_at, reference))
                * (1.0 + 0.25 * math.log1p(heard))
            )
            score = round(score, 6)
            for end in (f.near, f.far):
                best[end] = max(best.get(end, 0.0), score)
            items.append(Item(FACT, fid, self._fact_text(f), score, f.hops, secret=f.secret))
        for entity_id, e in walk.entities.items():
            seed = entity_id in walk.seeds
            if not seed and entity_id not in best:
                continue
            score = (
                10.0 + 1.0 / (1 + walk.seeds.index(entity_id))
                if seed
                else round(0.5 * best[entity_id], 6)
            )
            hops = 0 if seed else walk.cost.get(entity_id, MAX_HOPS)
            items.append(Item(ENTRY, entity_id, self._entry_text(e, entity_id), score, hops))
        return items

    def _entry_text(self, e: Mapping[str, object], entity_id: str) -> str:
        kind = KIND_LABELS.get(str(e["type"]), str(e["type"]))
        role = ROLE_LABELS.get(str(e["role"])) if e.get("role") else None
        words = f"{e['name']} ({kind}{', ' + role if role else ''}"
        if self.audience == DM_SCREEN and entity_id in self.conditions:
            words += f", {self.conditions[entity_id]}"
        words += ")"
        if self.audience == DM_SCREEN and e["description"]:
            words += f": {e['description']}"
        return words

    def _fact_text(self, f: _Fact) -> str:
        walk = self.walk
        what = _predicate_words(self.onto, f.predicate, f.detail)
        return f"{walk.entities[f.subject_id]['name']} {what} {walk.entities[f.object_id]['name']}"


def _cut(
    audience: str, as_of: AsOf, seeds: tuple[str, ...], items: list[Item], budget: int
) -> Recall:
    """Highest score first (ties by id, so the same question always gets the same answer), kept
    whole until the budget is spent. An item whose quotes do not fit is kept without them; an
    item that cannot fit at all is left out and the smaller ones after it still get their
    turn, so one long entry never empties the recall."""
    ordered = sorted(items, key=lambda i: (-i.score, i.kind, i.id))
    kept: list[Item] = []
    used = 0
    for item in ordered:
        if used + item.size > budget and item.quotes:
            item = _replace_quotes(item, ())
        if used + item.size > budget:
            continue
        kept.append(item)
        used += item.size
    return Recall(audience, as_of, tuple(kept), seeds, len(ordered) - len(kept), used)


def _fill(
    items: Sequence[Item], quotes: Mapping[str, tuple[Quote, ...]], room: int
) -> tuple[tuple[Item, ...], int]:
    """Give the best facts their quotes, in score order, while there is budget left over: a
    quote is added whole or not at all, and never takes the place of an item. Returns the items
    and how much room the quotes used."""
    out: list[Item] = []
    used = 0
    for item in items:
        given: list[Quote] = []
        for quote in quotes.get(item.id, ()) if item.kind == FACT else ():
            cost = len(quote.text) + 20
            if used + cost > room:
                break
            given.append(quote)
            used += cost
        out.append(_replace_quotes(item, tuple(given)) if given else item)
    return tuple(out), used
