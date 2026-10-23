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

import contextlib
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory import beliefs
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
from dmbot.memory.clues import KNOWS, outside_audience, says_they_acted_on_it
from dmbot.memory.models import (
    ALIVE,
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

# Campaigns already logged as past the critical mass in this process: one line each, not one for
# every check (the store is built anew for every call).
_CROSSED: set[str] = set()

LIKELY, PROBABLE, POSSIBLE = "likely", "probable", "possible"

# What kind of conflict a claim makes with something confirmed. The first four are the
# `checks.py` rules; BACK_FROM_THE_END is a claim that a dead or destroyed entry is something
# else again, which only the DM can say (`states.py`).
BACK_FROM_THE_END = "back_from_the_end"
# A character outside a limited clue's list speaks of or uses the clue (#1649). Its number is
# per claim: possible by default, likely when the claim says they told it or used it.
KNOWS_A_SECRET = "knows_a_secret"
KNOWS_A_SECRET_DEFAULT, KNOWS_A_SECRET_ACTED = 0.40, 0.85

# How sure each rule is, set here and not guessed by an AI: a direct contradiction of a
# confirmed fact is likely; a dead or destroyed entry acting later is probable (it may be a
# flashback or a ghost); a one-of-a-kind item in two hands is probable; being in two places at
# once is only possible (people travel). These numbers are provisional: they get tuned on the
# golden set and in live tests.
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
    # Each clue only some people know -> who the DM listed as knowing it (#1649)
    limited: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # The connections the DM pinned as 100% truth (#2447): a claim against one is "off the rails"
    pinned: frozenset[str] = frozenset()


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
    # For KNOWS_A_SECRET (#1649): the character who is outside the list, and who is on it.
    learner_id: str | None = None
    learner_name: str | None = None
    # The earlier fact is one the DM pinned as 100% truth (#2447)
    pinned: bool = False

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
    found: list[Conflict | None] = []
    ended = {
        eid: Ended(state.from_session_at, state.created_at)
        for eid, state in world.states.items()
        if state.condition in ENDED
    }
    by_entry: dict[str, list[Relation]] = {}  # built once: a claim only meets its own entries
    for r in world.relations:
        by_entry.setdefault(r.subject_id, []).append(r)
        by_entry.setdefault(r.object_id, []).append(r)
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
        outsider = _outside_a_limited_clue(world, levels, claim, who[0], other[0])
        if outsider is not None:
            found.append(outsider)
        s, o = ordered(term, subject_id, object_id)
        candidate = Relation(
            id=new_id(), subject_id=s, predicate=term.key, object_id=o, detail="", confidence=1.0,
            status=CONFIRMED, source=DM, mention_ids=(), from_session_at=claim.session_started_at,
            to_session_at=None, from_game_time=None, to_game_time=None, secret=False,
            created_at=claim.created_at,
        )  # fmt: skip
        touching = list({r.id: r for r in (*by_entry.get(s, ()), *by_entry.get(o, ()))}.values())
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
        if item is not None and item.pair not in seen:
            seen.add(item.pair)
            unique.append(item)
    unique.sort(  # what goes against a pinned truth first
        key=lambda c: (
            not c.pinned,
            _ORDER[c.level],
            -c.probability,
            name_key(c.entity_name),
            c.claim.id,
        )
    )
    return unique


def _outside_a_limited_clue(
    world: World, levels: Levels, claim: Claim, one: str, other: str
) -> Conflict | None:
    """A claim that ties a confirmed character to a clue only some people know, when the DM did
    not list that character (#1649). Possible by default; likely when the claim says they told
    it or used it. Nothing for a clue with the switch off, for a name still waiting to be
    confirmed, or for a character who is listed."""
    for character_id, clue_id in ((one, other), (other, one)):
        audience = world.limited.get(clue_id)
        character = world.entities.get(character_id)
        clue = world.entities.get(clue_id)
        if audience is None or character is None or clue is None:
            continue
        if character.type != "character" or character.status != CONFIRMED:
            continue
        if not outside_audience(character_id, audience):
            continue
        probability = (
            KNOWS_A_SECRET_ACTED
            if says_they_acted_on_it(claim.relationship)
            else KNOWS_A_SECRET_DEFAULT
        )
        level = levels.level_of(probability)
        if level is None:
            return None
        return Conflict(
            claim=claim, kind=KNOWS_A_SECRET, probability=probability, level=level,
            entity_id=clue_id, entity_name=clue.name, ended_condition=None,
            earlier_words=_names(world, audience), earlier=None, clash_ids=(),
            revive_id=None, condition=None, learner_id=character_id,
            learner_name=character.name,
        )  # fmt: skip
    return None


def _names(world: World, ids: frozenset[str]) -> str:
    """The people the DM listed, by name: "Belleros, Cerric"."""
    named = sorted((world.entities[i].name for i in ids if i in world.entities), key=name_key)
    return ", ".join(named)


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
) -> Conflict | None:
    """The conflict, or None when the rule is not sure enough to reach even `possible`
    (the backend settings may be raised above a rule's number)."""
    probability = RULE_PROBABILITY[kind]
    level = levels.level_of(probability)
    if level is None:
        return None
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
        # A confirmed "Ulfgar is dead" claim keeps the entry's id as its fact (no state row
        # may have been made), so the state's id and then the entry's id are both tried.
        said = (world.said.get(end.id) or world.said.get(end.entity_id)) if end else None
        clash_ids = ()
    revive = end.entity_id if end is not None and kind != BACK_FROM_THE_END else None
    return Conflict(
        claim=claim,
        kind=kind,
        probability=probability,
        level=level,
        entity_id=entity_id,
        entity_name=name,
        ended_condition=end.condition if end is not None else None,
        earlier_words=words,
        earlier=said,
        clash_ids=clash_ids,
        revive_id=revive,
        condition=condition,
        pinned=clash is not None and clash.id in world.pinned,
    )


def _predicate_words(onto: Ontology, key: str, detail: str) -> str:
    term = onto.predicates.get(key)
    return detail if key == "associated_with" and detail else (term.label if term else key)


class ContinuityStore:
    """Finds the conflicts and applies the DM's answers. One campaign at a time."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._claims = ClaimStore(memory)

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
        facts, sessions = await self._counts(guild_id, campaign_id)
        ready = reached_critical_mass(facts, sessions, levels)
        if ready and campaign_id not in _CROSSED:
            _CROSSED.add(campaign_id)
            log.info(
                "Continuity warnings are on for a campaign: %d confirmed facts, %d sessions",
                facts, sessions,
            )  # fmt: skip
        if not waiting:
            return Report((), facts, sessions, ready, 0)  # the story is not read
        # A 📌 truth the DM pinned is checked even before the critical mass: the DM asked for it
        # (#2447). Nothing pinned, nothing read, so a campaign with none pays nothing.
        pinned = await self._memory.pinned_connections(guild_id, campaign_id)
        if not ready and not pinned:
            return Report((), facts, sessions, ready, len(waiting))
        world = await self._world(guild_id, campaign_id, opted_in, pinned)
        found = conflicts_in(waiting, world, levels)
        if not ready:
            found = [c for c in found if c.pinned]
        return Report(tuple(found), facts, sessions, ready, len(waiting))

    async def is_ready(self, guild_id: int, campaign_id: str, levels: Levels) -> bool:
        """Whether the campaign has reached the critical mass (two counts, no rows): the live
        heads-up reads this once a session (#2068)."""
        facts, sessions = await self._counts(guild_id, campaign_id)
        return reached_critical_mass(facts, sessions, levels)

    async def _counts(self, guild_id: int, campaign_id: str) -> tuple[int, int]:
        """How many confirmed facts the campaign has and how many sessions they come from:
        two counts, no rows, so the story is only read when there is something to check."""
        async with self._memory._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS facts, count(DISTINCT from_session_at) AS sessions"
                " FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed'",
                scope.ids,
            )
            row = await cur.fetchone()
        return (int(row["facts"]), int(row["sessions"])) if row else (0, 0)

    async def _world(
        self,
        guild_id: int,
        campaign_id: str,
        opted_in: Collection[int],
        pinned: Collection[str] = (),
    ) -> World:
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            entity_rows = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            alias_rows = await scope.select(ALIASES, " AND status <> 'rejected'")
            # Secret facts are left out: the DM keeps them from the table on purpose, so a
            # clash with one is not a slip to point at. Unless the DM pinned it as 100% truth:
            # then a clash is just what the pin is for (the list is the DM's alone).
            relation_rows = await scope.select(
                RELATIONS,
                " AND status = 'confirmed' AND (NOT secret OR id = ANY(%s))"
                " ORDER BY created_at, id",
                [list(pinned)],
            )
            nesting = await place_nesting(scope, onto)
            state_rows = await scope.select(STATES, " AND closed_at IS NULL")
            claim_rows = await scope.select(
                CLAIMS, " AND status = 'confirmed' AND fact_id IS NOT NULL"
            )
            # Who the DM listed for each clue only some people know (#1649). Read apart from
            # the facts above: a listed fact the DM marked secret still counts as listed.
            knows_rows = await scope.select(
                RELATIONS,
                " AND predicate = %s AND status = 'confirmed' AND to_session_at IS NULL"
                " AND object_id IN (SELECT id FROM memory_entities WHERE audience_limited"
                " AND guild_id = %s AND campaign_id = %s)",
                [KNOWS, *scope.ids],
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
        limited: dict[str, set[str]] = {
            e.id: set() for e in entities.values() if e.audience_limited
        }
        for row in knows_rows:
            if row["object_id"] in limited:
                limited[row["object_id"]].add(row["subject_id"])
        # A switch on with nobody left on the list (the connection was removed elsewhere) means
        # DMbot doesn't know who knows it, so it says nothing: an empty list is never "nobody".
        limited = {clue: who for clue, who in limited.items() if who}
        return World(
            onto,
            names_of(entity_rows, alias_rows),
            entities,
            relations,
            unique,
            nesting,
            states,
            said,
            {clue: frozenset(who) for clue, who in limited.items()},
            frozenset(pinned),
        )

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
        pinned = await self._memory.pinned_connections(guild_id, campaign_id)
        world = await self._world(guild_id, campaign_id, opted_in, pinned)
        mine = [c for c in conflicts_in([claim], world, levels) if c.claim.id == claim_id]
        clash_ids = tuple(dict.fromkeys(i for c in mine for i in c.clash_ids))
        revive = {c.revive_id: c.ended_condition for c in mine if c.revive_id is not None}
        # "They learned it" (#1649): the character joins the clue's list, dated to the session.
        learners = {(c.entity_id, c.learner_id) for c in mine if c.learner_id is not None}

        async def before(w: Changes) -> None:
            # Everything an answer changes is checked again here, under the campaign's lock,
            # in the transaction that writes the claim: the story may have moved since it was read.
            for entity_id, ended in revive.items():
                state = await w.select(
                    STATES, " AND entity_id = %s AND closed_at IS NULL", [entity_id]
                )
                if state and state[0]["condition"] in ENDED:
                    back = INTACT if ended == DESTROYED else ALIVE
                    await self._memory.set_condition_in(
                        w, entity_id, back, claim.session_started_at
                    )
            for clue_id, character_id in sorted(learners):
                try:
                    await self._memory.add_knower_in(
                        w, clue_id, character_id, claim.session_started_at
                    )
                except MemoryRuleError:
                    continue  # the clue or the character changed meanwhile; the claim still counts
            for fact_id in clash_ids:  # the earlier fact ends where the new one starts
                row = await w.get(RELATIONS, fact_id)
                if row is not None and row["to_session_at"] is None and claim.session_started_at:
                    await w.update(RELATIONS, fact_id, {"to_session_at": claim.session_started_at})
                if (
                    fact_id in world.pinned
                ):  # "Story changed" takes the DM's pin off, in this change
                    with contextlib.suppress(MemoryRuleError):  # (already unpinned meanwhile)
                        await beliefs.set_pin(w, fact_id, False)

        await self._claims.confirm(
            guild_id, campaign_id, claim_id, opted_in=opted_in, before=before
        )
        return "Remembered, and the earlier story is kept as history."
