"""Rule checks run on every new relationship (docs/PLAN.md, "Campaign memory rules", 5).

Pure functions, no database. A failed check never blocks or changes the fact: it
becomes a flag for review, because only the DM decides what's true in their story.

Every rule about the story's memory is one of two kinds, and a new rule says which (#2663;
docs/STORY_MEMORY.md, "Hard rules and warnings"):

**Structural: never broken, so the write is refused.** These protect the data's shape, not the
story, so no reading of the story can excuse them:
- a connection joins two different names (`MemoryStore.add_relation_in`, and a database check);
- every row, and both ends of every connection or belief, belong to the one campaign the write is
  for: composite keys on `(guild_id, campaign_id, ...)` in the database, never a check in code;
- a "led to" link, when it is made, joins confirmed names and does not close a loop
  (`threads.would_loop`). Confirming a rejected link, merging two names and restoring a backup do
  not run this check yet (follow-up issue);
- a confidence is 0 to 1, a status is a known one and a session span ends after it starts (checked
  in code before the write, `store._check_span` and the like; the span has no database check), and
  a campaign has one "truth" view and one "party" view (unique indexes).

**A warning: flagged for the DM, never refused.** The story may be right and the data odd: the
`FLAG_KINDS` below. A one-of-a-kind item in two holders' hands may be a handoff, a theft or a
mistaken belief; a dead name acting may be a flashback. The fact is stored as given, a flag asks
the DM, and the DM decides.

**Safe when two writes race.** A structural rule that reads before it writes (the loop check, the
"already known" check) is only safe if no other write to the campaign slips in between. Every
rule-checked write goes through `MemoryStore._write`, which takes the campaign's row lock
(`SELECT ... FOR NO KEY UPDATE` on `campaigns`) and holds it to the end of the transaction, so one
campaign's writes run one at a time. A path that reads and writes memory outside `_write` must lock
the same campaign row (an advisory lock would not keep `_write` out), or lean on a database
constraint. Every check stays inside one campaign: no query here or in the store reads another
campaign's items, so a same-named item elsewhere raises nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from dmbot.memory import kin, next_words, place_levels, place_words
from dmbot.memory.link_words import reads_both_ways
from dmbot.memory.models import PROPOSED, REJECTED, Relation
from dmbot.memory.ontology import Ontology, PredicateTerm

WRONG_SUBJECT, WRONG_OBJECT = "wrong_subject", "wrong_object"
TOO_MANY, CONTRADICTION = "too_many", "contradiction"
# Story memory 5a (#1267): a one-of-a-kind item in two holders' hands, a place inside itself
# (through other places), and something that is dead or destroyed acting after it ended.
HELD_TWICE, PLACE_LOOP, ACTS_AFTER_END = "held_twice", "place_loop", "acts_after_end"
FLAG_KINDS = (WRONG_SUBJECT, WRONG_OBJECT, TOO_MANY, CONTRADICTION, HELD_TWICE, PLACE_LOOP,
              ACTS_AFTER_END)  # fmt: skip

# Places nest (an inn in a town in a region): the relationship that says where something is.
NESTS = "located_in"
# The relationships where the subject DOES something, so a dead or destroyed one can't (the
# two-way ones act with both ends). Not `kin_of` (the dead still have family), not `located_in`
# (a body lies somewhere), not `owns` (a dead owner still owns, and an heir's claim is dated
# later), not `plays`. A list in code, never guessed from a relationship's
# word: a new relationship acts only when it is added here.
ACTING = frozenset(
    {"carries", "serves", "sworn_to", "member_of", "knows", "appears_in", "ally_of",
     "enemy_of", "rival_of", "leads", "mentor_of", "travels_with"}
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Problem:
    kind: str
    other_id: str | None = None  # the relationship it clashes with


def _overlap(lo1: int | None, hi1: int | None, lo2: int | None, hi2: int | None) -> bool:
    """Do two half-open spans [lo, hi) overlap? None means open-ended."""
    starts_before_other_ends = hi2 is None or lo1 is None or lo1 < hi2
    other_starts_before_this_ends = hi1 is None or lo2 is None or lo2 < hi1
    return starts_before_other_ends and other_starts_before_this_ends


def same_time(a: Relation, b: Relation) -> bool:
    """Could both facts hold at once? By session now; by game time too once both have
    one (TimeBot, Phase 4). Unknown times count as overlapping, to be safe."""
    if not _overlap(a.from_session_at, a.to_session_at, b.from_session_at, b.to_session_at):
        return False
    have_game_time = None not in (a.from_game_time, b.from_game_time)
    return not have_game_time or _overlap(
        a.from_game_time, a.to_game_time, b.from_game_time, b.to_game_time
    )


def same_pair(a: Relation, b: Relation) -> bool:
    return {a.subject_id, a.object_id} == {b.subject_id, b.object_id}


def conflicts(onto: Ontology, p: PredicateTerm, other_key: str) -> bool:
    other = onto.predicates.get(other_key)
    return other_key in p.conflicts_with or (other is not None and p.key in other.conflicts_with)


def ordered(
    p: PredicateTerm,
    subject_id: str,
    object_id: str,
    detail: str = "",
    directional: bool = False,
    both_ways: bool | None = None,
) -> tuple[str, str]:
    """Two-way relationships are stored once, smaller ID first, so "A ally B" and
    "B ally A" are the same fact (inverse consistency by construction). Not a family connection
    with a role true of one name only ("Mira is the niece of Aldric"): there the order is the
    meaning (#2457). `both_ways` is the DM's own direction for it (#4154): one the DM made one-way
    ("Wit sees Cerric as a rival") keeps its order, and one the DM made two-way is sorted."""
    if p.key == "kin_of" and directional and kin.directional(detail):
        return subject_id, object_id
    if p.key == "next_to" and next_words.one_way(detail):
        return subject_id, object_id  # "Targos is on the shore of Maer Dualdon" keeps its order
    if (p.symmetric if both_ways is None else both_ways) and object_id < subject_id:
        return object_id, subject_id
    return subject_id, object_id


def _same_order(old: Relation, new: Relation) -> bool:
    """Do the two say it between the same names in the same order? A family role the DM gave a
    direction to ("Mira is the niece of Aldric") is twin to a plain one with that role in either
    order: the plain one has no direction, so the DM's word on it wins (#2457). Two with a
    direction must agree on it."""
    if (old.subject_id, old.object_id) == (new.subject_id, new.object_id):
        return True
    # one the DM turned one way (#4154) is still the same fact as a two-way one said again, in
    # either order: the DM's direction is kept on the old row
    if any(
        reads_both_ways(r.predicate, r.detail, r.directional, r.both_ways) for r in (old, new)
    ) and (old.subject_id, old.object_id) == (new.object_id, new.subject_id):
        return True
    if new.predicate == "kin_of" and not (old.directional and new.directional):
        return (old.subject_id, old.object_id) == (new.object_id, new.subject_id)
    return False


def waiting_guess(r: Relation) -> bool:
    """A connection made only to hold what someone believes (`placing.py`): proposed, secret and
    the extractor's. No other proposal is one (the speaker scan's are not secret)."""
    return r.status == PROPOSED and r.secret and r.source == "extractor"


def duplicate_of(new: Relation, existing: Iterable[Relation]) -> Relation | None:
    """An existing fact saying the same thing at the same time: a live one if there is
    one, else a rejected one (so a fact the DM rejected doesn't quietly come back)."""
    same = [
        old
        for old in existing
        if old.id != new.id
        and old.predicate == new.predicate
        and _same_order(old, new)
        and old.detail.casefold() == new.detail.casefold()
        and same_time(old, new)
    ]
    live = [r for r in same if r.status != REJECTED]
    if live:
        return live[0]
    return same[0] if same else None


class Nesting:
    """Which places are inside which, from the live `located_in` facts between places, how big
    each place is, and the campaign's setting (#4173). Pure: built from (place, the place it is in)
    pairs, a level per place and the setting's id."""

    def __init__(
        self,
        pairs: Iterable[tuple[str, str]] = (),
        levels: Mapping[str, str] | None = None,
        setting: str | None = None,
    ) -> None:
        self._in: dict[str, set[str]] = {}
        for inner, outer in pairs:
            self._in.setdefault(inner, set()).add(outer)
        self._levels = dict(levels or {})
        self._setting = setting if setting in self._levels else None  # (a setting has a size)

    def _stated(self, place: str) -> set[str]:
        seen: set[str] = set()
        todo = [place]
        while todo:
            for outer in self._in.get(todo.pop(), ()):
                if outer not in seen:
                    seen.add(outer)
                    todo.append(outer)
        return seen

    def above(self, place: str) -> set[str]:
        """Every place this one is inside, at any depth (a loop in the data can't hang it). A place
        of a known size smaller than the campaign's setting (or inside one) is inside the setting
        too, unless something says otherwise (#4173 slice 2: "Is Targos in Faerûn?" is never
        asked): its stated places reach one as big as the setting, or the setting itself."""
        seen = self._stated(place)
        if self._setting is None or place == self._setting or self._setting in seen:
            return seen
        if place in self._stated(self._setting):  # (the setting is inside it: bad data, no loop)
            return seen
        top = place_levels.RANK[self._levels[self._setting]]
        sizes = [place_levels.RANK[lv] for p in (place, *seen) if (lv := self._levels.get(p))]
        if sizes and min(sizes) > top:  # smaller than the setting, and nothing as big says other
            return seen | {self._setting} | self._stated(self._setting)
        return seen

    def related(self, a: str, b: str) -> bool:
        """Can someone be in both at once? When one is inside the other (being in both is being in
        the smaller one), and when the two are of different sizes (#4173): a bank and the town it
        may stand in are never rivals, even before anyone says it is in that town. Two places of the
        same size, or of sizes nobody knows, are rivals unless one is inside the other."""
        if a == b or b in self.above(a) or a in self.above(b):
            return True
        if not place_levels.different(self._levels.get(a), self._levels.get(b)):
            return False
        # A place already known to be inside another place of the other one's size is elsewhere:
        # a bakery in Neverwinter and the town of Waterdeep are rivals, whatever their sizes.
        return not (self._inside_another(a, b) or self._inside_another(b, a))

    def _inside_another(self, place: str, other: str) -> bool:
        """Is `place` inside a place, not `other`, of `other`'s size?"""
        level = self._levels.get(other)
        around = self.above(other)  # (what holds `other` is not elsewhere: Toril)
        return any(
            p != other and p not in around and self._levels.get(p) == level
            for p in self.above(place)
        )

    def would_loop(self, inner: str, outer: str) -> bool:
        """Would putting `inner` in `outer` make a place part of itself?"""
        return inner == outer or inner in self.above(outer)


class Ended(NamedTuple):
    """When a dead or destroyed condition began: the session it dates from (None when nobody
    said) and when DMbot stored it."""

    session: int | None
    stored: int


def acts_after_end(onto: Ontology, fact: Relation, ended: Mapping[str, Ended]) -> bool:
    """Is a dead or destroyed entry the actor of a fact dated after it ended? Like is compared
    with like: a fact with a session date against the end's session date (the end's stored time
    when it has none), a fact with no date by when it was stored against when the end was
    stored. A fact in the same session as the end is not after it."""
    term = onto.predicates.get(fact.predicate)
    if term is None or term.key not in ACTING:
        return False
    actors = (fact.subject_id, fact.object_id) if term.symmetric else (fact.subject_id,)
    for a in actors:
        end = ended.get(a)
        if end is None:
            continue
        if fact.from_session_at is None:
            if fact.created_at > end.stored:
                return True
        elif fact.from_session_at > (end.stored if end.session is None else end.session):
            return True
    return False


def check_relation(
    onto: Ontology,
    new: Relation,
    subject_type: str,
    object_type: str,
    existing: Iterable[Relation],
    *,
    object_unique: bool = False,
    nesting: Nesting | None = None,
    ended: Mapping[str, Ended] | None = None,
) -> list[Problem]:
    """Problems with `new`, given the live facts already stored about its subject and
    object. Never raises for a rule failure: problems are returned to be flagged.
    `object_unique`: the object is an item of which only one exists. `nesting`: the places
    inside places, without `new`. `ended`: when each dead or destroyed entry ended."""
    p = onto.predicates[new.predicate]
    problems: list[Problem] = []
    if not onto.is_any(subject_type, p.subject_types):
        problems.append(Problem(WRONG_SUBJECT))
    if not onto.is_any(object_type, p.object_types):
        problems.append(Problem(WRONG_OBJECT))
    # A secret proposal is a place to hang a guess (#2412), not a claim about the story: it is
    # neither judged against facts nor are facts judged against it. Every other proposal is.
    if waiting_guess(new):
        return problems
    live = [
        r for r in existing
        if r.status != REJECTED and not waiting_guess(r) and r.id != new.id and same_time(r, new)
    ]  # fmt: skip
    if p.max_per_subject is not None:
        # Two-way facts are stored smaller ID first, so count each side either way.
        ends = (new.subject_id, new.object_id) if p.symmetric else (new.subject_id,)
        for end in ends:
            others = [
                r
                for r in live
                if r.predicate == p.key
                and not same_pair(r, new)
                and (end in (r.subject_id, r.object_id) if p.symmetric else r.subject_id == end)
            ]
            if p.key == NESTS:
                # Each word of a place tie is its own: living in one town, working in another and
                # having been seen in a third are three facts, and a past word never ends a
                # present one. A past word ("lived in", "worked in") is history and is never
                # limited at all: two past homes both stay (#3363).
                mine = place_words.slot(new.detail, subject_type)
                others = (
                    []
                    if mine[1]
                    else [r for r in others if place_words.slot(r.detail, subject_type) == mine]
                )
            if p.key == NESTS and nesting is not None:
                # Being in the inn and in the town it is in is one place, not two.
                others = [r for r in others if not nesting.related(r.object_id, new.object_id)]
            if len(others) >= p.max_per_subject:
                problems += [Problem(TOO_MANY, r.id) for r in others]
    if p.max_per_object is not None and object_unique:
        holders = [
            r
            for r in live
            if r.predicate == p.key
            and r.object_id == new.object_id
            and r.subject_id != new.subject_id
        ]
        if len(holders) >= p.max_per_object:
            problems += [Problem(HELD_TWICE, r.id) for r in holders]
    if (
        p.key == NESTS
        and nesting is not None
        and onto.is_a(subject_type, "place")
        and onto.is_a(object_type, "place")
        and nesting.would_loop(new.subject_id, new.object_id)
    ):
        problems.append(Problem(PLACE_LOOP))
    problems += [
        Problem(CONTRADICTION, r.id)
        for r in live
        if same_pair(r, new) and conflicts(onto, p, r.predicate)
    ]
    if ended and acts_after_end(onto, new, ended):
        problems.append(Problem(ACTS_AFTER_END))
    return problems
