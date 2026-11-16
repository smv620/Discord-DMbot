"""An item has one current whereabouts: someone carries it, or a place keeps it (#3363, owner
2026-11-04: the graph shows the most recent relationship). A new "carries" ends the present
"is kept in" (it becomes "was kept in", history); a new present "is kept in" ends the present
"carries"; and when there is no order between them (one read that gives both, or an undated fact),
carrying wins, since the item was taken from where it was found. That is for a reader's facts
only: a fact the DM adds by hand is the newest thing the table knows, so it ends the other and
is never made history itself (the DM decides). An older fact read late never
ends a newer one: it becomes history itself. Ownership is a different tie and is never ended.
A hidden fact (secret, or not yet shown) never ends or rewrites a fact the players can see, so
the history cannot give it away. A mover's visits are events (#4530): the newest is where they
were last seen, every older one is "was in", worked out and never asked (`_settle_visit`). Pure:
no database, so the rules are tested alone."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory import place_words
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.memory.place_levels import MOVE, ONE, RIVALS

CARRIES, LOCATED = "carries", "located_in"
WAS_KEPT = "was kept in"


@dataclass(frozen=True, slots=True)
class Settled:
    """What a new fact does to the item's other whereabouts: `ends` maps a fact's id to the
    changes to write on it; `new_changes` are changes to write on the new fact itself (it is
    history already, or must read as it)."""

    ends: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    new_changes: Mapping[str, Any] = field(default_factory=dict)


def _present_keeping(r: Relation, item_id: str) -> bool:
    return (
        r.predicate == LOCATED
        and r.subject_id == item_id
        and place_words.slot(r.detail, "item") == (place_words.KEPT, False)
    )


def _live(r: Relation) -> bool:
    return r.status == CONFIRMED and r.to_session_at is None


def _hidden(r: Relation) -> bool:
    return r.secret or r.unrevealed


def _can_touch(new: Relation, old: Relation) -> bool:
    """A hidden fact must not change what the players can see."""
    return not _hidden(new) or _hidden(old)


def _newer(a: Relation, b: Relation) -> bool:
    """Is `a` known to be later than `b`? No date on either side is no order."""
    return a.from_session_at is not None and (
        b.from_session_at is None or a.from_session_at > b.from_session_at
    )


def _span_end(old: Relation, new: Relation) -> int | None:
    """When the old fact ended: the new one's session, when known and not before it began."""
    if new.from_session_at is None:
        return None
    if old.from_session_at is not None and new.from_session_at < old.from_session_at:
        return None
    return new.from_session_at


def _ends_at(old: Relation, new: Relation, now: int) -> int:
    """When a carrying the DM ended by hand stops: the new fact's session, else now, and never
    before the carrying began (a span must not run backwards)."""
    return max(new.from_session_at or now, old.from_session_at or 0)


# Something that moves (a character, a creature, a god, a group such as the party) is somewhere, and
# the graph shows the most recent place of each kind (owner, 2026-11-04, extended by Supervisor,
# 2026-11-05, after the party showed as "last seen in" two towns). An item has its own rule above.
_MOVERS = frozenset(
    {"character", "person", "npc", "player_character", "deity", "creature", "faction"}
)
_NEVER_ENDS = (place_words.FROM,)  # where someone began is not a place they left


def _moves(kind: str) -> bool:
    return kind in _MOVERS


_PAST_WORD = {
    place_words.LIVES: "lived in", place_words.WORKS: "worked in", place_words.LAST_SEEN: "was in"
}  # fmt: skip
_PRESENT_WORD = {"lived in": "lives in", "worked in": "works in", "was in": "was last seen in"}
# (`reopened`: back again)
# Why a mover's place ended when they moved to a bigger place elsewhere (#4173 slice 5): kept, so a
# later link that puts the smaller place inside the bigger one reopens it (Supervisor's condition)
MOVED = "moved"


def _visit_cmp(a: Relation, b: Relation, line_of: Callable[[str], int | None]) -> int:
    """Which of two visits happened later (#4530): 1 if `a`, -1 if `b`, 0 when nothing orders
    them. By session; in one session by the first line said (unknown: no order); an upload's visit
    (no session) by when it was kept (the row's `created_at`), against a session's start."""
    when_a = a.from_session_at if a.from_session_at is not None else a.created_at
    when_b = b.from_session_at if b.from_session_at is not None else b.created_at
    if not when_a or not when_b:
        return 0
    if when_a != when_b:
        return 1 if when_a > when_b else -1
    if a.from_session_at is None or b.from_session_at is None:
        return 0  # (one upload's visits: no order, the ruling's A)
    line_a, line_b = line_of(a.id), line_of(b.id)
    if line_a is None or line_b is None or line_a == line_b:
        return 0
    return 1 if line_a > line_b else -1


def _settle_visit(
    new: Relation,
    kind: str,
    others: Sequence[Relation],
    *,
    by_hand: bool,
    now: int,
    relation: Callable[[str, str], str] | None,
    line_of: Callable[[str], int | None],
) -> Settled:
    """A visit is an event, and stays true (#4530, Supervisor's ruling): the newest confirmed visit
    is where they were last seen, and every older one is history ("was in"). Never asked: by the
    session, then the later line in it; an upload's visits by when it was kept. Two visits nothing
    orders (one upload's, or one session's with no lines) are both history and neither is the
    newest (the ruling's A); the next ordered visit is. A visit confirmed late that is older than
    one kept goes straight to history and never moves the mover back. A hidden visit never turns
    a visible one into history, and a place inside the other is one place (as `_settle_mover`)."""
    ends: dict[str, dict[str, Any]] = {}
    history: list[tuple[int, bool]] = []  # (when this visit ends, whether it is a move)
    for r in others:
        if r.id == new.id or r.predicate != LOCATED or r.subject_id != new.subject_id:
            continue
        if r.status != CONFIRMED or r.object_id == new.object_id:
            continue
        word, past = place_words.slot(r.detail, kind)
        if word != place_words.LAST_SEEN:
            continue
        how = relation(r.object_id, new.object_id) if relation is not None else RIVALS
        if how == ONE:
            continue  # the inn and the town it is in: one place
        order = _visit_cmp(new, r, line_of)
        later = by_hand or order > 0
        earlier = not by_hand and order < 0
        live = _live(r) and not past
        # newer, or nothing orders the two: the other live visit is history ("was in")
        if live and not earlier and _can_touch(new, r):
            at = max(new.from_session_at or now, r.from_session_at or 0, 1)
            ends[r.id] = _ended(r, at, place_words.LAST_SEEN, MOVED if how == MOVE else None)
        # older than any visit, live or already history, or no order with a live one: this visit
        # is history too, so a late, older visit never moves the mover back. (A hidden newer one
        # never makes a visible one history: that would say they moved.)
        if (earlier or (live and not later)) and (not _hidden(r) or _hidden(new)):
            at = max(r.from_session_at or now, new.from_session_at or 0, 1)
            history.append((at, how == MOVE))
    if not history:
        return Settled(ends, {})
    # (the earliest end, as `_settle_mover` keeps it; moved when any newer visit was a move)
    new_changes: dict[str, Any] = {
        "to_session_at": min(at for at, _ in history),
        "detail": _PAST_WORD[place_words.LAST_SEEN],
    }
    if any(moved for _, moved in history):
        new_changes["end_reason"] = MOVED
    return Settled(ends, new_changes)


def _settle_mover(
    new: Relation,
    kind: str,
    others: Sequence[Relation],
    *,
    by_hand: bool,
    now: int,
    related: Callable[[str, str], bool] | None = None,
    relation: Callable[[str, str], str] | None = None,
    line_of: Callable[[str], int | None] | None = None,
) -> Settled:
    """A new present place of one kind ("lives in", "works in") ends the older present place of the
    SAME kind (never another kind), by when it happened, not when it was read: a later session
    ends an earlier one; an earlier one read late becomes history itself; with no order (a date
    missing on either side, or the same session) both stay and the one-at-a-time flag asks the
    DM, as before. A sighting is a visit and goes to `_settle_visit` (#4530). A place the DM
    added by hand is the newest. Only a confirmed fact does this: a newer place that is still
    only proposed ends nothing until the DM confirms it (`settle` is run again then). A hidden
    fact never changes what players see: it ends nothing they can see, and a visible fact never
    turns into history because of it. A place inside the other (the inn and its town) is one
    place and ends nothing. For a group only the sighting is replaced (a guild based in two
    towns is the DM's to settle). The ended fact stays, as history, with its end; the DM's Undo
    brings it back."""
    word, past = place_words.slot(new.detail, kind)
    if past or word in _NEVER_ENDS or (kind == "faction" and word != place_words.LAST_SEEN):
        return Settled()
    if word == place_words.LAST_SEEN:
        if relation is None and related is not None:
            relation = lambda a, b: ONE if related(a, b) else RIVALS  # noqa: E731
        return _settle_visit(
            new, kind, others, by_hand=by_hand, now=now, relation=relation,
            line_of=line_of or (lambda _id: None),
        )  # fmt: skip
    ends: dict[str, dict[str, Any]] = {}
    new_changes: dict[str, Any] | None = None
    for r in others:
        if r.id == new.id or r.predicate != LOCATED or r.subject_id != new.subject_id:
            continue
        if not _live(r) or r.object_id == new.object_id:
            continue
        if place_words.slot(r.detail, kind) != (word, False):
            continue  # another kind of place tie, or one that is past already
        if relation is not None:
            how = relation(r.object_id, new.object_id)
        elif related is not None:
            how = ONE if related(r.object_id, new.object_id) else RIVALS
        else:
            how = RIVALS
        if how == ONE:
            continue  # the inn and the town it is in: one place, not two
        reason = MOVED if how == MOVE else None
        if by_hand:
            if _can_touch(new, r):
                ends[r.id] = _ended(
                    r, max(new.from_session_at or now, r.from_session_at or 0), word, reason
                )
        elif _dated_after(new, r):
            if _can_touch(new, r):
                ends[r.id] = _ended(r, new.from_session_at or now, word, reason)
        elif how == MOVE and not _dated_after(r, new):
            continue  # (a move with no order is no conflict: both stay, nothing is flagged)
        elif _dated_after(r, new) and (not _hidden(r) or _hidden(new)):
            # The older fact was read late: it is history, and the newer place stands. (A hidden
            # newer place never turns a visible one into history: that would say the party moved.)
            # (Keep looping: an even older place still ends, whatever order the rows come in.)
            changes: dict[str, Any] = {"to_session_at": r.from_session_at}
            if word in _PAST_WORD:
                changes["detail"] = _PAST_WORD[word]
            if reason is not None:
                changes["end_reason"] = reason
            if new_changes is None or changes["to_session_at"] < new_changes["to_session_at"]:
                new_changes = changes
    return Settled(ends, new_changes or {})


def _dated_after(a: Relation, b: Relation) -> bool:
    """Is `a` known to be later than `b`? Both must be dated: with a date missing on either side
    there is no order, and neither ends the other."""
    return (
        a.from_session_at is not None
        and b.from_session_at is not None
        and a.from_session_at > b.from_session_at
    )


def _ended(old: Relation, at: int, word: str, reason: str | None = None) -> dict[str, Any]:
    """The change that makes an old place history: its end (never before it began), where the way
    has a past wording, that ("lived in"), and why, when they moved (`MOVED`)."""
    changes: dict[str, Any] = {"to_session_at": max(at, old.from_session_at or 0)}
    if word in _PAST_WORD:
        changes["detail"] = _PAST_WORD[word]
    if reason is not None:
        changes["end_reason"] = reason
    return changes


def reopened(detail: str) -> dict[str, Any]:
    """The change that reopens a place a mover left by `MOVED` (#4173 slice 5): no end, no reason,
    and the present wording back ("lives in")."""
    changes: dict[str, Any] = {"to_session_at": None, "end_reason": None}
    if detail in _PRESENT_WORD:
        changes["detail"] = _PRESENT_WORD[detail]
    return changes


def settle(
    new: Relation,
    subject_kind: str,
    object_kind: str,
    others: Sequence[Relation],
    *,
    by_hand: bool = False,
    now: int = 0,
    related: Callable[[str, str], bool] | None = None,
    relation: Callable[[str, str], str] | None = None,
    line_of: Callable[[str], int | None] | None = None,
) -> Settled:
    """What a new, confirmed fact does to the live facts already known about its item.
    `by_hand`: the DM added it themselves, so it counts as the newest of all (`now` dates it).
    `related(a, b)`: whether one place is inside the other (a mover in the inn and in the town it is
    in is in one place, not two: it ends nothing). `line_of(id)`: the first transcript line of the
    claim that made a fact, to order two visits in one session (#4530)."""
    if new.status != CONFIRMED or new.to_session_at is not None:
        return Settled()
    if (
        new.predicate == LOCATED
        and _moves(subject_kind)
        and not _moves(object_kind)
        and (object_kind != "item")
    ):
        return _settle_mover(
            new, subject_kind, others, by_hand=by_hand, now=now, related=related,
            relation=relation, line_of=line_of,
        )  # fmt: skip
    if new.predicate == CARRIES and object_kind == "item":
        ends: dict[str, dict[str, Any]] = {}
        for r in others:
            if r.id == new.id or not _live(r) or not _present_keeping(r, new.object_id):
                continue
            if not by_hand and _newer(r, new):
                # The keeping is later than this carrying (a carrying read late): the carrying
                # is history, and the keeping stands.
                return Settled(new_changes={"to_session_at": r.from_session_at})
            if not _can_touch(new, r):
                continue
            changes: dict[str, Any] = {"detail": WAS_KEPT}  # (history, kept as it was)
            if (at := _span_end(r, new)) is not None:
                changes["to_session_at"] = at
            ends[r.id] = changes
        return Settled(ends)
    if subject_kind == "item" and _present_keeping(new, new.subject_id):
        ends = {}
        for r in others:
            if r.id == new.id or not _live(r):
                continue
            if r.predicate != CARRIES or r.object_id != new.subject_id:
                continue
            if by_hand or _newer(new, r):
                if _can_touch(new, r):
                    ends[r.id] = {"to_session_at": _ends_at(r, new, now)}
            elif not _hidden(r) or _hidden(new):
                # No order, or the carrying is later: carrying wins and the place is history.
                # (A hidden carrying never turns a visible keeping into history: that would say
                # the item moved.)
                return Settled(new_changes={"detail": WAS_KEPT})
        return Settled(ends)
    return Settled()
