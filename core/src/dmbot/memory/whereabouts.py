"""An item has one current whereabouts: someone carries it, or a place keeps it (#3363, owner
2026-11-04: the graph shows the most recent relationship). A new "carries" ends the present
"is kept in" (it becomes "was kept in", history); a new present "is kept in" ends the present
"carries"; and when one read gives both with no order (a lore document that says where it was
found and who has it now), carrying wins, since the item was taken from there. Ownership is a
different tie and is never ended here. Pure: no database, so the rules are tested alone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory import place_words
from dmbot.memory.models import CONFIRMED, Relation

CARRIES, LOCATED = "carries", "located_in"
ITEM = "item"
WAS_KEPT = "was kept in"


@dataclass(frozen=True, slots=True)
class Settled:
    """What a new fact does to the item's other whereabouts: `ends` maps a fact's id to the
    changes to write on it; `new_detail` is a different detail for the new fact itself."""

    ends: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    new_detail: str | None = None


def _present_keeping(r: Relation, item_id: str) -> bool:
    return (
        r.predicate == LOCATED
        and r.subject_id == item_id
        and place_words.slot(r.detail, ITEM) == (place_words.KEPT, False)
    )


def _live(r: Relation) -> bool:
    return r.status == CONFIRMED and r.to_session_at is None


def _when(old: Relation, new: Relation) -> int | None:
    """When the old fact ended: the new one's session, if it is known and not before the old
    one began (a span must not run backwards)."""
    if new.from_session_at is None:
        return None
    if old.from_session_at is not None and new.from_session_at < old.from_session_at:
        return None
    return new.from_session_at


def settle(
    new: Relation, subject_kind: str, object_kind: str, others: Sequence[Relation]
) -> Settled:
    """What a new, confirmed fact does to the live facts already known about its item."""
    if new.status != CONFIRMED or new.to_session_at is not None:
        return Settled()
    if new.predicate == CARRIES and object_kind == ITEM:
        ends: dict[str, dict[str, Any]] = {}
        for r in others:
            if r.id != new.id and _live(r) and _present_keeping(r, new.object_id):
                changes: dict[str, Any] = {"detail": WAS_KEPT}  # (history, kept as it was)
                if (at := _when(r, new)) is not None:
                    changes["to_session_at"] = at
                ends[r.id] = changes
        return Settled(ends)
    if _present_keeping(new, new.subject_id) and subject_kind == ITEM:
        carried = [
            r for r in others
            if r.id != new.id and _live(r) and r.predicate == CARRIES
            and r.object_id == new.subject_id
        ]  # fmt: skip
        ends = {}
        for r in carried:
            newer = new.from_session_at is not None and (
                r.from_session_at is None or new.from_session_at > r.from_session_at
            )
            if not newer:
                # No order between them (the same read, or an older sighting): carrying wins
                # and the place is history.
                return Settled(new_detail=WAS_KEPT)
            ends[r.id] = {"to_session_at": new.from_session_at}
        return Settled(ends)
    return Settled()
