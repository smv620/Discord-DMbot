"""Family links worked out from stated ones, kept as suggestions (#2959 item 2, #2960; Supervisor's
ruling 2026-10-30). The rules are `kin_derive` (pure); this is the part that reads the campaign and
writes the results.

Every result is a PROPOSED `kin_of` connection with source `worked_out`: never confirmed on the DM's
behalf, shown to the DM only ("worked out from X and Y, not stated"), never to players. A confirm
makes it stand on its own; a rejection is final (it is never made again). A proposed one is taken
away again when one of the links it came from is removed or rejected, so recomputing is always
safe. A link that is secret in either source is secret here. Counts only leave this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.memory import kin, kin_derive
from dmbot.memory._changes import ENTITIES, RELATIONS, Changes, Scope
from dmbot.memory.knowers import entry_shown_end
from dmbot.memory.models import CONFIRMED, PROPOSED, WORKED_OUT_SOURCE

KIN = "kin_of"


@dataclass(frozen=True, slots=True)
class Reworked:
    """What one recompute did, as counts only."""

    added: int
    removed: int
    waiting: int


def _stated(
    rows: list[dict[str, object]], entity_ids: set[str] | None = None
) -> list[kin_derive.Stated]:
    """The confirmed family links a person stated, as "a is the role of b". A role that has a
    direction but was stored without one is ambiguous, so it is not used; a worked-out link is never
    a source for another."""
    out: list[kin_derive.Stated] = []
    for r in rows:
        if r["status"] != CONFIRMED or r["source"] == WORKED_OUT_SOURCE:
            continue
        if entity_ids is not None and not {str(r["subject_id"]), str(r["object_id"])} <= entity_ids:
            continue  # an end was removed or merged away
        role = kin.role_of(str(r["detail"] or ""))
        if role is None or not (r["directional"] or role in kin.MUTUAL):
            continue
        out.append(kin_derive.Stated(str(r["id"]), str(r["subject_id"]), role, str(r["object_id"])))
    return out


async def sources_of(w: Changes) -> dict[tuple[str, str, str], tuple[str, str]]:
    """(a, role, b) of every link that can be worked out now -> the ids of the two links it follows
    from."""
    rows = await w.select(RELATIONS, " AND predicate = %s", [KIN])
    return {(d.a_id, d.role, d.b_id): d.source_ids for d in kin_derive.derive(_stated(rows))}


def kept_from_players(
    source_ids: tuple[str, ...],
    by_id: dict[str, dict[str, object]],
    entities: dict[str, dict[str, object]],
) -> bool:
    """Whether a link worked out from these sources must start secret: a source is secret or not
    yet said at the table, or an end of either (the middle name too) is kept from the players.
    Confirming it would otherwise hand the players what the hidden source still keeps from them."""
    for i in source_ids:
        row = by_id.get(i)
        if row is None or row["secret"] or row["unrevealed"]:
            return True
        for end in (str(row["subject_id"]), str(row["object_id"])):
            entity = entities.get(end)
            if entity is None or not entry_shown_end(entity):
                return True
    return False


async def rework(
    w: Changes,
    add: Callable[[str, str, str, bool], Awaitable[None]],
    delete: Callable[[str], Awaitable[None]],
) -> Reworked:
    """Make the suggestions match the stated links now. `add(a, role, b, secret)` writes one
    proposed link and `delete(id)` takes one away (the store's own checked paths, which also clear
    the flag rows that point at it). Idempotent."""
    rows = await w.select(RELATIONS, " AND predicate = %s", [KIN])
    entities = {str(e["id"]): e for e in await w.select(ENTITIES, "", [])}
    derived = kin_derive.derive(_stated(rows, set(entities)))
    by_id = {str(r["id"]): r for r in rows}
    held = {
        (str(r["subject_id"]), kin.role_of(str(r["detail"] or "")), str(r["object_id"])): r
        for r in rows
        if r["source"] == WORKED_OUT_SOURCE
    }
    any_state = {
        (str(r["subject_id"]), kin.role_of(str(r["detail"] or "")), str(r["object_id"]))
        for r in rows
    }
    wanted: set[tuple[str, str, str]] = {(d.a_id, d.role, d.b_id) for d in derived}
    added = removed = 0
    for d in derived:
        key: tuple[str, str, str] = (d.a_id, d.role, d.b_id)
        if key in any_state:  # stated, suggested before, confirmed or rejected: leave as it is
            continue
        secret = kept_from_players(d.source_ids, by_id, entities)
        await add(d.a_id, d.role, d.b_id, secret)
        added += 1
    sources: dict[tuple[str, str, str], tuple[str, str]] = {
        (d.a_id, d.role, d.b_id): d.source_ids for d in derived
    }
    for held_key, row in held.items():
        if row["status"] != PROPOSED:
            continue
        if held_key not in wanted:
            await delete(str(row["id"]))  # a source went: the suggestion goes
            removed += 1
        elif not row["secret"] and kept_from_players(sources[held_key], by_id, entities):
            # a source became secret, unrevealed or hidden after the suggestion: it must not be
            # shown as plain once confirmed (it only ever goes up, never back down)
            await w.update(RELATIONS, str(row["id"]), {"secret": True})
    waiting = sum(1 for row in held.values() if row["status"] == PROPOSED) - removed + added
    return Reworked(added, removed, max(waiting, 0))


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One waiting suggestion, as the DM's card needs it: names, and the two links it follows from
    in plain words ("Aldric is the mother of Bryn")."""

    relation_id: str
    subject: str
    role: str
    object: str
    from_words: tuple[str, str]
    secret: bool


def _sentence(names: dict[str, str], row: dict[str, object]) -> str:
    role = kin.role_of(str(row["detail"] or "")) or "relative"
    who = names.get(str(row["subject_id"]), "?")
    whom = names.get(str(row["object_id"]), "?")
    return f"{who} is the {role} of {whom}"


async def waiting(scope: Scope) -> list[Suggestion]:
    """The proposed worked-out links with the links they come from, oldest first."""
    rows = await scope.select(RELATIONS, " AND predicate = %s ORDER BY created_at, id", [KIN])
    mine = [r for r in rows if r["source"] == WORKED_OUT_SOURCE and r["status"] == PROPOSED]
    if not mine:
        return []
    derived: dict[tuple[str, str, str], tuple[str, ...]] = {
        (d.a_id, d.role, d.b_id): tuple(d.source_ids) for d in kin_derive.derive(_stated(rows))
    }
    by_id = {str(r["id"]): r for r in rows}
    ends = sorted({str(x) for r in rows for x in (r["subject_id"], r["object_id"])})
    names = {
        str(e["id"]): str(e["name"])
        for e in await scope.select(ENTITIES, " AND id = ANY(%s)", [ends])
    }
    out: list[Suggestion] = []
    for r in mine:
        role = kin.role_of(str(r["detail"] or ""))
        key = (str(r["subject_id"]), role or "", str(r["object_id"]))
        sources = derived.get(key)
        if sources is None or role is None:
            continue  # its sources went: the next recompute takes it away
        out.append(
            Suggestion(
                str(r["id"]),
                names.get(key[0], "?"),
                role,
                names.get(key[2], "?"),
                (_sentence(names, by_id[sources[0]]), _sentence(names, by_id[sources[1]])),
                bool(r["secret"]),
            )
        )
    return out
