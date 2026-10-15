"""Continuity warnings, after the session (docs/STORY_MEMORY.md, section 4; #1320).

Slice 3 of phase 5a. After a session, the claims the DM has not answered yet (#1297) are run
through the section-2 rule checks (`checks.py`) against the campaign's **confirmed** facts and
current conditions. Every hit is a *possible conflict* with a level (possible, probable or
likely) that the rule sets, never an AI: a direct contradiction of something confirmed is
likely, an indirect one only possible. The DM sees them in plain words, with the earlier thing
quoted, and chooses; DMbot never decides what is true in the story.

Nothing is shown until the campaign has enough confirmed story to check against (the "critical
mass"), and nothing here runs during play. Pure functions first (they need no database), then
`ContinuityStore`, which reads one campaign at a time and writes only when the DM presses a
button, in one transaction (like #1301).

Isolation and consent: every read is scoped to one campaign by the memory store. A line is
quoted only when every speaker it came from is recorded now (`opted_in`, read just before);
otherwise the conflict is still shown, with the fact in DMbot's own words and no quote.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory._changes import ALIASES, CLAIMS, ENTITIES, RELATIONS, STATES, Changes
from dmbot.memory.checks import (
    ACTS_AFTER_END,
    CONTRADICTION,
    HELD_TWICE,
    TOO_MANY,
    Ended,
    Nesting,
    check_relation,
    ordered,
)
from dmbot.memory.claims import (
    GONE,
    Claim,
    ClaimStore,
    Extracted,
    Names,
    condition_of,
    names_of,
    place_relationship,
)
from dmbot.memory.models import (
    ALIVE,
    CLAIM_CONFIRMED,
    CLAIM_PROPOSED,
    CONFIRMED,
    DESTROYED,
    DM,
    ENDED,
    INTACT,
    Entity,
    MemoryRuleError,
    Relation,
    State,
    name_key,
    new_id,
)
from dmbot.memory.ontology import Ontology
from dmbot.memory.states import place_nesting, state_of
from dmbot.memory.store import MemoryStore, _entity, _load_ontology, _relation

log = logging.getLogger(__name__)

LIKELY, PROBABLE, POSSIBLE = "likely", "probable", "possible"

# What kind of conflict a claim makes with something confirmed. The first four are the
# `checks.py` rules; BACK_FROM_THE_END is a claim that a dead or destroyed entry is something
# else again, which only the DM can say (`states.py`).
BACK_FROM_THE_END = "back_from_the_end"

# How sure each rule is, set here and not guessed by an AI: a direct contradiction of a
# confirmed fact is likely; a dead or destroyed entry acting later is probable (it may be a
# flashback or a ghost); a one-of-a-kind item in two hands is probable; being in two places at
# once is only possible (people travel). These numbers are tuned on the golden set.
RULE_PROBABILITY: Mapping[str, float] = {
    BACK_FROM_THE_END: 0.90,
    CONTRADICTION: 0.90,
    ACTS_AFTER_END: 0.65,
    HELD_TWICE: 0.70,
    TOO_MANY: 0.40,
}

# What the buttons do, in the button's custom ID and in the logs (counts, never words).
CHANGED, NOT_TRUE, IGNORE = "c", "n", "i"
ACTIONS = (CHANGED, NOT_TRUE, IGNORE)


@dataclass(frozen=True, slots=True)
class Levels:
    """The backend settings (DMBOT_CONTINUITY_*): where each level starts, and how much
    confirmed story a campaign needs before any warning is shown. `possible` ≥ 25%,
    `probable` > 50%, `likely` ≥ 80% (section 4)."""

    possible: float = 0.25
    probable: float = 0.50
    likely: float = 0.80
    min_facts: int = 50
    min_sessions: int = 3

    def level_of(self, probability: float) -> str | None:
        """Which level a probability reaches, or None when it is below `possible`."""
        if probability >= self.likely:
            return LIKELY
        if probability > self.probable:
            return PROBABLE
        if probability >= self.possible:
            return POSSIBLE
        return None


_ORDER = {LIKELY: 0, PROBABLE: 1, POSSIBLE: 2}


@dataclass(frozen=True, slots=True)
class Said:
    """An earlier confirmed claim, in the words it was said in: how DMbot knows what it
    knows. `quote` is None when anyone who said it is not recorded now."""

    session_started_at: int | None
    quote: str | None


@dataclass(frozen=True, slots=True)
class World:
    """What the checks need, read once: the confirmed facts, the entries and what they are
    called, who is dead or destroyed, and which earlier claims the facts came from."""

    onto: Ontology
    names: Names
    entities: Mapping[str, Entity]
    relations: Sequence[Relation]  # confirmed only
    unique: frozenset[str]  # one-of-a-kind items
    nesting: Nesting
    states: Mapping[str, State]  # current conditions
    said: Mapping[str, Said] = field(default_factory=dict)  # fact or state id -> where from


@dataclass(frozen=True, slots=True)
class Conflict:
    claim: Claim
    kind: str
    probability: float
    level: str
    entity_id: str  # the entry the warning is about
    entity_name: str
    ended_condition: str | None  # dead / destroyed, when the earlier thing is an ending
    earlier_words: str  # the earlier fact in DMbot's words: "Bram carries Heartseeker"
    earlier: Said | None
    clash_ids: tuple[str, ...]  # the earlier facts a "that changed" answer ends
    revive_id: str | None  # the dead or destroyed entry a "that changed" answer brings back
    condition: str | None  # the condition the claim itself says, for a state claim

    @property
    def pair(self) -> tuple[str, str]:
        return (self.claim.id, self.kind)


@dataclass(frozen=True, slots=True)
class Report:
    """What an after-session check found."""

    conflicts: tuple[Conflict, ...] = ()
    facts: int = 0  # confirmed facts in the campaign
    sessions: int = 0  # sessions those facts come from
    ready: bool = False  # critical mass reached
    claims: int = 0  # claims looked at


def reached_critical_mass(facts: int, sessions: int, levels: Levels) -> bool:
    return facts >= levels.min_facts and sessions >= levels.min_sessions


def _is_ended(world: World, entity_id: str) -> State | None:
    state = world.states.get(entity_id)
    return state if state is not None and state.condition in ENDED else None


def conflicts_in(claims: Sequence[Claim], world: World, levels: Levels) -> list[Conflict]:
    """The possible conflicts between these claims and the confirmed story. Pure. A claim that
    names nothing DMbot knows, or fits no rule, makes none. At most one conflict for each claim
    and rule; the surest first."""
    found: list[Conflict] = []
    ended = {
        eid: Ended(state.from_session_at, state.created_at)
        for eid, state in world.states.items()
        if state.condition in ENDED
    }
    for claim in claims:
        if claim.status != CLAIM_PROPOSED:
            continue
        wording = Extracted(
            claim.subject, claim.relationship, claim.object, claim.details, claim.lines, claim.how
        )
        who = world.names.one(claim.subject)
        if isinstance(who, str):
            continue
        condition = condition_of(wording)
        if condition is not None:
            end = _is_ended(world, who[0])
            if end is not None and condition not in ENDED:
                found.append(
                    _make(claim, BACK_FROM_THE_END, world, levels, who[0], end, condition=condition)
                )
            continue
        other = world.names.one(claim.object)
        if isinstance(other, str):
            continue
        term, from_object, _label = place_relationship(
            world.onto, claim.relationship, who[1], other[1]
        )
        subject_id, object_id = (other[0], who[0]) if from_object else (who[0], other[0])
        if subject_id == object_id:
            continue
        s, o = ordered(term, subject_id, object_id)
        candidate = Relation(
            new_id(), s, term.key, o, "", 1.0, CONFIRMED, DM, (), claim.session_started_at, None,
            None, None, False, claim.created_at,
        )  # fmt: skip
        touching = [r for r in world.relations if {r.subject_id, r.object_id} & {s, o}]
        for problem in check_relation(
            world.onto,
            candidate,
            world.entities[s].type,
            world.entities[o].type,
            touching,
            object_unique=o in world.unique,
            nesting=world.nesting,
            ended=ended,
        ):
            if problem.kind not in RULE_PROBABILITY:
                continue  # types, place loops: structure, not continuity
            clash = [r for r in touching if r.id == problem.other_id]
            if problem.kind == ACTS_AFTER_END:
                actor = next((e for e in (s, o) if e in ended), who[0])
                found.append(
                    _make(claim, problem.kind, world, levels, actor, _is_ended(world, actor))
                )
            elif clash:
                found.append(
                    _make(claim, problem.kind, world, levels, who[0], None, clash=clash[0])
                )
    seen: set[tuple[str, str]] = set()
    unique: list[Conflict] = []
    for item in found:
        if item.level and item.pair not in seen:
            seen.add(item.pair)
            unique.append(item)
    unique.sort(
        key=lambda c: (_ORDER[c.level], -c.probability, name_key(c.entity_name), c.claim.id)
    )
    return unique


def _make(
    claim: Claim,
    kind: str,
    world: World,
    levels: Levels,
    entity_id: str,
    end: State | None,
    *,
    condition: str | None = None,
    clash: Relation | None = None,
) -> Conflict:
    probability = RULE_PROBABILITY[kind]
    level = levels.level_of(probability)
    entity = world.entities.get(entity_id)
    name = entity.name if entity is not None else claim.subject
    if clash is not None:
        subject = world.entities.get(clash.subject_id)
        obj = world.entities.get(clash.object_id)
        words = " ".join(
            part
            for part in (
                subject.name if subject else "",
                _predicate_words(world.onto, clash.predicate, clash.detail),
                obj.name if obj else "",
            )
            if part
        )
        said = world.said.get(clash.id)
        clash_ids: tuple[str, ...] = (clash.id,)
    else:
        words = ""
        said = world.said.get(end.id) if end is not None else None
        clash_ids = ()
    revive = end.entity_id if end is not None and kind != BACK_FROM_THE_END else None
    return Conflict(
        claim=claim,
        kind=kind,
        probability=probability,
        level=level or POSSIBLE,
        entity_id=entity_id,
        entity_name=name,
        ended_condition=end.condition if end is not None else None,
        earlier_words=words,
        earlier=said,
        clash_ids=clash_ids,
        revive_id=revive,
        condition=condition,
    )


def _predicate_words(onto: Ontology, key: str, detail: str) -> str:
    term = onto.predicates.get(key)
    return detail if key == "associated_with" and detail else (term.label if term else key)


class ContinuityStore:
    """Finds the conflicts and applies the DM's answers. One campaign at a time."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._claims = ClaimStore(memory)
        self._crossed: set[str] = set()

    async def check(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        opted_in: Collection[int],
        levels: Levels,
        session_started_at: int | None = None,
    ) -> Report:
        """The session's unanswered claims against the confirmed story. With
        `session_started_at`, only that session's claims. Nothing is returned (and nothing is
        read but the counts) below the critical mass."""
        waiting = await self._claims.waiting(guild_id, campaign_id, opted_in=opted_in)
        if session_started_at is not None:
            waiting = [c for c in waiting if c.session_started_at == session_started_at]
        world, facts, sessions = await self._world(guild_id, campaign_id, opted_in)
        ready = reached_critical_mass(facts, sessions, levels)
        if ready and campaign_id not in self._crossed:
            self._crossed.add(campaign_id)
            log.info(
                "Continuity warnings are on for a campaign: %d confirmed facts, %d sessions",
                facts, sessions,
            )  # fmt: skip
        if not ready or not waiting:
            return Report((), facts, sessions, ready, len(waiting))
        return Report(
            tuple(conflicts_in(waiting, world, levels)), facts, sessions, ready, len(waiting)
        )

    async def _world(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> tuple[World, int, int]:
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            entity_rows = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            alias_rows = await scope.select(ALIASES, " AND status <> 'rejected'")
            relation_rows = await scope.select(
                RELATIONS, " AND status = 'confirmed' AND NOT secret ORDER BY created_at, id"
            )
            nesting = await place_nesting(scope, onto)
            state_rows = await scope.select(STATES, " AND closed_at IS NULL")
            claim_rows = await scope.select(
                CLAIMS, " AND status = 'confirmed' AND fact_id IS NOT NULL"
            )
        entities = {e.id: e for e in (_entity(r) for r in entity_rows)}
        relations = [
            _relation(r)
            for r in relation_rows
            if r["subject_id"] in entities and r["object_id"] in entities
        ]
        states = {str(r["entity_id"]): state_of(r) for r in state_rows}
        said: dict[str, Said] = {}
        for row in claim_rows:
            speakers = tuple(row["speaker_ids"])
            ok = bool(speakers) and all(s in opted_in for s in speakers)
            words = " ".join(
                part for part in (row["subject"], row["relationship"], row["object"]) if part
            )
            said[str(row["fact_id"])] = Said(row["session_started_at"], words if ok else None)
        unique = frozenset(
            e.id for e in entities.values() if onto.is_a(e.type, "item") and e.is_unique
        )
        sessions = {r.from_session_at for r in relations if r.from_session_at is not None}
        sessions |= {s.from_session_at for s in states.values() if s.from_session_at is not None}
        world = World(
            onto,
            names_of(entity_rows, alias_rows),
            entities,
            relations,
            unique,
            nesting,
            states,
            said,
        )
        return world, len(relations), len(sessions)

    async def answer(
        self,
        guild_id: int,
        campaign_id: str,
        claim_id: str,
        action: str,
        *,
        opted_in: Collection[int],
        levels: Levels,
    ) -> str:
        """Apply one button. Returns what happened, for the DM, in plain words. Raises
        `MemoryRuleError` (with a message to show) when it can't be done and nothing changed.
        CHANGED: the claim becomes a fact, dated, and what it replaces ends or comes back (one
        transaction); NOT_TRUE: the claim is not world truth and is not asked again; IGNORE:
        nothing is written."""
        if action == IGNORE:
            return "Left as it is."
        claim = await self._claims.get(guild_id, campaign_id, claim_id)
        if claim is None or claim.status != CLAIM_PROPOSED:
            raise MemoryRuleError(GONE)
        if action == NOT_TRUE:
            await self._claims.reject(guild_id, campaign_id, claim_id)
            return "Not remembered. DMbot won't ask again."
        if action != CHANGED:
            raise MemoryRuleError("DMbot doesn't know that button any more.")
        world, _facts, _sessions = await self._world(guild_id, campaign_id, opted_in)
        mine = [c for c in conflicts_in([claim], world, levels) if c.claim.id == claim_id]
        top = mine[0] if mine else None

        async def before(w: Changes) -> None:
            if top is None:
                return
            if top.revive_id is not None:  # a dead or destroyed entry acts again: it is back
                back = INTACT if top.ended_condition == DESTROYED else ALIVE
                await self._memory.set_condition_in(
                    w, top.revive_id, back, claim.session_started_at
                )
            for fact_id in top.clash_ids:  # the earlier fact ends where the new one starts
                if claim.session_started_at is not None:
                    await w.update(RELATIONS, fact_id, {"to_session_at": claim.session_started_at})

        result = await self._claims.confirm(
            guild_id, campaign_id, claim_id, opted_in=opted_in, before=before
        )
        assert result.claim.status == CLAIM_CONFIRMED
        return "Remembered, and the earlier story is kept as history."
