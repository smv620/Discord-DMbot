"""From the story extractor's output to proposals the DM answers (docs/STORY_MEMORY.md,
section 1 and "AI roles"; #1297). Everything between the AI call and memory, with no AI call
in it: parse the answer, run the deterministic rules, re-check consent, store what is left as
*proposals*, and write a fact only when the DM says yes.

The extractor is untrusted twice over. The transcript it reads can say anything, and its
answer is only text. So nothing it writes is more than a proposal: a claim goes in the
`memory_claims` table, never straight into memory, its source ("extractor") can't confirm, and
a fact is written only by `ClaimStore.confirm`, as the DM, through the same store that checks
every other fact. A claim the rules or consent refuse is kept as "dropped" with the reason, so
the numbers show how often, but it is never asked about and never a fact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import ALIASES, CLAIMS, ENTITIES
from dmbot.memory.models import (
    CLAIM_CONFIRMED,
    CLAIM_DROPPED,
    CLAIM_PROPOSED,
    CLAIM_REJECTED,
    CONDITIONS,
    CONFIRMED,
    DETAIL_MAX,
    DM,
    HOW_PLAN,
    HOWS,
    NAME_MAX,
    Flag,
    MemoryRuleError,
    name_key,
    new_id,
)
from dmbot.memory.ontology import SYNONYMS, Ontology, PredicateTerm, conditions_for
from dmbot.memory.store import MemoryStore, _load_ontology

EXTRACTOR = "extractor"  # the memory source of everything this module writes on its own
MAX_CLAIMS = 60  # for one call: the same cap as the draft extractor's answer check
FIELD_MAX = NAME_MAX
LINES_MAX = 10  # lines one claim may cite
# How many waiting questions to show in one message: five Yes and five No buttons fill the
# two rows Discord allows beside the rest of the message.
PAGE = 5

NOT_THE_DM = "Only the DM can answer that."
GONE = "DMbot doesn't have that question any more."

# Why a claim was dropped, in words the numbers (and a curious DM) can read.
PLAN_REASON = "a plan, not something that happened"
INJECTION_REASON = "it reads like an instruction, not a story"
NO_SPEAKER_REASON = "a line it cites has no known speaker"
STOPPED_REASON = "a speaker it cites is not being recorded now"
STOPPED_NOW = "Someone who said that has stopped being recorded, so it can't be kept."
ODD_NOW = "That doesn't look like something said in the game any more."
UNKNOWN_RELATION_REASON = "DMbot has no relationship by that name"
UNKNOWN_NAME_REASON = "a name in it is not one DMbot knows yet"
TWO_ENTRIES_REASON = "a name in it could be two different entries"
SAME_ENTRY_REASON = "it connects an entry to itself"
STATE_REASON = "that entry can't be in that condition"

# A claim that is an instruction to DMbot in a story's clothes: never memory. The extractor's
# prompt says the same, but it is a model, and this is the check that doesn't forget.
_INSTRUCTION = re.compile(
    r"ignore (all |any |your |the |previous |prior )*(instructions|rules|prompt)"
    r"|disregard (all |any |your |the |previous |prior )*(instructions|rules|prompt)"
    r"|\b(system|admin|developer) (prompt|mode|message)\b"
    r"|\byou are now\b|\bnew instructions\b|\bas the dm'?s assistant\b",
    re.IGNORECASE,
)
_STATE_VERBS = frozenset({"is", "was", "are", "were", "became", "be"})


@dataclass(frozen=True, slots=True)
class Extracted:
    """One claim as the extractor said it: free text, not yet checked against anything."""

    subject: str
    relationship: str
    object: str
    details: str
    lines: tuple[str, ...]
    how: str


@dataclass(frozen=True, slots=True)
class Claim:
    """A stored claim."""

    id: str
    subject: str
    relationship: str
    object: str
    details: str
    how: str
    lines: tuple[str, ...]
    speaker_ids: tuple[int, ...]
    source: str
    prompt_version: str
    model: str
    status: str
    reason: str
    session_started_at: int | None
    created_at: int
    decided_at: int | None
    fact_id: str | None


def claim_of(r: Mapping[str, Any]) -> Claim:
    """A stored claim from its row."""
    return Claim(
        r["id"], r["subject"], r["relationship"], r["object"], r["details"], r["how"],
        tuple(r["lines"]), tuple(r["speaker_ids"]), r["source"], r["prompt_version"], r["model"],
        r["status"], r["reason"], r["session_started_at"], r["created_at"], r["decided_at"],
        r["fact_id"],
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Proposed:
    """What a batch of extractor claims came to."""

    waiting: int  # stored as proposals for the DM
    dropped: int  # stored as dropped, with the reason
    repeated: int  # asked before (or turned down): not stored again
    malformed: int = 0  # not even a claim (set by the caller from `parse_answer`)


@dataclass(frozen=True, slots=True)
class Confirmed:
    claim: Claim
    flags: tuple[Flag, ...]  # problems the store's checks raised for the DM


def _field(value: object) -> str | None:
    """A short piece of the extractor's answer: one line of text within the limit, or None
    when it is not text or is empty."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:FIELD_MAX] if text else None


def parse_answer(text: str, refs: Collection[str]) -> tuple[list[Extracted], int]:
    """The claims in an extractor's JSON answer, and how many items were not well-formed.
    Only line references that were sent count; a claim citing none of them is dropped here
    (it can't be traced to anything). Pure, and strict about every field: the answer is data."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    items = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], 1
    known = set(refs)
    out: list[Extracted] = []
    bad = max(0, len(items) - MAX_CLAIMS)
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            bad += 1
            continue
        subject, relationship = _field(item.get("subject")), _field(item.get("relationship"))
        obj = _field(item.get("object")) or ""
        details = " ".join(str(item.get("details") or "").split())[:DETAIL_MAX]
        how = item.get("how")
        cited = item.get("lines")
        lines = tuple(
            dict.fromkeys(
                str(n)
                for n in (cited if isinstance(cited, list) else [])
                if isinstance(n, (int, str)) and not isinstance(n, bool) and str(n) in known
            )
        )[:LINES_MAX]
        if subject is None or relationship is None or how not in HOWS or not lines:
            bad += 1
            continue
        assert isinstance(how, str)
        out.append(Extracted(subject, relationship, obj, details, lines, how))
    return out, bad


def injection_like(claim: Extracted) -> bool:
    return any(
        _INSTRUCTION.search(text)
        for text in (claim.subject, claim.relationship, claim.object, claim.details)
    )


def _phrase(text: str) -> str:
    """Words as a key ("Is a member of" → "is_a_member_of"), so wording, case and spacing do
    not matter when a phrase is matched to a relationship."""
    return "_".join(re.findall(r"[a-z0-9]+", text.casefold()))


def resolve_relationship(onto: Ontology, text: str) -> tuple[PredicateTerm, bool] | None:
    """The relationship a phrase means and whether it is said from the object's side
    ("owned by"): its key, a synonym of a core one, or its words. None: not one DMbot has."""
    phrase = _phrase(text)
    if not phrase:
        return None
    for key in (phrase, SYNONYMS.get(phrase, "")):
        term = onto.predicates.get(key)
        if term is not None and term.status == "active":
            return term, False
    for term in onto.predicates.values():
        if term.status != "active":
            continue
        if phrase in {_phrase(term.label)} | {_phrase(s) for s in term.synonyms}:
            return term, False
        if term.inverse_label and phrase == _phrase(term.inverse_label):
            return term, True
    return None


def condition_of(claim: Extracted) -> str | None:
    """The condition a claim says an entry is in ("Roan is dead"), if it is one."""
    if _phrase(claim.relationship) in _STATE_VERBS:
        word = _phrase(claim.object)
        if word in CONDITIONS:
            return word
    return None


@dataclass(frozen=True, slots=True)
class Names:
    """Which entries a name means, by name or other name: key → (entry id, its kind)."""

    by_key: Mapping[str, tuple[tuple[str, str], ...]]

    def one(self, name: str) -> tuple[str, str] | str:
        """The entry (id, kind), or the reason there isn't exactly one."""
        found = self.by_key.get(name_key(name), ())
        if not found:
            return UNKNOWN_NAME_REASON
        if len(found) > 1:
            return TWO_ENTRIES_REASON
        return found[0]


def names_of(entities: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]]) -> Names:
    """Which entries each name (or other name) means, from the campaign's live entries."""
    kinds = {str(e["id"]): str(e["type"]) for e in entities}
    seen: dict[str, dict[str, str]] = {}
    for e in entities:
        seen.setdefault(name_key(str(e["name"])), {})[str(e["id"])] = str(e["type"])
    for a in aliases:
        if str(a["entity_id"]) in kinds:
            seen.setdefault(str(a["key"]), {})[str(a["entity_id"])] = kinds[str(a["entity_id"])]
    return Names({k: tuple(v.items()) for k, v in seen.items()})


def judge(
    claim: Extracted, onto: Ontology, names: Names, speakers: Sequence[int | None],
    opted_in: Collection[int],
) -> str:  # fmt: skip
    """Why a claim can't go to the DM, or "" when it can. Pure: the rules from memory's
    ontology, the names as DMbot knows them, and who may be listened to *now*."""
    # Consent first: whatever else is wrong with a claim, a speaker who is not recorded now
    # must leave nothing behind.
    if not speakers or any(s is None for s in speakers):
        return NO_SPEAKER_REASON
    if any(s not in opted_in for s in speakers):
        return STOPPED_REASON
    if claim.how == HOW_PLAN:
        return PLAN_REASON  # a plan is never turned into a fact
    if injection_like(claim):
        return INJECTION_REASON
    who = names.one(claim.subject)
    if isinstance(who, str):
        return who
    condition = condition_of(claim)
    if condition is not None:
        allowed = conditions_for(onto, who[1])
        return "" if allowed is not None and condition in allowed else STATE_REASON
    resolved = resolve_relationship(onto, claim.relationship)
    if resolved is None:
        return UNKNOWN_RELATION_REASON
    term, said_from_object = resolved
    other = names.one(claim.object)
    if isinstance(other, str):
        return other
    if who[0] == other[0]:
        return SAME_ENTRY_REASON
    s_kind, o_kind = (other[1], who[1]) if said_from_object else (who[1], other[1])
    if term.subject_types and not onto.is_any(s_kind, term.subject_types):
        return f"{term.label} doesn't fit those two"
    if term.object_types and not onto.is_any(o_kind, term.object_types):
        return f"{term.label} doesn't fit those two"
    return ""


def _repeat_key(subject: str, relationship: str, obj: str, how: str) -> tuple[Any, ...]:
    """What makes two questions the same: the words, not who said them. A plan is its own
    question (it is never asked, so it must not hide the fact it resembles)."""
    return name_key(subject), _phrase(relationship), name_key(obj), how == HOW_PLAN


class ClaimStore:
    """Proposals from the extractor, and the DM's answers to them."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def propose(
        self,
        guild_id: int,
        campaign_id: str,
        claims: Sequence[Extracted],
        speakers: Mapping[str, int | None],
        *,
        opted_in: Collection[int],
        prompt_version: str,
        model: str,
        session_started_at: int | None,
        malformed: int = 0,
    ) -> Proposed:
        """Store what the extractor said as proposals. `speakers` maps each transcript line
        the call was given to who spoke it; `opted_in` is who may be recorded *now*, read
        after the call (consent can change while the AI works). A claim citing anyone else is
        not stored at all, not even as dropped: its words are theirs, and they said no. Nothing
        here is a fact."""
        waiting = dropped = repeated = 0
        async with self._store._write(guild_id, campaign_id, EXTRACTOR) as w:
            onto = await _load_ontology(w)
            entities = await w.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await w.select(ALIASES, " AND status <> 'rejected'")
            names = names_of(entities, aliases)
            asked: set[tuple[Any, ...]] = set()  # what the DM has seen or is waiting on
            refused: set[tuple[Any, ...]] = set()  # what was dropped before, and why
            for r in await w.select(CLAIMS, ""):
                key = _repeat_key(r["subject"], r["relationship"], r["object"], r["how"])
                if r["status"] == CLAIM_DROPPED:
                    refused.add((*key, r["reason"]))
                else:
                    asked.add(key)
            rows: list[dict[str, Any]] = []
            for claim in claims[:MAX_CLAIMS]:
                key = _repeat_key(claim.subject, claim.relationship, claim.object, claim.how)
                cited = [speakers.get(ref) for ref in claim.lines]
                reason = judge(claim, onto, names, cited, opted_in)
                if reason in (STOPPED_REASON, NO_SPEAKER_REASON):
                    dropped += 1  # counted, never kept
                    continue
                if key in asked or (*key, reason) in refused:
                    repeated += 1
                    continue
                if reason:
                    dropped += 1
                    refused.add((*key, reason))
                else:
                    waiting += 1
                    asked.add(key)
                rows.append(
                    {
                        "id": new_id(),
                        "subject": claim.subject,
                        "relationship": claim.relationship,
                        "object": claim.object,
                        "details": claim.details,
                        "how": claim.how,
                        "lines": list(claim.lines),
                        "speaker_ids": sorted({s for s in cited if s is not None}),
                        "source": EXTRACTOR,
                        "prompt_version": prompt_version,
                        "model": model,
                        "status": CLAIM_DROPPED if reason else CLAIM_PROPOSED,
                        "reason": reason,
                        "session_started_at": session_started_at,
                        "created_at": w.now,
                        "decided_at": None,
                        "fact_id": None,
                    }
                )
            await w.insert_many(CLAIMS, rows)
        return Proposed(waiting, dropped, repeated, malformed)

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Forget, before anyone is shown them, the claims (waiting, turned down or dropped;
        never what the DM confirmed) that cite someone who is not recorded in this server
        now: marked dropped, with the words wiped. This is the
        consent re-check on the read path. It catches a Stop recording me whose clean-up
        failed, and a backup restored after someone stopped or into a server where they never
        agreed (consent is per server). Returns how many went."""
        async with self._store._write(guild_id, campaign_id, EXTRACTOR) as w:
            stale = [
                r
                for r in await w.select(CLAIMS, " AND status <> 'confirmed'")
                if (r["subject"] or r["lines"])  # not already wiped
                and (not r["speaker_ids"] or not set(r["speaker_ids"]) <= set(opted_in))
            ]
            for r in stale:
                await w.update(
                    CLAIMS,
                    r["id"],
                    {
                        "status": CLAIM_DROPPED,
                        "reason": STOPPED_REASON,
                        "subject": "",
                        "relationship": "",
                        "object": "",
                        "details": "",
                        "lines": [],
                        "speaker_ids": [],
                    },
                )
            return len(stale)

    async def waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int], limit: int = 200
    ) -> list[Claim]:
        """The questions waiting for the DM, grouped by who they are about: the oldest first
        within a name. Only claims whose every speaker is recorded now (`opted_in`, read just
        before); the others are wiped first (`purge_stopped`). At most `limit`, the oldest
        (a page is five, so 200 is far more than anyone answers in one sitting, and the
        grouping covers just these)."""
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                CLAIMS,
                " AND status = 'proposed' ORDER BY created_at, id LIMIT %s",
                [limit],
            )
        found = [claim_of(r) for r in rows]
        found.sort(key=lambda c: name_key(c.subject))  # stable: oldest first within a name
        return found

    async def get(self, guild_id: int, campaign_id: str, claim_id: str) -> Claim | None:
        async with self._store._read(guild_id, campaign_id) as scope:
            row = await scope.get(CLAIMS, claim_id, lock=False)
        return None if row is None else claim_of(row)

    async def counts(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> dict[str, int]:
        """How many claims there are in each status (one grouped count, not the rows), after
        the ones that cite someone not recorded now are wiped, so a count never includes
        words the DM must not see."""
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT status, count(*) AS n FROM memory_claims"
                " WHERE guild_id = %s AND campaign_id = %s GROUP BY status",
                scope.ids,
            )
            return {str(r["status"]): int(r["n"]) for r in await cur.fetchall()}

    async def forget_speaker(self, guild_id: int, user_id: int) -> int:
        """Stop recording me: in every campaign of the server, delete the claims that cite
        this person's words and were not turned into a fact (waiting, turned down or dropped).
        What the DM confirmed is the DM's fact and stays. Returns how many went."""
        async with self._store._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_claims WHERE guild_id = %s AND %s = ANY(speaker_ids)"
                " AND status <> 'confirmed'",
                (guild_id, user_id),
            )
            return int(cur.rowcount)

    async def reject(self, guild_id: int, campaign_id: str, claim_id: str) -> Claim:
        """The DM says no: it is not asked again, and nothing is written to memory."""
        async with self._store._write(guild_id, campaign_id, DM) as w:
            row = await w.get(CLAIMS, claim_id)
            if row is None or row["status"] != CLAIM_PROPOSED:
                raise MemoryRuleError(GONE)
            row = await w.update(CLAIMS, claim_id, {"status": CLAIM_REJECTED, "decided_at": w.now})
            return claim_of(row)

    async def confirm(
        self, guild_id: int, campaign_id: str, claim_id: str, *, opted_in: Collection[int]
    ) -> Confirmed:
        """The DM says yes: the fact is written as the DM's own, through the store (so every
        rule and check runs as for any fact), and the claim keeps which fact it became. A fact
        the rules refuse leaves the question waiting and says why. `opted_in` is who may be
        recorded now: if anyone whose words it holds has stopped, the claim is deleted instead
        (the stored row is untrusted too, so the plan and instruction checks run again)."""
        claim = await self.get(guild_id, campaign_id, claim_id)
        if claim is None or claim.status != CLAIM_PROPOSED:
            raise MemoryRuleError(GONE)
        wording = Extracted(
            claim.subject, claim.relationship, claim.object, claim.details, claim.lines, claim.how
        )
        if claim.how == HOW_PLAN:
            raise MemoryRuleError(PLAN_REASON)
        if injection_like(wording):
            async with self._store._write(guild_id, campaign_id, EXTRACTOR) as w:
                await w.update(
                    CLAIMS, claim_id, {"status": CLAIM_DROPPED, "reason": INJECTION_REASON}
                )  # not asked again
            raise MemoryRuleError(ODD_NOW)
        if not claim.speaker_ids or any(s not in opted_in for s in claim.speaker_ids):
            async with self._store._write(guild_id, campaign_id, DM) as w:
                await w.delete(CLAIMS, claim_id)
            raise MemoryRuleError(STOPPED_NOW)
        async with self._store._read(guild_id, campaign_id) as scope:
            onto = await _load_ontology(scope)
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
        names = names_of(entities, aliases)
        # Asked again against what is known now: a name may have been merged or renamed since.
        who = names.one(claim.subject)
        if isinstance(who, str):
            raise MemoryRuleError(f"Can't write that now: {who}.")
        resolved = None
        other: tuple[str, str] | str = ""
        condition = condition_of(wording)
        if condition is None:
            resolved = resolve_relationship(onto, claim.relationship)
            other = names.one(claim.object)
            if resolved is None:
                raise MemoryRuleError(f"Can't write that now: {UNKNOWN_RELATION_REASON}.")
            if isinstance(other, str):
                raise MemoryRuleError(f"Can't write that now: {other}.")
        # One transaction (#1301): the question is taken, the fact written and the claim told
        # which fact it became, or none of it happens. The campaign's write lock is held, so a
        # second press waits and then finds the question gone. A fact the rules refuse rolls
        # the whole thing back and leaves the question waiting. The check at the top of
        # `confirm` is only an early exit: this one, under the lock, is the one that counts.
        async with self._store._write(guild_id, campaign_id, DM) as w:
            current = await w.get(CLAIMS, claim_id)
            if current is None or current["status"] != CLAIM_PROPOSED:
                raise MemoryRuleError(GONE)
            if condition is not None:
                state, flags = await self._store.set_condition_in(
                    w, who[0], condition, claim.session_started_at
                )
                fact_id = who[0] if state is None else state.id
            else:
                assert resolved is not None and not isinstance(other, str)
                term, from_object = resolved
                subject_id, object_id = (other[0], who[0]) if from_object else (who[0], other[0])
                relation, flags = await self._store.add_relation_in(
                    w, subject_id, term.key, object_id, confidence=1.0, status=CONFIRMED,
                    detail=claim.details, from_session_at=claim.session_started_at,
                )  # fmt: skip
                fact_id = relation.id
            row = await w.update(
                CLAIMS,
                claim_id,
                {"status": CLAIM_CONFIRMED, "decided_at": w.now, "fact_id": fact_id},
            )
        return Confirmed(claim_of(row), tuple(flags))
