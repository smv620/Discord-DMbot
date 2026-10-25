"""The DM places a reading as a belief (docs/STORY_MEMORY.md, section 9a; #2412).

A reading is a claim that carries what someone believes (`readings.py`), not a fact to keep:
`ClaimStore.confirm` refuses it. This is its own step, "place it", and the DM's alone. It writes
beliefs and, where one is needed to hang a belief on, a *proposed* connection:

- **A theory, an accusation, a dodge under a spell** (the player character's own view, or the
  party's when the table agreed): the belief goes on the connection the words make. If that
  connection is already there (confirmed or waiting), the belief is added to it and the connection
  is left alone. If there is none, or only one the DM turned down, a new *proposed*, secret
  connection is made to hold it: no reader shows a proposed connection to the players, and the
  party's belief short of 1.0 is not "the party knows it".
- **A reveal or a confession under a spell**: the players saw it, so the connection is the DM's to
  keep, shown. It is confirmed (not secret) by this step, and the party's belief gets the
  reading's own status and the claim as its evidence. A secret connection is never revealed here.
- **A frame-up**: the false public statement gets The truth's 0.0 False Evidence; the new secret
  plot ("Fabio framed Lucian") is kept as a secret connection of the DM's.

Nothing here ever changes an existing connection's status, secret flag or words, and a rejected
connection is never brought back. The belief's time is the session the claim was made in; a later
belief for the same view replaces it from its own session (a new row, history kept). Everything is
one write with one Undo, and the claim keeps which connection it became (`fact_id`).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.memory import beliefs, evidence
from dmbot.memory._changes import ALIASES, CLAIMS, ENTITIES, PERSPECTIVES, RELATIONS, Changes
from dmbot.memory.claims import (
    GONE,
    INJECTION_REASON,
    NAME_FIRST,
    ODD_NOW,
    PLAN_REASON,
    STOPPED_NOW,
    Claim,
    ClaimStore,
    Extracted,
    claim_of,
    condition_of,
    injection_like,
    names_of,
    place_relationship,
)
from dmbot.memory.models import (
    ACCUSATION,
    CLAIM_CONFIRMED,
    CLAIM_DROPPED,
    CLAIM_PROPOSED,
    CONFIRMED,
    DM,
    EVASION_UNDER_MAGIC,
    FALSE_EVIDENCE,
    HOW_PLAN,
    MAGICALLY_VERIFIED,
    NPC_VIEW,
    PARTY,
    PC_VIEW,
    PROPOSED,
    REVEALED_FACT,
    SECRET_PLOT,
    THEORY,
    TRUTH,
    Belief,
    Flag,
    MemoryRuleError,
    new_id,
)
from dmbot.memory.store import _load_ontology, flag_kept

NOT_A_READING = "That is a fact to keep, not a belief to place."
A_STATE = "That is how something is, not a connection, so there is nothing to place."
UNKNOWN_CHARACTER = "DMbot doesn't know which character believes that."
PC_PREFIX = "pc:"
HOST_CONFIDENCE = 0.5  # a proposed connection that only holds beliefs: nobody has kept it


def _checked(stored: Mapping[str, Any]) -> dict[str, Any]:
    """The stored reading, which is untrusted (a backup file can bring any JSON), as a view, a
    status and a confidence that go together, or the plain refusal. Each view may hold only its
    own kinds of belief (the same table as `beliefs.place_belief`, checked here before anything is
    opened, so a wrong pair is never half-placed)."""
    view, status, confidence = stored.get("view"), stored.get("status"), stored.get("confidence")
    if (
        not isinstance(view, str)
        or not isinstance(status, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
    ):
        raise MemoryRuleError(beliefs.BAD_READING)
    try:
        c = float(confidence)
    except OverflowError:  # (a whole number too big for a float)
        raise MemoryRuleError(beliefs.BAD_READING) from None
    if not 0.0 <= c <= 1.0:  # (also catches NaN)
        raise MemoryRuleError(beliefs.BAD_READING)
    guess = status in (THEORY, ACCUSATION) and 0.0 < c < 1.0
    ok = {
        TRUTH: (status == FALSE_EVIDENCE and c == 0.0) or (status == SECRET_PLOT and c == 1.0),
        PARTY: guess
        or (status == EVASION_UNDER_MAGIC and 0.0 < c < 1.0)
        or (status in (REVEALED_FACT, MAGICALLY_VERIFIED) and c == 1.0),
    }.get(view, guess and view.startswith(PC_PREFIX) and len(view) > len(PC_PREFIX))
    if not ok:
        raise MemoryRuleError(beliefs.BAD_READING)
    return {"view": view, "status": status, "confidence": c}


@dataclass(frozen=True, slots=True)
class Placed:
    claim: Claim
    relation_id: str  # the connection the belief hangs on
    belief: Belief | None  # None: the DM's view already held it (a kept secret plot)
    flags: tuple[Flag, ...]
    batch: int | None = None  # the change-log batch, for the Undo


async def place(
    claims: ClaimStore,
    guild_id: int,
    campaign_id: str,
    claim_id: str,
    *,
    opted_in: Collection[int],
) -> Placed:
    """The DM places a reading (see the module's note). The same early exits as `confirm` (the
    question is gone, a plan, an instruction, someone stopped being recorded), the names asked
    again against what is known now, then one write."""
    store = claims._store  # (this module is the claims' second half)
    claim = await claims.get(guild_id, campaign_id, claim_id)
    if claim is None or claim.status != CLAIM_PROPOSED:
        raise MemoryRuleError(GONE)
    if claim.reading is None:
        raise MemoryRuleError(NOT_A_READING)
    wording = Extracted(
        claim.subject, claim.relationship, claim.object, claim.details, claim.lines, claim.how
    )
    if claim.how == HOW_PLAN:
        raise MemoryRuleError(PLAN_REASON)
    if condition_of(wording) is not None:
        raise MemoryRuleError(A_STATE)
    if injection_like(wording):
        async with store._write(guild_id, campaign_id, "extractor") as w:
            await w.update(CLAIMS, claim_id, {"status": CLAIM_DROPPED, "reason": INJECTION_REASON})
        raise MemoryRuleError(ODD_NOW)
    if not claim.speaker_ids or any(s not in opted_in for s in claim.speaker_ids):
        async with store._write(guild_id, campaign_id, DM) as w:
            await w.delete(CLAIMS, claim_id)
            await evidence.placeholder_claims(w.conn, guild_id, [claim_id])
        raise MemoryRuleError(STOPPED_NOW)
    reading = _checked(claim.reading)
    async with store._read(guild_id, campaign_id) as scope:
        onto = await _load_ontology(scope)
        entities = await scope.select(
            ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
        )
        aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
    names = names_of(entities, aliases)
    waiting = {str(e["id"]) for e in entities if e["status"] == PROPOSED}
    ends: list[tuple[str, str]] = []
    for name in (claim.subject, claim.object):
        found = names.one(name)
        if isinstance(found, str):
            raise MemoryRuleError(f"Can't write that now: {found}.")
        if found[0] in waiting:
            raise MemoryRuleError(NAME_FIRST)
        ends.append(found)
    who, other = ends
    term, from_object, label = place_relationship(onto, claim.relationship, who[1], other[1])
    subject_id, object_id = (other[0], who[0]) if from_object else (who[0], other[0])
    holder: str | None = None  # the character whose point of view it is
    view = str(reading["view"])
    if view.startswith(PC_PREFIX):
        found = names.one(view[len(PC_PREFIX) :])
        if isinstance(found, str) or found[0] in waiting or found[0] not in names.roles:
            raise MemoryRuleError(UNKNOWN_CHARACTER)
        holder = found[0]
    async with store._write(guild_id, campaign_id, DM) as w:
        current = await w.get(CLAIMS, claim_id)
        if current is None or current["status"] != CLAIM_PROPOSED:
            raise MemoryRuleError(GONE)
        relation_id, belief, flags = await _place_in(
            store, w, claim, reading, subject_id, term.key, object_id,
            label, holder, names.roles,
        )  # fmt: skip
        row = await w.update(
            CLAIMS,
            claim_id,
            {"status": CLAIM_CONFIRMED, "decided_at": w.now, "fact_id": relation_id},
        )
        batch = w.batch
    return Placed(claim_of(row), relation_id, belief, tuple(flags), batch)


async def _view_id(
    w: Changes, view: str, holder: str | None, roles: Mapping[str, tuple[str | None, int | None]]
) -> str:
    """The perspective a reading's view means; a character gets theirs when first needed (a
    player's character holds beliefs as one, any other as an NPC)."""
    ids = await beliefs.perspective_ids(w)
    if view == PARTY:
        return ids[PARTY]
    if view == TRUTH:
        return ids[TRUTH]
    if holder is None:
        raise MemoryRuleError(beliefs.BAD_READING)
    have = await w.select(PERSPECTIVES, " AND entity_id = %s", [holder])
    if have:
        return str(have[0]["id"])
    made = new_id()
    kind = PC_VIEW if roles.get(holder, (None, None))[1] is not None else NPC_VIEW
    await w.insert(
        PERSPECTIVES, {"id": made, "kind": kind, "entity_id": holder, "created_at": w.now}
    )
    return made


async def _place_in(
    store: Any,
    w: Changes,
    claim: Claim,
    reading: Mapping[str, Any],
    subject_id: str,
    predicate: str,
    object_id: str,
    detail: str,
    holder: str | None,
    roles: Mapping[str, tuple[str | None, int | None]],
) -> tuple[str, Belief | None, list[Flag]]:
    """The write: find or make the connection, then the belief, in the one open write."""
    status, confidence, view = str(reading["status"]), float(reading["confidence"]), reading["view"]
    # What the connection must be for this reading. A reveal is shown and kept; a secret plot is a
    # kept secret; everything else only needs something to hang on, which nobody has kept.
    keeps = status in (REVEALED_FACT, MAGICALLY_VERIFIED, SECRET_PLOT)
    secret = status != REVEALED_FACT and status != MAGICALLY_VERIFIED
    wanted = CONFIRMED if keeps else PROPOSED
    relation, flags = await store.add_relation_in(
        w, subject_id, predicate, object_id,
        confidence=1.0 if keeps else HOST_CONFIDENCE, status=wanted, detail=detail,
        from_session_at=claim.session_started_at, secret=secret, fresh=True, leave=True,
    )  # fmt: skip
    host = relation.id
    if keeps and relation.status == PROPOSED:
        # Waiting as a theory until now; the DM placing the reading keeps it. This is the DM's
        # Keep on the connection (it can only be a proposal that was made for beliefs).
        await w.update(RELATIONS, host, {"status": CONFIRMED, "secret": secret})
        flags = [*flags, *await flag_kept(w, host)]  # judged now, as a fact
    if status == SECRET_PLOT:  # the DM's view already holds it, as a plot, from the connection
        return host, None, list(flags)
    perspective = await _view_id(w, view, holder, roles)
    belief = await beliefs.place_belief(
        w, perspective, host, confidence, status,
        evidence=[claim.id], session_at=claim.session_started_at,
    )  # fmt: skip
    return host, belief, list(flags)
