"""A one-of-a-kind item changing hands (#4115; Supervisor's ruling and the owner's addition,
2026-11-10).

When two different holders each have a current `owns` (or each a current `carries`) of a
one-of-a-kind item, DMbot asks the DM once: "Does A still have it?". "No, not any more" ends A's
ties to the item as history (`to_session_at`, plus why when the story said it: `end_reason`); "Yes,
both have it" is remembered so the question is never asked again for that item and those two.
DMbot never ends a tie on its own, and it ends only `owns` and `carries`: "found" and every other
event-like tie happened and stay true (the owner: "would still have 'found' originally").

Owning and holding by two different people is no conflict (a thief has what someone else owns), so
only two holders of the same tie are asked about.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import FLAGS, RELATIONS, Changes, Scope
from dmbot.memory.checks import HELD_TWICE
from dmbot.memory.models import CONFIRMED, END_REASONS, MemoryRuleError, Relation
from dmbot.memory.ontology import Ontology
from dmbot.memory.states import entity_is_unique
from dmbot.memory.whereabouts import CARRIES

OWNS = "owns"
# The only ties a hand-over ends. Anything else (a "found", a "made") is something that happened.
HELD = (OWNS, CARRIES)
GONE = "That has changed since. Nothing was saved."


@dataclass(frozen=True, slots=True)
class Question:
    """Two holders of the same one-of-a-kind item, by the same tie. Ids only."""

    item_id: str
    older_id: str  # A: had it first, the one asked about ("Does A still have it?")
    newer_id: str  # B: the DM said has it now
    predicate: str  # the tie both hold: "owns" or "carries"
    older_relation_id: str
    newer_relation_id: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Which question this is, whichever tie rows say it (a press checks it still waits)."""
        return (self.item_id, self.older_id, self.newer_id, self.predicate)


def _order(r: dict[str, Any]) -> tuple[int, int, str]:
    """Which tie is newer: the session it began in, then when DMbot wrote it."""
    return (r["from_session_at"] or 0, r["created_at"], r["id"])


def pair(a: str, b: str) -> tuple[str, str]:
    """A pair of holders in one order (the table's CHECK)."""
    return (a, b) if a < b else (b, a)


async def waiting(scope: Scope, onto: Ontology) -> list[Question]:
    """The questions waiting for the DM, oldest item first. A pair the DM answered "Yes, both" is
    never asked again; a holder that is not confirmed, or an item that is not one of a kind, is
    never asked about."""
    cur = await scope.conn.execute(
        "SELECT r.id, r.subject_id, r.predicate, r.object_id, r.from_session_at, r.created_at,"
        " e.id AS item_id, e.type, e.name, e.status, e.unique_item"
        " FROM memory_relations r"
        " JOIN memory_entities e ON e.guild_id = r.guild_id AND e.campaign_id = r.campaign_id"
        "  AND e.id = r.object_id"
        " JOIN memory_entities s ON s.guild_id = r.guild_id AND s.campaign_id = r.campaign_id"
        "  AND s.id = r.subject_id AND s.status = 'confirmed'"
        " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.predicate = ANY(%s)"
        "  AND r.status = 'confirmed' AND r.to_session_at IS NULL AND e.status = 'confirmed'"
        "  AND EXISTS (SELECT 1 FROM memory_relations o WHERE o.guild_id = r.guild_id"
        "   AND o.campaign_id = r.campaign_id AND o.object_id = r.object_id"
        "   AND o.predicate = r.predicate AND o.subject_id <> r.subject_id"
        "   AND o.status = 'confirmed' AND o.to_session_at IS NULL)",
        (*scope.ids, list(HELD)),
    )
    rows = await cur.fetchall()
    both = await _both(scope)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if onto.is_a(r["type"], "item") and entity_is_unique(r | {"id": r["item_id"]}):
            groups.setdefault((r["item_id"], r["predicate"]), []).append(r)
    out: list[Question] = []
    for (item, predicate), ties in sorted(groups.items()):
        ties.sort(key=_order)
        newer = ties[-1]  # the newest holder is B; each older holder is asked about once
        seen: set[str] = {newer["subject_id"]}
        for older in reversed(ties[:-1]):
            a = older["subject_id"]
            if a in seen or (item, *pair(a, newer["subject_id"])) in both:
                continue
            seen.add(a)
            out.append(Question(item, a, newer["subject_id"], predicate, older["id"], newer["id"]))
    return out


async def _both(scope: Scope) -> set[tuple[str, str, str]]:
    cur = await scope.conn.execute(
        "SELECT item_id, first_id, second_id FROM memory_hand_over_both"
        " WHERE guild_id = %s AND campaign_id = %s",
        scope.ids,
    )
    return {(r["item_id"], r["first_id"], r["second_id"]) for r in await cur.fetchall()}


def ends_at(older_from: int | None, newer_from: int | None, now: int) -> int:
    """When the older holder's tie stops: the session the new one began in, else now, and never
    before the old tie began (a span must not run backwards; `whereabouts._ends_at`'s rule)."""
    return max(newer_from or now, (older_from or 0) + 1)


async def hand_over(
    w: Changes,
    item_id: str,
    holder_id: str,
    *,
    at: int,
    reason: str | None,
    predicates: Sequence[str] = HELD,
) -> list[str]:
    """End `holder_id`'s current ties to the item among `predicates` (only `owns` and `carries`)
    at `at`, with `reason` (one of END_REASONS, or None when the story didn't say). Through the
    change log, so Undo reopens them. Returns the ids of the ties it ended."""
    if reason is not None and reason not in END_REASONS:
        raise ValueError(f"Not an end reason: {reason}")
    if not predicates or any(p not in HELD for p in predicates):
        raise ValueError("A hand-over ends only owns and carries.")  # never a "found" (#4115)
    ended: list[str] = []
    for r in await w.select(
        RELATIONS,
        " AND subject_id = %s AND object_id = %s AND predicate = ANY(%s)"
        " AND status = 'confirmed' AND to_session_at IS NULL",
        [holder_id, item_id, list(predicates)],
    ):
        end = max(at, (r["from_session_at"] or 0) + 1)
        await w.update(RELATIONS, r["id"], {"to_session_at": end, "end_reason": reason})
        ended.append(r["id"])
    if ended:  # an ended tie holds nothing: its "held twice" alerts no longer hold
        for flag in await w.select(
            FLAGS,
            " AND kind = %s AND status = 'open' AND (relation_id = ANY(%s) OR other_id = ANY(%s))",
            [HELD_TWICE, ended, ended],
        ):
            await w.update(FLAGS, flag["id"], {"status": "resolved"})
    return ended


async def answer(w: Changes, onto: Ontology, question: Question, *, still_has: bool) -> list[str]:
    """The DM's answer to one question, inside one write (one Undo). "No, not any more" ends A's
    tie of the question's kind, and with an `owns` A's `carries` too (what A no longer owns, A no
    longer holds); "Yes, both have it" remembers the pair. Refused when the question no longer
    waits (answered elsewhere, a tie ended or changed meanwhile). Returns the ties it ended."""
    if question.key not in {q.key for q in await waiting(w, onto)}:
        raise MemoryRuleError(GONE)
    if still_has:
        first, second = pair(question.older_id, question.newer_id)
        await w.conn.execute(
            "INSERT INTO memory_hand_over_both (guild_id, campaign_id, item_id, first_id,"
            " second_id, created_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (*w.ids, question.item_id, first, second, w.now),
        )
        return []
    newer = await w.get(RELATIONS, question.newer_relation_id)
    older = await w.get(RELATIONS, question.older_relation_id)
    if newer is None or older is None or newer["status"] != CONFIRMED:
        raise MemoryRuleError(GONE)
    at = ends_at(older["from_session_at"], newer["from_session_at"], w.now)
    kinds = HELD if question.predicate == OWNS else (CARRIES,)
    return await hand_over(w, question.item_id, question.older_id, at=at, reason=None,
                           predicates=kinds)  # fmt: skip


# The history line's words (#4115, through the ux-critic): plain verbs, past tense, oldest first,
# never a date. `{a}` had it; `{b}` had it next.
_WITH_NEXT = {
    "sold": "{a} sold it to {b}",
    "gave": "{a} gave it to {b}",
    "traded": "{a} traded it to {b}",
}
_ALONE = {
    "sold": "{a} sold it",
    "gave": "{a} gave it away",
    "traded": "{a} traded it away",
    "stolen": "it was stolen from {a}",
    "lost": "{a} lost it",
}
USED_TO = "{a} used to have it"
HAS_NOW = "{a} has it now"
STILL_OWNS = {
    "stolen": "it was stolen from {a}, who still owns it",
    "lost": "{a} lost it but still owns it",
}


def history(ties: Sequence[Relation], name: Callable[[str], str | None]) -> list[str]:
    """How an item changed hands, oldest first, from its confirmed `owns` and `carries` ties: one
    step a holder ("Claude sold it to Belleros", "Belleros has it now"). Empty unless one of them
    has ended (an item nobody handed over has no history to tell). `name` gives a holder's name as
    the reader may see it, or None to leave that holder out (a secret, a name they don't know)."""
    held = [t for t in ties if t.predicate in HELD and t.status == CONFIRMED]
    if not any(t.to_session_at is not None for t in held):
        return []
    by_holder: dict[str, list[Relation]] = {}
    for t in sorted(held, key=lambda t: (t.from_session_at or 0, t.created_at, t.id)):
        by_holder.setdefault(t.subject_id, []).append(t)
    holders = [(h, n) for h, ts in by_holder.items() if (n := name(h)) is not None]
    steps: list[str] = []
    for i, (holder, shown) in enumerate(holders):
        mine = by_holder[holder]
        if any(t.to_session_at is None for t in mine):
            # A theft or a loss ends what the victim held, never what they own (Supervisor's
            # ruling): they still own it, and the line says so.
            taken = [
                t.end_reason
                for t in mine
                if t.predicate == CARRIES and t.end_reason in STILL_OWNS
                and not any(o.predicate == CARRIES and o.to_session_at is None for o in mine)
            ]  # fmt: skip
            steps.append(
                STILL_OWNS[taken[-1]].format(a=shown) if taken else HAS_NOW.format(a=shown)
            )
            continue
        last = max(t.to_session_at or 0 for t in mine)
        reasons = [t.end_reason for t in mine if t.to_session_at == last and t.end_reason]
        reason = reasons[0] if reasons else None
        after = holders[i + 1][1] if i + 1 < len(holders) else None
        if reason in _WITH_NEXT and after is not None:
            steps.append(_WITH_NEXT[reason].format(a=shown, b=after))
        elif reason is not None:
            steps.append(_ALONE[reason].format(a=shown))
        else:
            steps.append(USED_TO.format(a=shown))
    return steps
