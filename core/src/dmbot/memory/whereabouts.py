"""An item has one current whereabouts: someone carries it, or a place keeps it (#3363, owner
2026-11-04: the graph shows the most recent relationship). A new "carries" ends the present
"is kept in" (it becomes "was kept in", history); a new present "is kept in" ends the present
"carries"; and when there is no order between them (one read that gives both, or an undated fact),
carrying wins, since the item was taken from where it was found. That is for a reader's facts
only: a fact the DM adds by hand is the newest thing the table knows, so it ends the other and
is never made history itself (the DM decides). An older fact read late never
ends a newer one: it becomes history itself. Ownership is a different tie and is never ended.
A hidden fact (secret, or not yet shown) never ends or rewrites a fact the players can see, so
the history cannot give it away. Pure: no database, so the rules are tested alone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory import place_words
from dmbot.memory.models import CONFIRMED, Relation

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


def settle(
    new: Relation,
    subject_kind: str,
    object_kind: str,
    others: Sequence[Relation],
    *,
    by_hand: bool = False,
    now: int = 0,
) -> Settled:
    """What a new, confirmed fact does to the live facts already known about its item.
    `by_hand`: the DM added it themselves, so it counts as the newest of all (`now` dates it)."""
    if new.status != CONFIRMED or new.to_session_at is not None:
        return Settled()
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
