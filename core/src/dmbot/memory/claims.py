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
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from dmbot.memory import evidence, forget_readings, kin, link
from dmbot.memory._changes import ALIASES, CLAIMS, ENTITIES, Changes
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
    PROPOSED,
    Flag,
    MemoryRuleError,
    clean_text,
    name_key,
    new_id,
)
from dmbot.memory.ontology import (
    FALLBACK_PREDICATE,
    REASON_BETWEEN,
    SYNONYMS,
    Accepted,
    Fallback,
    Ontology,
    PredicateTerm,
    Proposal,
    Refused,
    Reused,
    Tally,
    check_named,
    conditions_for,
    decide_term,
    resolve_kind,
)
from dmbot.memory.store import MemoryStore, _load_ontology, _new_alias
from dmbot.memory.voice import EFFECT, EFFECT_NOTE, OWN, PARTY, PARTY_REASON, voice
from dmbot.names import not_names
from dmbot.word_end import at_word_end

if TYPE_CHECKING:  # (placing builds on this module, so it is only named for the checker)
    from dmbot.memory.placing import Recorded

EXTRACTOR = "extractor"  # the memory source of everything this module writes on its own
MAX_CLAIMS = 60  # for one call: the same cap as the draft extractor's answer check
FIELD_MAX = NAME_MAX
LINES_MAX = 10  # lines one claim may cite
# How many waiting questions to show in one message: five Yes and five No buttons fill the
# two rows Discord allows beside the rest of the message.
PAGE = 5

NOT_THE_DM = "Only the DM can answer that."
GONE = "DMbot doesn't have that question any more."
A_READING = "That is a belief to place, not a fact to keep."

# Why a claim was dropped, in words the numbers (and a curious DM) can read.
PLAN_REASON = "a plan, not something that happened"
INJECTION_REASON = "it reads like an instruction, not a story"
NO_SPEAKER_REASON = "a line it cites has no known speaker"
STOPPED_REASON = "a speaker it cites is not being recorded now"
STOPPED_NOW = "Someone who said that has stopped being recorded, so it can't be kept."
ODD_NOW = "That doesn't look like something said in the game any more."
UNKNOWN_NAME_REASON = "a name in it is not one DMbot knows yet"
TWO_ENTRIES_REASON = "a name in it could be two different entries"
SAME_ENTRY_REASON = "it connects an entry to itself"
STATE_REASON = "that entry can't be in that condition"
NAME_REJECTED_REASON = "the DM said a name in it isn't a name"
SECRET_NAME_REASON = "a name in it is one only the DM may use"
NAME_FIRST = "Say what the new name is first (in Check new names)."
# A claim whose name was past the session's limit is kept, waiting for its name to be proposed in
# a later session (#1367). It is a proposed claim with this as its `reason`, which no claim that
# is asked or dropped carries.
WAITING_NAME_REASON = "waiting for its name to be proposed"
WAITING_NAME_CLAIMS = 500  # how many such claims one campaign keeps; past that they are let go
NEW_NAME_LIMIT = 20  # new names one session sends to the DM; the rest wait for next time
SESSION_CLAIMS = 3000  # claims one session may hand to `propose` at once
CONCEPT = "concept"  # a proposed entry whose kind is not known yet: the DM says what it is

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
    # A belief for the DM to place (#2412, `memory.readings`), as plain words and numbers, or None
    # for a claim that is just something said.
    reading: Mapping[str, Any] | None = None


def is_family(claim: Extracted) -> bool:
    """Does this claim say a family role between two names ("is the niece of", "has an uncle")?
    The only kind a session may suggest for now (owner, 2026-10-29: families first, allies and
    enemies not yet, #2960). Decided in code from the claim's words (read loosely: the AI says
    "married to" and "half-brother of" too), whatever the AI returned: any other kind of claim is
    dropped before it is stored. A measurement aid for #1282, kept off live."""
    return kin.looks_like_family(claim.relationship)


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
    reading: Mapping[str, Any] | None = None


def claim_of(r: Mapping[str, Any]) -> Claim:
    """A stored claim from its row."""
    return Claim(
        r["id"], r["subject"], r["relationship"], r["object"], r["details"], r["how"],
        tuple(r["lines"]), tuple(r["speaker_ids"]), r["source"], r["prompt_version"], r["model"],
        r["status"], r["reason"], r["session_started_at"], r["created_at"], r["decided_at"],
        r["fact_id"], r.get("reading"),
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Proposed:
    """What a batch of extractor claims came to."""

    waiting: int  # stored as proposals for the DM
    dropped: int  # stored as dropped, with the reason
    repeated: int  # asked before (or turned down): not stored again
    malformed: int = 0  # not even a claim (set by the caller from `parse_answer`)
    # Why claims were dropped (stored or not), by the reason's wording; counts only.
    reasons: Mapping[str, int] = field(default_factory=dict)
    named: int = 0  # new names made for the DM to check (proposed entries)
    new: tuple[str, ...] = ()  # those names as said (for the DM screen; never for the log)
    joined: int = 0  # spellings added to a name already waiting for the DM
    deferred: int = 0  # claims left for next time: their name is past the session's limit


@dataclass(frozen=True, slots=True)
class Confirmed:
    claim: Claim
    flags: tuple[Flag, ...]  # problems the store's checks raised for the DM
    batch: int | None = None  # the change-log batch, for the ↩️ Undo of a fact (#2410)


def _field(value: object, *, name: bool = False) -> str | None:
    """A short piece of the extractor's answer: one line of text within the limit, or None
    when it is not text or is empty."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if name:  # a name is never shortened (#2607): a cut one is another name
        return text if 0 < len(text) <= FIELD_MAX else None
    return at_word_end(text, FIELD_MAX) if text else None


_LIST_START = re.compile(r'\{\s*"claims"\s*:\s*\[')


def _whole_items(text: str) -> list[object]:
    """The complete claims at the start of an answer that was cut off in the middle of its list
    (Haiku 5.5 writes longer, and a batch can end mid-claim): every item before the broken one is
    as good as in a whole answer, so it is kept instead of losing the batch (#2152). Only an
    answer whose first `{` opens the claims list and whose list never closes is read this way: a
    list that ended properly in an answer that is broken elsewhere stays rejected, and words
    before the JSON (which may quote a transcript) are never searched for a list."""
    first = text.find("{")
    found = _LIST_START.match(text, first) if first >= 0 else None
    if found is None:
        return []
    decoder, at = json.JSONDecoder(), found.end()
    items: list[object] = []
    while True:
        while at < len(text) and text[at] in " \t\r\n,":
            at += 1
        try:
            item, at = decoder.raw_decode(text, at)
        except ValueError:  # the cut item, or the end of the list
            return [] if text[at:].lstrip().startswith("]") else items
        items.append(item)


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
    cut = 0
    if not isinstance(items, list):
        items = _whole_items(text) if data is None else []
        if not items:
            return [], 1
        cut = 1  # the item the answer broke off in counts as malformed
    known = set(refs)
    out: list[Extracted] = []
    bad = cut + max(0, len(items) - MAX_CLAIMS)
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            bad += 1
            continue
        subject = _field(item.get("subject"), name=True)
        relationship = _field(item.get("relationship"))
        obj = _field(item.get("object"), name=True) or ""
        said = item.get("details")  # words only: a list or a number is no detail
        details = at_word_end(" ".join(said.split()), DETAIL_MAX) if isinstance(said, str) else ""
        how = item.get("how")
        if isinstance(how, str):  # "Heard", " heard " and "heard." all mean the same word
            how = how.strip().rstrip(".").lower()
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


def reads_like_instruction(text: str) -> bool:
    """Is this text an instruction to DMbot in a story's clothes? (Also used on the story plan
    read from an adventure, #1714: a book's text is data, as a transcript's is.)"""
    return bool(_INSTRUCTION.search(text))


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


def place_relationship(
    onto: Ontology, text: str, subject_kind: str, object_kind: str, tally: Tally | None = None
) -> tuple[PredicateTerm, bool, str]:
    """The relationship a claim's words go on, whether they are said from the object's side,
    and the label to keep with the fact ("" when the relationship's own wording is the label).

    A relationship DMbot knows is used as it is. Words it does not know go to the steward
    (#1303): an existing term it recognises (a near spelling, say) is reused; a rules word, a
    term the meaning check cannot yet vouch for, or anything else it refuses goes on the
    catch-all `associated_with` with the claim's own words as the label. A claim is never lost
    for its relationship, and DMbot never makes a new relationship term from a claim yet:
    until the meaning check (embeddings) exists, an unchecked new term counts as refused."""
    known = resolve_relationship(onto, text)
    if known is not None:
        return known[0], known[1], ""
    decision = decide_term(
        onto,
        # "link" is only the proposal's key when the words have no letters to make one from.
        Proposal(_phrase(text) or "link", text, subject_types=(subject_kind,),
                 object_types=(object_kind,)),
    )  # fmt: skip
    if isinstance(decision, Accepted) and not decision.meaning_checked:
        decision = Refused(REASON_BETWEEN, Fallback(FALLBACK_PREDICATE, text))
    if tally is not None:
        tally.record(decision)
    if isinstance(decision, Reused) and decision.existing in onto.predicates:
        return onto.predicates[decision.existing], False, ""
    label = " ".join(text.split())
    if len(label) > DETAIL_MAX:
        # Cut at a whole word so the card never shows half a word.
        label = label[: DETAIL_MAX - 1].rsplit(" ", 1)[0] + "…"
    # A blank label would leave a nameless link; say it plainly instead.
    return onto.active_predicate(FALLBACK_PREDICATE), False, label or "linked to"


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
    secret: frozenset[str] = frozenset()  # keys that only a secret name has
    # Each character's (role, its player) by entry id, for who speaks for it (#2562)
    roles: Mapping[str, tuple[str | None, int | None]] = field(default_factory=dict)

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
    open_keys = {name_key(str(e["name"])) for e in entities}
    open_keys |= {str(a["key"]) for a in aliases if not a.get("secret")}
    hidden = frozenset(
        str(a["key"])
        for a in aliases
        if a.get("secret") and str(a["entity_id"]) in kinds and str(a["key"]) not in open_keys
    )
    roles = {
        str(e["id"]): (e.get("role"), e.get("played_by"))
        for e in entities
        if str(e["type"]) == "character"
    }
    return Names({k: tuple(v.items()) for k, v in seen.items()}, hidden, roles)


def judge(
    claim: Extracted, onto: Ontology, names: Names, speakers: Sequence[int | None],
    opted_in: Collection[int], tally: Tally | None = None, dms: Collection[int] = (),
) -> str:  # fmt: skip
    """Why a claim can't go to the DM, or "" when it can. Pure: the rules from memory's
    ontology, the names as DMbot knows them, and who may be listened to *now*. A relationship
    DMbot does not know is not a reason: it goes through the steward (`place_relationship`),
    which puts the claim on a known one or on the catch-all, counted in `tally`."""
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
    # A secret name is the DM's: a claim a player said with one in it is not matched (the
    # transcript is open to the server, and a proposal could give the name away).
    if names.secret and any(s not in dms for s in speakers):
        said = [claim.subject] if condition_of(claim) is not None else [claim.subject, claim.object]
        if any(name_key(n) in names.secret for n in said):
            return SECRET_NAME_REASON
    who = names.one(claim.subject)
    if isinstance(who, str):
        return who
    condition = condition_of(claim)
    if condition is not None:
        allowed = conditions_for(onto, who[1])
        return "" if allowed is not None and condition in allowed else STATE_REASON
    other = names.one(claim.object)
    if isinstance(other, str):
        return other
    if who[0] == other[0]:
        return SAME_ENTRY_REASON
    term, said_from_object, _label = place_relationship(
        onto, claim.relationship, who[1], other[1], tally
    )
    s_kind, o_kind = (other[1], who[1]) if said_from_object else (who[1], other[1])
    if term.subject_types and not onto.is_any(s_kind, term.subject_types):
        return f"{term.label} doesn't fit those two"
    if term.object_types and not onto.is_any(o_kind, term.object_types):
        return f"{term.label} doesn't fit those two"
    # Who speaks for a character (#2562): a PC's own choice and feeling come from its player, an
    # NPC's from the DM. A player's word about someone else's character is what they believe, never
    # the character's own, so it is not offered to the DM as a truth.
    if _voice_of(names, who, other, said_from_object, term, claim, speakers, dms) == PARTY:
        return PARTY_REASON
    return ""


def _voice_of(
    names: Names,
    who: tuple[str, str],
    other: tuple[str, str],
    said_from_object: bool,
    term: PredicateTerm,
    claim: Extracted,
    speakers: Sequence[int | None],
    dms: Collection[int],
) -> str:
    """`own`, `effect` or `party` (`memory.voice`) for a claim about the character it is about."""
    holder = other if said_from_object else who
    if holder[1] != "character":
        return OWN  # (a group's attitude stays the DM's)
    role, plays = names.roles.get(holder[0], (None, None))
    return voice(role, term.key, claim.how, speakers, plays, dms)


def effect_note(
    claim: Extracted, onto: Ontology, names: Names, speakers: Sequence[int | None],
    dms: Collection[int],
) -> str:  # fmt: skip
    """The words to add to a claim's details when the DM said it of a player character: it is
    something that happened to them, told by the DM, not their own choice (#2562). "" otherwise."""
    who = names.one(claim.subject)
    other = names.one(claim.object)
    if isinstance(who, str) or isinstance(other, str):
        return ""
    term, said_from_object, _ = place_relationship(onto, claim.relationship, who[1], other[1])
    if _voice_of(names, who, other, said_from_object, term, claim, speakers, dms) != EFFECT:
        return ""
    holder = other if said_from_object else who
    return (
        EFFECT_NOTE.format(name=claim.object if said_from_object else claim.subject)
        if holder
        else ""
    )


def _repeat_key(
    subject: str, relationship: str, obj: str, how: str, reading: Mapping[str, Any] | None = None
) -> tuple[Any, ...]:
    """What makes two questions the same: the words, not who said them. A plan is its own
    question (it is never asked, so it must not hide the fact it resembles). A reading is its own
    question too: the same words as a theory, a reveal and a plain claim are three things to place
    (point of view and status tell them apart)."""
    # (Only the point of view and the status: two readings of the same words that differ only in
    # the clue they name are one question. Slice 2b may need the clue here.)
    held = None if reading is None else (reading.get("view"), reading.get("status"))
    return name_key(subject), _phrase(relationship), name_key(obj), how == HOW_PLAN, held


async def _turned_down(scope: Any) -> set[str]:
    """The name keys the DM said are not names: those of an entry turned down ("Not a name"),
    and any other name turned down. `scope` is a read or a write of one campaign."""
    gone = [str(e["id"]) for e in await scope.select(ENTITIES, " AND status = 'rejected'")]
    found = await scope.select(ALIASES, " AND (status = 'rejected' OR entity_id = ANY(%s))", [gone])
    return {str(a["key"]) for a in found}


def _unknown_names(claim: Extracted, names: Names) -> list[str]:
    """The names in a claim that match no entry (the subject, and the object unless it is the
    condition a state change names), as said."""
    said = [claim.subject] if condition_of(claim) is not None else [claim.subject, claim.object]
    return [n for n in said if n.strip() and not names.by_key.get(name_key(n))]


def implied_kind(onto: Ontology, claim: Extracted, side: str) -> str:
    """The kind of entry a relationship fixes for a name on this side of a claim, or `CONCEPT`
    ("unsure") when it fixes none: "is in" puts a place on its object side, say. The DM can
    always change it when the name is checked."""
    condition = condition_of(claim)
    if condition is not None:
        # "Garrick is dead": the one kind that can be in that condition, if there is just one.
        fits = [
            k
            for k in ("character", "item", "place", "faction")
            if (allowed := conditions_for(onto, k)) is not None and condition in allowed
        ]
        return fits[0] if len(fits) == 1 else CONCEPT
    known = resolve_relationship(onto, claim.relationship)
    if known is None:
        return CONCEPT
    term, from_object = known
    types = term.object_types if (side == "object") != from_object else term.subject_types
    # (a kind that allows several ends is guessed as a character when it allows one: a new name
    # in "knows" or "rival of" is a person far more often than anything else)
    pick = types[0] if len(types) == 1 else "character" if "character" in types else None
    if pick is None:
        return CONCEPT
    try:
        onto.active_type(pick)
    except MemoryRuleError:
        return CONCEPT
    return pick if pick != "person" else CONCEPT


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
        said: Mapping[str, str] | None = None,
        opted_in: Collection[int],
        prompt_version: str,
        model: str,
        session_started_at: int | None,
        malformed: int = 0,
        tally: Tally | None = None,
        dms: Collection[int] = (),
        skip: Collection[str] = (),
        new_names: int = NEW_NAME_LIMIT,
        limit: int = MAX_CLAIMS,
        make_names: bool = True,
    ) -> Proposed:
        """Store what the extractor said as proposals. `speakers` maps each transcript line
        the call was given to who spoke it; `opted_in` is who may be recorded *now*, read
        after the call (consent can change while the AI works). A claim citing anyone else is
        not stored at all, not even as dropped: its words are theirs, and they said no. Nothing
        here is a fact.

        The linker (#1364): a name DMbot does not know is not a reason to lose the claim. Up to
        `new_names` of them (the most said first; the rest wait for next time and their claims
        are not stored) become *proposed* entries for the DM, whose claims wait on the name and
        are asked only once the DM has said what it is (`waiting`). A name said two near ways
        is one proposal, and a spelling near a name already waiting joins it. Nothing is joined
        to a confirmed entry here: a likely match is shown to the DM by the names review. A name
        the DM said isn't a name (`skip` too: the people at the table) drops its claims for good.
        `dms` are the campaign's DMs: only they can say a secret name. `make_names` False: a claim
        with a name DMbot does not know is dropped (nothing waits for the name), so it can never
        become a new name later (a reading never makes one, #2412)."""
        waiting = dropped = repeated = 0
        why: Counter[str] = Counter()
        async with self._store._write(guild_id, campaign_id, EXTRACTOR) as w:
            onto = await _load_ontology(w)
            entities = await w.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await w.select(ALIASES, " AND status <> 'rejected'")
            refused_names = await _turned_down(w)
            names = names_of(entities, aliases)
            asked: set[tuple[Any, ...]] = set()  # what the DM has seen or is waiting on
            refused: set[tuple[Any, ...]] = set()  # what was dropped before, and why
            for r in await w.select(CLAIMS, ""):
                key = _repeat_key(
                    r["subject"], r["relationship"], r["object"], r["how"], r.get("reading")
                )
                if r["status"] == CLAIM_DROPPED:
                    refused.add((*key, r["reason"]))
                else:
                    asked.add(key)

            leftovers = [
                r
                for r in await w.select(CLAIMS, " AND status = 'proposed'")
                if r["reason"] == WAITING_NAME_REASON and make_names
            ]  # fmt: skip  (a pass that makes no names leaves earlier waiting claims alone)

            def judged(claim: Extracted) -> str:
                cited = [speakers.get(ref) for ref in claim.lines]
                return judge(claim, onto, names, cited, opted_in, tally, dms)

            # First pass: the rules, with the names as they are. A claim that fails only for a
            # name DMbot does not know is a candidate for the linker.
            work: list[tuple[Extracted, str, list[str]]] = []  # claim, reason, unknown names
            for claim in claims[:limit]:
                reason = judged(claim)
                unknown: list[str] = []
                # A pronoun is a name only where a cited line plainly says so ("My name is We").
                cued = not_names.cued_in("\n".join((said or {}).get(r, "") for r in claim.lines))
                if reason == UNKNOWN_NAME_REASON:
                    unknown = _unknown_names(claim, names)
                    if not make_names:
                        unknown = []  # dropped below with its reason, not kept waiting
                    elif any(name_key(n) in refused_names for n in unknown):
                        reason = NAME_REJECTED_REASON
                        unknown = []  # never made into an entry again
                    elif any(name_key(n) in skip for n in unknown):
                        unknown = []  # a person at the table: not a story name
                    elif not all(not_names.allowed(n, cued) for n in unknown):
                        unknown = []  # a pronoun ("he", "we"): never made into a name (#2358)
                work.append((claim, reason, unknown))
            # Claims kept from an earlier session for a name that was past its limit come first:
            # their names are proposed before this session's own (#1367).
            old: list[tuple[dict[str, Any], Extracted, list[str]]] = []
            for r in leftovers:
                kept = Extracted(
                    r["subject"], r["relationship"], r["object"], r["details"],
                    tuple(r["lines"]), r["how"], r.get("reading"),
                )  # fmt: skip
                if not r["speaker_ids"] or any(x not in opted_in for x in r["speaker_ids"]):
                    continue  # its speaker stopped: `purge_stopped` wipes it when it is read
                unknown = _unknown_names(kept, names)
                if any(name_key(n) in refused_names for n in unknown):
                    await w.update(
                        CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": NAME_REJECTED_REASON}
                    )
                elif any(name_key(n) in skip or not_names.is_not_name(n) for n in unknown):
                    await w.update(
                        CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": UNKNOWN_NAME_REASON}
                    )
                elif unknown:
                    old.append((r, kept, unknown))
            old_mentions = [name_key(n) for _, _, unknown in old for n in unknown]
            chosen_old, left_old = link.most_mentioned(old_mentions, new_names)
            fresh = [name_key(n) for _, _, unknown in work for n in unknown]
            fresh = [m for m in fresh if m not in chosen_old]
            chosen_new, left_new = link.most_mentioned(fresh, max(0, new_names - len(chosen_old)))
            chosen = [*chosen_old, *chosen_new]
            left = bool(left_old or left_new)
            deferred = 0
            if chosen:
                both = [*work, *((k, "", u) for _, k, u in old)]
                new, joined = await self._link(w, onto, both, chosen, entities, aliases)
                names = names_of(entities, aliases)
            else:
                new, joined = [], 0
            for r, kept, _ in old:  # the names that were made now resolve: ask them as usual
                if _unknown_names(kept, names):
                    continue
                holders = [int(x) for x in r["speaker_ids"]]
                reason = judge(kept, onto, names, holders, opted_in, tally, dms)
                if reason:
                    await w.update(CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": reason})
                else:
                    await w.update(CLAIMS, r["id"], {"reason": ""})
                    waiting += 1
            kept_waiting = len(leftovers)
            rows: list[dict[str, Any]] = []
            for claim, reason, unknown in work:
                if unknown:
                    if left and any(name_key(n) not in chosen for n in unknown):
                        # Past the limit: kept waiting for its name, which is proposed in a later
                        # session (most-said first, before that session's own).
                        key = _repeat_key(
                            claim.subject,
                            claim.relationship,
                            claim.object,
                            claim.how,
                            claim.reading,
                        )
                        if key in asked:
                            repeated += 1
                        elif kept_waiting >= WAITING_NAME_CLAIMS:
                            why[WAITING_NAME_REASON] += 1  # too many kept: let this one go
                        else:
                            kept_waiting += 1
                            deferred += 1
                            asked.add(key)
                            reason = WAITING_NAME_REASON
                            cited = [speakers.get(ref) for ref in claim.lines]
                            rows.append(self._row(
                                claim, cited, prompt_version, model, session_started_at, w.now,
                                status=CLAIM_PROPOSED, reason=reason,
                            ))  # fmt: skip
                        continue
                    reason = judged(claim)
                key = _repeat_key(
                    claim.subject, claim.relationship, claim.object, claim.how, claim.reading
                )
                cited = [speakers.get(ref) for ref in claim.lines]
                if reason in (STOPPED_REASON, NO_SPEAKER_REASON):
                    dropped += 1  # counted, never kept
                    why[reason] += 1
                    continue
                if key in asked or (*key, reason) in refused:
                    repeated += 1
                    continue
                if reason:
                    dropped += 1
                    why[reason] += 1
                    refused.add((*key, reason))
                else:
                    waiting += 1
                    asked.add(key)
                if not reason and (note := effect_note(claim, onto, names, cited, dms)):
                    claim = replace(claim, details=f"{claim.details} {note}".strip())
                rows.append(
                    self._row(
                        claim, cited, prompt_version, model, session_started_at, w.now,
                        status=CLAIM_DROPPED if reason else CLAIM_PROPOSED, reason=reason,
                    )
                )  # fmt: skip
            await w.insert_many(CLAIMS, rows)
        return Proposed(
            waiting, dropped, repeated, malformed, dict(why), len(new), tuple(new), joined, deferred
        )

    @staticmethod
    def _row(
        claim: Extracted, cited: Sequence[int | None], prompt_version: str, model: str,
        session_started_at: int | None, now: int, *, status: str, reason: str,
    ) -> dict[str, Any]:  # fmt: skip
        """A claim as stored."""
        return {
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
            "status": status,
            "reason": reason,
            "session_started_at": session_started_at,
            "created_at": now,
            "decided_at": None,
            "fact_id": None,
            "reading": dict(claim.reading) if claim.reading is not None else None,
        }

    async def _link(
        self,
        w: Changes,
        onto: Ontology,
        work: Sequence[tuple[Extracted, str, list[str]]],
        chosen: Sequence[str],
        entities: list[dict[str, Any]],
        aliases: list[dict[str, Any]],
    ) -> tuple[list[str], int]:
        """Make the chosen name keys proposed entries, or join them to one already waiting.
        `entities` and `aliases` are the rows the caller holds; the new ones are added to them.
        Returns (the new entries' names, spellings joined to a waiting one)."""
        spelling: dict[str, str] = {}  # name key → the words as first said
        sides: dict[str, set[str]] = {}  # name key → the kinds the claims' relationships imply
        times = Counter(
            name_key(n) for _, _, unknown in work for n in unknown if name_key(n) in chosen
        )
        for claim, _, unknown in work:
            for n in unknown:
                key = name_key(n)
                spelling.setdefault(key, n)
                side = "subject" if n == claim.subject else "object"
                sides.setdefault(key, set()).add(implied_kind(onto, claim, side))
        pending = {
            str(e["id"]) for e in entities if e["status"] == PROPOSED
        }  # names the DM has not answered yet
        waiting_keys: dict[str, str] = {}
        for e in entities:
            if str(e["id"]) in pending:
                waiting_keys[name_key(str(e["name"]))] = str(e["id"])
        for a in aliases:
            if str(a["entity_id"]) in pending and not a.get("secret"):  # never via a secret name
                waiting_keys[str(a["key"])] = str(a["entity_id"])
        made: list[str] = []
        joined = 0
        for lead, others in link.spellings(list(chosen)).items():
            group = [lead, *others]
            home = link.alike(lead, waiting_keys)
            fresh = home is None
            if home is None:
                kinds = set().union(*(sides[k] for k in group))
                kind, role = resolve_kind(kinds.pop() if len(kinds) == 1 else CONCEPT)
                name = " ".join(spelling[lead].split())
                try:
                    check_named(kind, name)
                except MemoryRuleError:
                    continue  # not a name DMbot can keep: its claims fail the name rules
                row = await w.insert(
                    ENTITIES,
                    {
                        "id": new_id(), "type": kind, "name": name, "role": role,
                        "description": f"Heard {sum(times[k] for k in group)} times",
                        "status": PROPOSED, "merged_into": None, "source": EXTRACTOR,
                        "created_at": w.now, "played_by": None,
                        "needs_look": False, "unique_item": None,
                    },
                )  # fmt: skip
                alias = await w.insert(
                    ALIASES, _new_alias(w, str(row["id"]), name, "full", PROPOSED, False, None)
                )
                entities.append(row)
                aliases.append(alias)
                home = str(row["id"])
                waiting_keys[lead] = home
                made.append(name)
            for key in group:
                if key in waiting_keys:
                    continue  # the entry's own name, or a spelling it already has
                try:
                    text = clean_text(spelling[key])
                except MemoryRuleError:
                    continue
                extra = _new_alias(w, home, text, "misheard", PROPOSED, False, None)
                aliases.append(await w.insert(ALIASES, extra))
                waiting_keys[key] = home
                joined += 0 if fresh else 1
        return made, joined

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
                        "reading": None,  # the clue, the act and the character it named go too
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
        # (`_sorted` also drops claims whose name the DM turned down: a write on a read path, safe
        # because it rechecks each claim's status under the campaign's lock.)
        ready, _pending = await self._sorted(guild_id, campaign_id)
        found = ready[:limit]  # the limit is after the split: waiting ones can't hide ready ones
        found.sort(key=lambda c: name_key(c.subject))  # stable: oldest first within a name
        return found

    async def _sorted(self, guild_id: int, campaign_id: str) -> tuple[list[Claim], list[Claim]]:
        """The proposed claims split by their names (#1364): (ready, waiting on a new name).
        A claim is ready when each name in it is a confirmed entry. One naming an entry the DM
        has not said yet what it is waits. One whose name is no longer anyone's (the DM said it
        isn't a name, or it was taken away) is dropped here for good, so it is never asked."""
        async with self._store._read(guild_id, campaign_id) as scope:
            # (A reading is a belief to place, not a fact to say yes to: it is not asked here until
            # the DM can place it, #2412.)
            sql = " AND status = 'proposed' AND reading IS NULL ORDER BY created_at, id"
            rows = await scope.select(CLAIMS, sql)
            if not rows:
                return [], []
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
            rejected = await _turned_down(scope)
        live = names_of(entities, aliases)
        sure = {str(e["id"]) for e in entities if e["status"] == CONFIRMED}
        ready: list[Claim] = []
        pending: list[Claim] = []
        gone: list[tuple[Claim, str]] = []  # their name was turned down
        for claim in map(claim_of, rows):
            wording = Extracted(
                claim.subject,
                claim.relationship,
                claim.object,
                claim.details,
                claim.lines,
                claim.how,
                claim.reading,
            )
            said = (
                [claim.subject]
                if condition_of(wording) is not None
                else [claim.subject, claim.object]
            )
            found = [live.by_key.get(name_key(n), ()) for n in said]
            unnamed = [n for n, f in zip(said, found, strict=True) if not f]
            if any(name_key(n) in rejected for n in unnamed):
                gone.append((claim, NAME_REJECTED_REASON))  # the DM said it isn't a name
            elif unnamed and claim.reason == WAITING_NAME_REASON:
                pending.append(claim)  # its name is not proposed yet: a later session does it
            elif unnamed:
                ready.append(claim)  # a name that went away: asking says why it can't be written
            elif all(any(e in sure for e, _ in f) for f in found):
                ready.append(claim)
            else:
                pending.append(claim)
        if gone:
            async with self._store._write(guild_id, campaign_id, EXTRACTOR) as w:
                for claim, reason in gone:
                    current = await w.get(CLAIMS, claim.id)
                    if current is not None and current["status"] == CLAIM_PROPOSED:
                        await w.update(
                            CLAIMS, claim.id, {"status": CLAIM_DROPPED, "reason": reason}
                        )
        return ready, pending

    async def get(self, guild_id: int, campaign_id: str, claim_id: str) -> Claim | None:
        async with self._store._read(guild_id, campaign_id) as scope:
            row = await scope.get(CLAIMS, claim_id, lock=False)
        return None if row is None else claim_of(row)

    async def placed_batch(self, guild_id: int, campaign_id: str, claim_id: str) -> int | None:
        """The change batch in which this reading was kept as a belief, if that keep has not been
        undone: found from the change log itself (claims are not logged, but the belief a keep
        writes is, and it carries the claim as its evidence, so another keep on the same
        connection is never mistaken for this one), so it holds when the answer that made the keep
        never got to record it (a retaken worker, #2412). None when it was never kept or was
        undone."""
        async with self._store._read(guild_id, campaign_id) as scope:
            claim = await scope.get(CLAIMS, claim_id, lock=False)
            if claim is None or claim["status"] != CLAIM_CONFIRMED:
                return None
            cur = await scope.conn.execute(
                "SELECT c.batch FROM memory_changes c WHERE c.guild_id = %s AND c.campaign_id = %s"
                " AND c.table_name = 'memory_beliefs' AND c.op = 'insert' AND c.source = 'dm'"
                " AND c.undoes IS NULL AND c.after->'evidence_ids' @> to_jsonb(%s::text)"
                " AND NOT EXISTS (SELECT 1 FROM memory_changes u WHERE u.guild_id = c.guild_id"
                " AND u.campaign_id = c.campaign_id AND u.undoes = c.batch)"
                " ORDER BY c.version DESC LIMIT 1",
                (guild_id, campaign_id, claim_id),
            )
            row = await cur.fetchone()
        return None if row is None else int(row["batch"])

    async def counts(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> dict[str, int]:
        """How many claims there are in each status (one grouped count, not the rows), after
        the ones that cite someone not recorded now are wiped, so a count never includes
        words the DM must not see."""
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        ready, pending = await self._sorted(guild_id, campaign_id)  # also drops the name-less
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT status, count(*) AS n FROM memory_claims"
                " WHERE guild_id = %s AND campaign_id = %s GROUP BY status",
                scope.ids,
            )
            found = {str(r["status"]): int(r["n"]) for r in await cur.fetchall()}
        # "proposed" is what the DM can answer now; the ones waiting on a new name are apart.
        if found.get(CLAIM_PROPOSED):
            found[CLAIM_PROPOSED] = len(ready)
            if not ready:
                del found[CLAIM_PROPOSED]
        if pending:
            found["names"] = len(pending)
        return found

    async def waiting_for_check(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> int:
        """How many proposed claims the DM could answer now, counted WITHOUT changing anything:
        `counts` first wipes the claims that cite someone not recorded now, which is right after
        a session but not at its start, when a player may be about to agree again (#1403). Only
        claims whose every speaker is recorded now are counted, and those waiting on a new name
        are left out, as `counts` leaves them out of "proposed". A claim `counts` would drop
        for a name turned down may still be counted here; the review drops it when it opens."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_claims WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'proposed' AND reason <> %s AND cardinality(speaker_ids) > 0"
                " AND speaker_ids <@ %s::bigint[] AND reading IS NULL",
                (*scope.ids, WAITING_NAME_REASON, list(opted_in)),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def forget_speaker(self, guild_id: int, user_id: int) -> int:
        """Stop recording me: in every campaign of the server, delete the claims that cite
        this person's words and were not turned into a fact (waiting, turned down or dropped).
        What the DM confirmed is the DM's fact and stays. A reading the DM placed is held to the
        same line (`forget_readings`): what the DM kept stays, a guess that only this person's
        words produced goes with what it made. Returns how many claims went."""
        async with self._store._db.guild(guild_id) as conn:
            readings = await forget_readings.forget(conn, guild_id, user_id)
            cur = await conn.execute(
                "DELETE FROM memory_claims WHERE guild_id = %s AND %s = ANY(speaker_ids)"
                " AND status <> 'confirmed' RETURNING id",
                (guild_id, user_id),
            )
            gone = [str(r["id"]) for r in await cur.fetchall()]
            await evidence.placeholder_claims(conn, guild_id, gone)  # their links keep no target
            # and what the DM confirmed stays, without this person's line references (#2676)
            await evidence.scrub_claim_lines(conn, guild_id, user_id=user_id)
            return len(gone) + readings

    async def reject(self, guild_id: int, campaign_id: str, claim_id: str) -> Claim:
        """The DM says no: it is not asked again, and nothing is written to memory."""
        async with self._store._write(guild_id, campaign_id, DM) as w:
            row = await w.get(CLAIMS, claim_id)
            if row is None or row["status"] != CLAIM_PROPOSED:
                raise MemoryRuleError(GONE)
            row = await w.update(CLAIMS, claim_id, {"status": CLAIM_REJECTED, "decided_at": w.now})
            return claim_of(row)

    @staticmethod
    async def reopen_in(w: Changes, claim_id: str) -> None:
        """The DM took its answer back (↩️ Undo on a remembered fact, #2410): the fact is gone, so
        the question waits again. The claims are not in the change log, so the undo's own write
        (`MemoryStore.undo(after=...)`) puts it back, and a failure rolls both back."""
        row = await w.get(CLAIMS, claim_id)
        if row is not None and row["status"] == CLAIM_CONFIRMED:
            await w.update(
                CLAIMS, claim_id, {"status": CLAIM_PROPOSED, "decided_at": None, "fact_id": None}
            )
            # A guess placed on a connection that other guesses share left a link from that
            # connection to this claim (links are not in the change log, so the Undo did not take
            # it): the connection no longer stands on a claim that waits again. Links on a belief
            # went with the belief, and a connection the placing made went whole.
            await w.conn.execute(
                "DELETE FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'claim' AND target_id = %s AND relation_id IS NOT NULL",
                (*w.ids, claim_id),
            )

    async def place(
        self,
        guild_id: int,
        campaign_id: str,
        claim_id: str,
        *,
        opted_in: Recorded,
    ) -> Any:
        """The DM places a reading as a belief (`placing.place`); `confirm` refuses a reading."""
        from dmbot.memory import placing  # (it builds on this module, so it is read late)

        return await placing.place(self, guild_id, campaign_id, claim_id, opted_in=opted_in)

    async def confirm(
        self,
        guild_id: int,
        campaign_id: str,
        claim_id: str,
        *,
        opted_in: Collection[int],
        before: Callable[[Changes], Awaitable[None]] | None = None,
        secret: bool = False,
    ) -> Confirmed:
        """The DM says yes: the fact is written as the DM's own, through the store (so every
        rule and check runs as for any fact), and the claim keeps which fact it became. A fact
        the rules refuse leaves the question waiting and says why. `opted_in` is who may be
        recorded now: if anyone whose words it holds has stopped, the claim is deleted instead
        (the stored row is untrusted too, so the plan and instruction checks run again).
        `before` runs first inside the same transaction (a continuity answer, #1320, ends the
        fact it replaces or brings back what had ended): all of it happens, or none.
        `secret`: the DM's "truth but secret" (#2410): The truth holds the connection and the
        players are not shown it. A condition ("Roan is dead") has no secret flag, so it is
        never written secret."""
        claim = await self.get(guild_id, campaign_id, claim_id)
        if claim is None or claim.status != CLAIM_PROPOSED:
            raise MemoryRuleError(GONE)
        if claim.reading is not None:
            # A theory said "yes" to as a fact would be a lie in the DM's own voice: a reading is
            # placed as a belief, which is its own step (#2412).
            raise MemoryRuleError(A_READING)
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
                await evidence.placeholder_claims(w.conn, guild_id, [claim_id])  # (#2662)
            raise MemoryRuleError(STOPPED_NOW)
        async with self._store._read(guild_id, campaign_id) as scope:
            onto = await _load_ontology(scope)
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
        names = names_of(entities, aliases)
        waiting_ids = {str(e["id"]) for e in entities if e["status"] == PROPOSED}
        # Asked again against what is known now: a name may have been merged or renamed since.
        who = names.one(claim.subject)
        if isinstance(who, str):
            raise MemoryRuleError(f"Can't write that now: {who}.")
        if who[0] in waiting_ids:
            raise MemoryRuleError(NAME_FIRST)
        resolved: tuple[PredicateTerm, bool, str] | None = None
        other: tuple[str, str] | str = ""
        condition = condition_of(wording)
        if condition is None:
            other = names.one(claim.object)
            if isinstance(other, str):
                raise MemoryRuleError(f"Can't write that now: {other}.")
            if other[0] in waiting_ids:
                raise MemoryRuleError(NAME_FIRST)
            resolved = place_relationship(onto, claim.relationship, who[1], other[1])
        # One transaction (#1301): the question is taken, the fact written and the claim told
        # which fact it became, or none of it happens. The campaign's write lock is held, so a
        # second press waits and then finds the question gone. A fact the rules refuse rolls
        # the whole thing back and leaves the question waiting. The check at the top of
        # `confirm` is only an early exit: this one, under the lock, is the one that counts.
        async with self._store._write(guild_id, campaign_id, DM) as w:
            current = await w.get(CLAIMS, claim_id)
            if current is None or current["status"] != CLAIM_PROPOSED:
                raise MemoryRuleError(GONE)
            if before is not None:
                await before(w)
            if condition is not None:
                state, flags = await self._store.set_condition_in(
                    w, who[0], condition, claim.session_started_at
                )
                fact_id = who[0] if state is None else state.id
            else:
                assert resolved is not None and not isinstance(other, str)
                term, from_object, label = resolved
                subject_id, object_id = (other[0], who[0]) if from_object else (who[0], other[0])
                # On the catch-all, the words it was said in are the fact's label and nothing
                # else: the card sets `detail` between the two names, so side notes can't go there.
                relation, flags = await self._store.add_relation_in(
                    w, subject_id, term.key, object_id, confidence=1.0, status=CONFIRMED,
                    detail=label or claim.details, from_session_at=claim.session_started_at,
                    secret=secret,
                )  # fmt: skip
                fact_id = relation.id
            row = await w.update(
                CLAIMS,
                claim_id,
                {"status": CLAIM_CONFIRMED, "decided_at": w.now, "fact_id": fact_id},
            )
            batch = w.batch
        return Confirmed(claim_of(row), tuple(flags), batch)
