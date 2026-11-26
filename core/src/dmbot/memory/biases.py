"""Biases DMbot read, toward a lineage or a named culture (#4229 slice 4; Supervisor's ruling on
#4229, 2026-11-21): rows of `memory_attitudes` with `status = 'read'` until the DM keeps them in
World notes.

A read row counts for nothing until then (the standing index reads confirmed rows toward an entry
only). The DM's 🚫 closes it as history, and a holder and target with any row at all (the DM's own,
a kept one, or one turned down) are never read in again, so nothing the DM answered comes back on
the next upload. Writes go through the caller's write, so each is in its one Undo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import place_peoples, standing
from dmbot.memory._changes import ATTITUDES, CULTURES, ENTITIES, Changes, Scope
from dmbot.memory.models import new_id
from dmbot.memory.ontology import Ontology

READ, CONFIRMED = "read", "confirmed"
# Who may hold a bias: a place, a group or a people (the owner's rule, 2026-11-12: a people's
# standing never flows onto a single character). Wider than the DM's own "Set attitude" (a group
# or a place, `store.ATTITUDE_BETWEEN`) on purpose: the ruling on #4229 lets a people hold one.
HOLDER_KINDS = ("place", "faction", "people")
FROM_TEXT = "text"  # (a read row's source; the DM's own rows say "dm")
# A read bias weighs as a feeling read from the story does (`standing.FEELING_WEIGHT`): kept, it is
# the DM's, but never the DM's own word on the pair, which only the standing card sets.
READ_WEIGHT = standing.FEELING_WEIGHT


@dataclass(frozen=True, slots=True)
class Bias:
    """One open bias toward a lineage or a named culture: who holds it, toward whom (a lineage key
    or a culture id), the step, and whether DMbot only read it."""

    id: str
    holder_id: str
    lineage: str
    culture_id: str
    step: str
    status: str


def _step_of(score: int) -> str:
    return min(standing.SCORE_OF, key=lambda s: abs(standing.SCORE_OF[s] - score))


def _bias(r: dict[str, Any]) -> Bias:
    return Bias(
        str(r["id"]), str(r["holder_id"]), str(r["toward_lineage"] or ""),
        str(r["toward_culture_id"] or ""), _step_of(int(r["score"])), str(r["status"]),
    )  # fmt: skip


def check_lineage(lineage: str) -> None:
    """A lineage an attitude points at is one DMbot knows (Supervisor's condition on #4229)."""
    if lineage not in place_peoples.LINEAGES:
        raise ValueError("Not a lineage DMbot knows")


def may_hold(onto: Ontology, kind: str) -> bool:
    return any(onto.is_a(kind, k) for k in HOLDER_KINDS)


async def add_read(
    w: Changes,
    onto: Ontology,
    holder_id: str,
    step: str,
    *,
    lineage: str = "",
    culture_id: str = "",
) -> bool:
    """DMbot's read of one bias, if the holder and target have no row at all yet (see above). True
    when it was written. The holder must be a live place, group or people; a culture must be the
    campaign's and not turned down."""
    if bool(lineage) == bool(culture_id):
        raise ValueError("A bias points at a lineage or a culture, one of the two")
    if step not in standing.SCORE_OF:
        raise ValueError("Not a step")
    if lineage:
        check_lineage(lineage)
        target = ("toward_lineage", lineage)
    else:
        culture = await w.get(CULTURES, culture_id)
        if culture is None or culture["status"] == "rejected":
            return False
        target = ("toward_culture_id", culture_id)
    holder = await w.get(ENTITIES, holder_id)
    if holder is None or holder["status"] == "rejected" or holder["merged_into"]:
        return False
    if not may_hold(onto, str(holder["type"])):  # (enforced here, whoever calls: never a person)
        return False
    if await w.select(
        ATTITUDES, f" AND holder_id = %s AND {target[0]} = %s", [holder_id, target[1]]
    ):
        return False
    await w.insert(
        ATTITUDES,
        {
            "id": new_id(), "holder_id": holder_id, "toward_id": None,
            "toward_lineage": lineage or None, "toward_culture_id": culture_id or None,
            "score": standing.SCORE_OF[step], "weight": READ_WEIGHT, "source": FROM_TEXT,
            "from_session_at": None, "created_at": w.now, "closed_at": None, "status": READ,
        },
    )  # fmt: skip
    return True


async def waiting(scope: Scope) -> list[Bias]:
    """The open biases DMbot read and the DM hasn't answered, oldest first."""
    rows = await scope.select(
        ATTITUDES, " AND status = 'read' AND closed_at IS NULL ORDER BY created_at, id", []
    )
    return [_bias(r) for r in rows]


async def keep(w: Changes, row_ids: Sequence[str]) -> int:
    """The DM's ✅: each becomes the DM's (`confirmed`). How many."""
    kept = 0
    for row_id in row_ids:
        row = await w.get(ATTITUDES, row_id)
        if row is not None and row["status"] == READ and row["closed_at"] is None:
            await w.update(ATTITUDES, row_id, {"status": CONFIRMED})
            kept += 1
    return kept


async def set_step(w: Changes, row_id: str, step: str) -> bool:
    """The DM's ✏️ Change: kept at this step, as the DM's (`confirmed`). False when it was gone."""
    if step not in standing.SCORE_OF:
        raise ValueError("Not a step")
    row = await w.get(ATTITUDES, row_id)
    if row is None or row["status"] != READ or row["closed_at"] is not None:
        return False
    holder = await w.get(ENTITIES, str(row["holder_id"]))  # (as the list's own rule: a live holder)
    if holder is None or holder["status"] == "rejected" or holder["merged_into"]:
        return False
    await w.update(ATTITUDES, row_id, {"status": CONFIRMED, "score": standing.SCORE_OF[step]})
    return True


async def turn_down(w: Changes, row_id: str) -> bool:
    """The DM's 🚫: closed as history, so it is never read in again. False when it was gone."""
    row = await w.get(ATTITUDES, row_id)
    if row is None or row["status"] != READ or row["closed_at"] is not None:
        return False
    await w.update(ATTITUDES, row_id, {"closed_at": w.now})
    return True


def target_words(b: Bias, culture_names: dict[str, str]) -> str:
    """How a line names the target: a lineage's plural ("orcs", "dwarves"), or the culture's
    name."""
    if b.lineage:
        return place_peoples.LINEAGES[b.lineage][1]
    return culture_names.get(b.culture_id, "")
