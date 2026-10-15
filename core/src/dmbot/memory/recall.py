"""The recall service (#1350, docs/STORY_MEMORY.md section 7): what DMbot may tell a helper
about the story, assembled in code so the AI only reads it.

Given some starting names (`seeds`), it walks one or two steps out over the facts the DM
**confirmed** that are true at the story time asked about, ranks them by how recent they are and
how often their names are heard, keeps what the audience may see, adds a few quoted lines as
evidence, and cuts the result to a size budget. Every item carries the id of the entry, fact or
transcript line it comes from, and `check_citations` confirms an answer cites only those.

Who may see what is decided here, before anything is returned (never by the prompt):

- `dm_screen`: everything, secrets included.
- `players`: no secret facts, no secret names, no entry notes (the DM's own description of an
  entry), no conditions (alive, dead) and nothing from the story plan.
- `transcript`: always empty. Nothing from here ever goes into a transcript.

Evidence is quoted only from speakers who are recorded *now*: the caller passes who that is and
this checks again on every read, so a person who stops being recorded vanishes from the next
recall. Sidebar lines (the DM's questions to DMbot and its answers) are never quoted.

Every read goes through one campaign (its server and its id are in every query, and row-level
security backs that up), so another campaign or server never shows up, even with the same names.
No AI, no embeddings and no graph database: a few indexed lookups per step, so one recall on a
campaign of thousands of entries takes milliseconds.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory._changes import Scope
from dmbot.memory.continuity import _predicate_words
from dmbot.memory.kinds import KIND_LABELS
from dmbot.memory.models import ROLE_LABELS, name_key
from dmbot.memory.ontology import Ontology
from dmbot.memory.store import MemoryStore, _load_ontology

DM_SCREEN, PLAYERS, TRANSCRIPT = "dm_screen", "players", "transcript"
AUDIENCES = (DM_SCREEN, PLAYERS, TRANSCRIPT)

DEFAULT_BUDGET = 4000  # characters; the backend setting DMBOT_RECALL_BUDGET overrides it
MAX_HOPS = 2
EVIDENCE_PER_FACT = 3
QUOTE_CHARS = 160
ITEM_OVERHEAD = 40  # an item's id and punctuation count against the budget too
SESSION_SECONDS = 30 * 86400  # a fact a month older than the newest scores half as recent
# A group or a thread holds its members together: walking through one costs no extra step.
CLUSTER_TYPES = frozenset({"faction", "thread"})
# The story plan is a separate layer (docs/STORY_MEMORY.md section 6) that does not exist as
# stored facts yet. Anything that says it comes from it is held back from the players, so
# turning the layer on later cannot leak the book.
PLAN_SOURCES = frozenset({"plan", "story_plan"})
_ID = re.compile(r"^[0-9a-f]{32}$")
_LIVE = ("proposed", "confirmed")

ENTRY, FACT = "entry", "fact"


@dataclass(frozen=True, slots=True)
class AsOf:
    """The story time facts must be true at: which session (its start, Unix seconds) and, once
    TimeBot has it, the game time. Neither: the story as it stands now."""

    session_at: int | None = None
    game_time: int | None = None


@dataclass(frozen=True, slots=True)
class Line:
    """One transcript line, as the caller's reader gives it. `sidebar` marks the DM's questions
    to DMbot and its answers, which are never quoted."""

    ref: str
    text: str
    speaker_id: int | None
    session_started_at: int | None = None
    at_ms: int | None = None
    sidebar: bool = False


# Reads transcript lines by the references facts and claims cite. The caller owns the
# transcript, so recall does not reach into it; a missing line is simply not quoted.
Lines = Callable[[Sequence[str]], Awaitable[Mapping[str, Line]]]


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
    """The SQL that keeps a fact only if it holds at `as_of`, and its values."""
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
    cost: dict[str, int] = field(default_factory=dict)
    facts: dict[str, _Fact] = field(default_factory=dict)


class RecallService:
    """Assembles the context for one question. One campaign at a time."""

    def __init__(self, memory: MemoryStore, lines: Lines | None = None) -> None:
        self._memory = memory
        self._lines = lines

    async def recall(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        seeds: Sequence[str],
        audience: str,
        as_of: AsOf | None = None,
        budget: int = DEFAULT_BUDGET,
        opted_in: Collection[int] = (),
        hops: int = MAX_HOPS,
    ) -> Recall:
        """What may be said about `seeds` (entry ids or names) to `audience` at `as_of`, cut to
        `budget` characters. `opted_in` are the people recorded now: only they are quoted."""
        if audience not in AUDIENCES:
            raise ValueError(f"unknown audience {audience!r}")
        as_of = as_of or AsOf()
        if audience == TRANSCRIPT:  # decided before anything is read
            return Recall(audience, as_of)
        hops = max(0, min(hops, MAX_HOPS))
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            walk = await self._walk(scope, seeds, audience, as_of, hops)
            ids = list(walk.entities)
            heard = await self._heard(scope, ids, opted_in)
            notes = await self._conditions(scope, ids, as_of) if audience == DM_SCREEN else {}
            onto = await _load_ontology(scope)
            sources = await self._sources(scope, list(walk.facts.values()))
        found = _Found(walk, heard, notes, onto, sources, as_of, audience)
        items = found.items()
        if self._lines is not None and opted_in:
            items = await self._with_quotes(items, found, set(opted_in))
        return _cut(audience, as_of, tuple(walk.seeds), items, budget)

    # ---- the walk --------------------------------------------------------------------

    async def _walk(
        self, scope: Scope, seeds: Sequence[str], audience: str, as_of: AsOf, hops: int
    ) -> _Walk:
        walk = _Walk()
        ids = await self._seed_ids(scope, seeds, audience)
        walk.entities.update(await self._entries(scope, ids))
        walk.seeds = [i for i in ids if i in walk.entities]
        walk.cost = {i: 0 for i in walk.seeds}
        when, values = _true_at(as_of)
        secret = " AND NOT secret" if audience == PLAYERS else ""
        frontier = list(walk.seeds)
        for _ in range(MAX_HOPS * 2 + 1):  # groups cost no step, so allow a few extra rounds
            if not frontier:
                break
            cur = await scope.conn.execute(
                "SELECT id, subject_id, predicate, object_id, detail, from_session_at,"
                " to_session_at, secret, source, mention_ids FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
                f" AND (subject_id = ANY(%s) OR object_id = ANY(%s)) AND {when}{secret}",
                (*scope.ids, frontier, frontier, *values),
            )
            rows = await cur.fetchall()
            if audience == PLAYERS:
                rows = [r for r in rows if str(r["source"]) not in PLAN_SOURCES]
            unknown = {
                end
                for r in rows
                for end in (str(r["subject_id"]), str(r["object_id"]))
                if end not in walk.entities
            }
            walk.entities.update(await self._entries(scope, sorted(unknown)))
            frontier = []
            for r in rows:
                fid, sub, obj = str(r["id"]), str(r["subject_id"]), str(r["object_id"])
                if sub not in walk.entities or obj not in walk.entities:
                    continue  # an end that is not a live entry (a person, or rejected)
                near, far = (sub, obj) if sub in walk.cost else (obj, sub)
                if near not in walk.cost:
                    continue
                step = 0 if walk.entities[near]["type"] in CLUSTER_TYPES else 1
                reach = walk.cost[near] + step
                if reach > hops or fid in walk.facts:
                    continue
                walk.facts[fid] = _fact_of(r, reach, near, far)
                if far not in walk.cost or reach < walk.cost[far]:
                    walk.cost[far] = reach
                    frontier.append(far)
        return walk

    async def _seed_ids(self, scope: Scope, seeds: Sequence[str], audience: str) -> list[str]:
        """The entries the seeds mean, in the order given: an id as it is, a name through the
        entries' names and the other names it goes by (secret ones only for the DM screen)."""
        ids: dict[str, None] = {}
        by_key: dict[str, list[str]] = {}
        keys = sorted({name_key(n) for n in seeds if not _ID.match(n) and name_key(n)})
        if keys:
            # Every entry's own name is its first alias, so one indexed lookup finds names and
            # other names alike. A secret name only means something to the DM screen.
            hide = " AND NOT secret" if audience == PLAYERS else ""
            cur = await scope.conn.execute(
                "SELECT key, entity_id FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                f" AND key = ANY(%s) AND status <> 'rejected'{hide}",
                (*scope.ids, keys),
            )
            for r in await cur.fetchall():
                by_key.setdefault(str(r["key"]), []).append(str(r["entity_id"]))
        for seed in seeds:
            found = [seed] if _ID.match(seed) else by_key.get(name_key(seed), [])
            for entity_id in found:
                ids.setdefault(entity_id)
        return list(ids)

    async def _entries(self, scope: Scope, ids: Sequence[str]) -> dict[str, dict[str, object]]:
        if not ids:
            return {}
        cur = await scope.conn.execute(
            "SELECT id, type, name, description, role FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)"
            " AND status = ANY(%s) AND type <> 'person'",
            (*scope.ids, list(ids), list(_LIVE)),
        )
        return {str(r["id"]): dict(r) for r in await cur.fetchall()}

    async def _heard(
        self, scope: Scope, ids: Sequence[str], opted_in: Collection[int]
    ) -> dict[str, int]:
        """How often each name was said, by people recorded now: counts, never words."""
        if not ids or not opted_in:
            return {}
        cur = await scope.conn.execute(
            "SELECT entity_id, sum(times)::int AS times FROM memory_heard"
            " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)"
            " AND speaker_id = ANY(%s) GROUP BY entity_id",
            (*scope.ids, list(ids), list(opted_in)),
        )
        return {str(r["entity_id"]): int(r["times"]) for r in await cur.fetchall()}

    async def _conditions(self, scope: Scope, ids: Sequence[str], as_of: AsOf) -> dict[str, str]:
        """The latest condition (dead, lost, ...) each entry had by `as_of`. DM screen only."""
        if not ids:
            return {}
        cur = await scope.conn.execute(
            "SELECT DISTINCT ON (entity_id) entity_id, condition FROM memory_states"
            " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)"
            " AND (%s::bigint IS NULL OR from_session_at IS NULL OR from_session_at <= %s)"
            " ORDER BY entity_id, from_session_at DESC NULLS LAST, created_at DESC",
            (*scope.ids, list(ids), as_of.session_at, as_of.session_at),
        )
        return {str(r["entity_id"]): str(r["condition"]) for r in await cur.fetchall()}

    async def _sources(
        self, scope: Scope, facts: Sequence[_Fact]
    ) -> dict[str, list[tuple[str, int | None, tuple[int, ...] | None]]]:
        """For each fact, where it was said: (line reference, session, speakers who said it)
        from its mentions and from the claim it was confirmed from."""
        out: dict[str, list[tuple[str, int | None, tuple[int, ...] | None]]] = {}
        if not facts or self._lines is None:
            return out
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
            " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
            " AND fact_id = ANY(%s)",
            (*scope.ids, [f.id for f in facts]),
        )
        for r in await cur.fetchall():
            speakers = tuple(int(s) for s in r["speaker_ids"] or ())
            for ref in r["lines"] or []:
                out.setdefault(str(r["fact_id"]), []).append(
                    (str(ref), r["session_started_at"], speakers)
                )
        return out

    async def _with_quotes(
        self, items: list[Item], found: _Found, opted_in: set[int]
    ) -> list[Item]:
        assert self._lines is not None
        wanted = sorted({ref for refs in found.sources.values() for ref, _, _ in refs})
        if not wanted:
            return items
        lines = await self._lines(wanted)
        out = []
        for item in items:
            if item.kind != FACT:
                out.append(item)
                continue
            quotes: dict[str, Quote] = {}
            for ref, session, speakers in found.sources.get(item.id, []):
                line = lines.get(ref)
                if line is None or line.sidebar or line.speaker_id not in opted_in:
                    continue
                if speakers is not None and (not speakers or not set(speakers) <= opted_in):
                    continue  # the claim it came from cites someone who is not recorded now
                text = " ".join(line.text.split())[:QUOTE_CHARS]
                if text:
                    quotes[ref] = Quote(ref, text, line.session_started_at or session, line.at_ms)
            newest = sorted(
                quotes.values(), key=lambda q: (q.session_started_at or 0, q.at_ms or 0, q.line_ref)
            )[::-1]
            out.append(_replace_quotes(item, tuple(newest[:EVIDENCE_PER_FACT])))
        return out


def _replace_quotes(item: Item, quotes: tuple[Quote, ...]) -> Item:
    return Item(item.kind, item.id, item.text, item.score, item.hops, item.secret, quotes)


@dataclass(slots=True)
class _Found:
    walk: _Walk
    heard: dict[str, int]
    conditions: dict[str, str]
    onto: Ontology
    sources: dict[str, list[tuple[str, int | None, tuple[int, ...] | None]]]
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
            items.append(
                Item(
                    FACT,
                    fid,
                    self._fact_text(f),
                    score,
                    f.hops,
                    secret=f.secret,
                )
            )
        for entity_id, e in walk.entities.items():
            seed = entity_id in walk.seeds
            if not seed and entity_id not in best:
                continue
            items.append(
                Item(
                    ENTRY,
                    entity_id,
                    self._entry_text(e, entity_id),
                    10.0 + 1.0 / (1 + walk.seeds.index(entity_id))
                    if seed
                    else round(0.5 * best[entity_id], 6),
                    0 if seed else walk.cost.get(entity_id, MAX_HOPS),
                )
            )
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
        sub, obj = f.subject_id, f.object_id
        what = _predicate_words(self.onto, f.predicate, f.detail)
        return f"{walk.entities[sub]['name']} {what} {walk.entities[obj]['name']}"


def _cut(
    audience: str, as_of: AsOf, seeds: tuple[str, ...], items: list[Item], budget: int
) -> Recall:
    """Highest score first (ties by id, so the same question always gets the same answer), kept
    whole until the budget is spent. An item whose quotes do not fit is kept without them."""
    ordered = sorted(items, key=lambda i: (-i.score, i.kind, i.id))
    kept: list[Item] = []
    used = 0
    for position, item in enumerate(ordered):
        if used + item.size > budget:
            bare = _replace_quotes(item, ())
            if item.quotes and used + bare.size <= budget:
                item = bare
            else:
                return Recall(audience, as_of, tuple(kept), seeds, len(ordered) - position, used)
        kept.append(item)
        used += item.size
    return Recall(audience, as_of, tuple(kept), seeds, 0, used)
