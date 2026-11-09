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
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from dmbot.memory import (
    descriptions,
    evidence,
    forget_readings,
    graph_review,
    kin,
    knows_how,
    link,
    one_off,
    place_words,
    reputation_words,
    scenes,
    unique_item,
    who_answers,
)
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory._changes import ALIASES, CLAIMS, ENTITIES, RELATIONS, STATES, Changes
from dmbot.memory.clues import KNOWS as KNOWS_PREDICATE
from dmbot.memory.list_names import split_list
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
    PLAYER_CHARACTER,
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
from dmbot.memory.store import (
    TRAVELS_WITH,
    MemoryStore,
    _companion_problem,
    _load_ontology,
    _new_alias,
    held_back_between,
    reveal_stated_in,
)
from dmbot.memory.voice import (
    EFFECT,
    EFFECT_NOTE,
    OWN,
    PARTY,
    PARTY_REASON,
    split_effect_note,
    voice,
)
from dmbot.names import not_names
from dmbot.word_end import at_word_end

logger = logging.getLogger(__name__)

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
# (#3244) A blow, a sighting or a chase lasts one moment; it is not a tie between two characters.
ONE_OFF_REASON = "a single action, not a lasting tie"
PREDICTION_REASON = "what is expected, not something that is so"
INJECTION_REASON = "it reads like an instruction, not a story"
NO_SPEAKER_REASON = "a line it cites has no known speaker"
STOPPED_REASON = "a speaker it cites is not being recorded now"
STOPPED_NOW = "Someone who said that has stopped being recorded, so it can't be kept."
ODD_NOW = "That doesn't look like something said in the game any more."
UNKNOWN_NAME_REASON = "a name in it is not one DMbot knows yet"
# (#3373) Only one particular thing is tracked: "Garret's sled", not "a sled".
ITEM_REASON = "an item that is not one particular thing"
# A kind of creature the rules already hold ("owlbears", "an owlbear") is not a name in the story
# (#3634): the lore read leaves it out too (`background.in_the_rules`); a creature the story names
# or sets apart ("Owlbear Mama") is not a kind and stays an entry.
RULES_REASON = "a kind of creature the rules already hold"
TWO_ENTRIES_REASON = "a name in it could be two different entries"
SAME_ENTRY_REASON = "it connects an entry to itself"
STATE_REASON = "that entry can't be in that condition"
NAME_REJECTED_REASON = "the DM said a name in it isn't a name"
NOT_A_NAME_REASON = "a number, time, address or plain word in it is no name"  # #3394
SECRET_NAME_REASON = "a name in it is one only the DM may use"
NAME_FIRST = "Say what the new name is first (in Check new names)."
# A claim whose name was past the session's limit is kept, waiting for its name to be proposed in
# a later session (#1367). It is a proposed claim with this as its `reason`, which no claim that
# is asked or dropped carries.
WAITING_NAME_REASON = "waiting for its name to be proposed"
WAITING_NAME_CLAIMS = 500  # how many such claims one campaign keeps; past that they are let go
NEW_NAME_LIMIT = 20  # new names one session sends to the DM; the rest wait for next time
# A claim with a player character at one end is never dropped unseen (#3238): when a name in it
# could mean two entries, it is kept for the DM's answer, with its lines, instead of being dropped.
UNPLACED_REASON = "kept for your answer: a name in it could be two different entries"
NEW_PERSON = "new-person"  # stands for the unnamed person a Keep is about to make (#3431)
TRAVELS_REASON = "a companion travels with a player character or the party, in that order"  # #3176
DESCRIBED_REASON = "kept for your answer: a person the story never names"  # #3431
UNPLACED_LIMIT = 20  # such claims one session keeps; the rest are counted and not stored
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
    # The entries the DM picked for a name that could mean two (#3238), ahead of the words.
    subject_picked: str | None = None
    object_picked: str | None = None
    # Why a player character's line is "something that happened to them" (#2562), apart from the
    # details: the note is stored run on to them and taken off here (#3624).
    note: str = ""


def claim_of(r: Mapping[str, Any]) -> Claim:
    """A stored claim from its row."""
    details, note = split_effect_note(str(r["details"] or ""))
    return Claim(
        r["id"], r["subject"], r["relationship"], r["object"], details, r["how"],
        tuple(r["lines"]), tuple(r["speaker_ids"]), r["source"], r["prompt_version"], r["model"],
        r["status"], r["reason"], r["session_started_at"], r["created_at"], r["decided_at"],
        r["fact_id"], r.get("reading"), r.get("subject_picked"), r.get("object_picked"), note,
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
    # About the player characters (#3238), counts only:
    pc_named: int = 0  # new names made for a claim about a player character, past the limit
    kept_unplaced: int = 0  # claims about a player character kept for the DM's answer
    unplaced_over_cap: int = 0  # ... and those past the session's cap, counted and not stored
    # About people the story describes but never names (#3431), counts only:
    described: int = 0  # claims kept for the DM's answer ("Keep a person with no name")
    described_held: int = 0  # ... and those past the session's cap, held for the next read (#3749)
    described_dropped: int = 0  # none since #3736 (every described end is kept); stays for the logs
    # About how names were said (#3634), counts only:
    rules_dropped: int = 0  # a claim about a kind of creature the rules hold: not kept
    tidied: int = 0  # claims with a name that lost a leading "a"/"an" or a bracketed note


@dataclass(frozen=True, slots=True)
class EntryOption:
    """An entry a name could mean, with what tells it from the others: its kind and a short note
    (the DM's own description, else its other names). Never a line of a transcript."""

    id: str
    name: str
    kind: str
    note: str


@dataclass(frozen=True, slots=True)
class WhichOne:
    """A name in a claim kept for the DM's answer (#3238) that could mean two entries: the words as
    said, which side of the claim they are on ("subject" or "object"), and the entries they could
    be. For the "Which one?" choice."""

    words: str
    side: str
    entries: tuple[EntryOption, ...]


@dataclass(frozen=True, slots=True)
class Confirmed:
    claim: Claim
    flags: tuple[Flag, ...]  # problems the store's checks raised for the DM
    batch: int | None = None  # the change-log batch, for the ↩️ Undo of a fact (#2410)
    # A connection the story held back that this claim stated, now shown to the players (#3963 c):
    # the caller says so to the DM.
    shown: bool = False


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


# Two characters KNOW each other and how kindly is a reputation word (#3175, #3181): the old
# friend, ally and enemy relationships are read as `knows` with the word they always meant.
_FILLER = frozenset({"is", "are", "was", "were", "a", "an", "the"})
_OLD_FEELING = {"ally_of": reputation_words.FRIENDLY, "enemy_of": reputation_words.HOSTILE}


def feeling_of(
    onto: Ontology, text: str, details: str = "", ends: tuple[str, str] = ("character", "character")
) -> tuple[PredicateTerm, str] | None:
    """`knows` and the reputation word, when a claim's relationship says how two characters feel:
    "knows" with a word from the nine in its details, the old "friend of", "ally of" and "enemy of"
    (read as friendly and hostile), or one of the nine words said as the relationship ("trusts",
    "distrusts"). None: not a feeling, the relationship goes on as it is. The word is a proposal
    that waits for the DM's yes like the claim; an off-list word is dropped, never guessed.
    Only between two characters (`ends`): an alliance or a war between groups stays `ally_of` and
    `enemy_of`, and a character knowing a place or an item has no feeling to carry."""
    knows = onto.predicates.get("knows")
    if knows is None or knows.status != "active" or set(ends) != {"character"}:
        return None
    word = reputation_words.word_of(text)
    if word is not None:
        return knows, word
    known = resolve_relationship(onto, text)
    if known is None:
        # ("is a friend of", "are enemies of": the wording around the old kind is not part of it)
        bare = [w for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in _FILLER]
        known = resolve_relationship(onto, " ".join(bare)) if bare else None
        if known is None or known[0].key not in _OLD_FEELING:
            return None
    term = known[0]
    if term.key in _OLD_FEELING:
        return knows, _OLD_FEELING[term.key]
    if term.key == "knows":
        return knows, reputation_words.word_of(details) or ""
    return None


def _fits(
    known: tuple[PredicateTerm, bool], subject_kind: str, object_kind: str, onto: Ontology
) -> bool:
    """Whether a known relationship's two ends are kinds it takes. A companion link
    (`travels_with`) is judged by the store's own rule afterwards, so it is never turned into
    the catch-all here (#3176)."""
    term, from_object = known
    if term.key == TRAVELS_WITH:
        return True
    s_kind, o_kind = (object_kind, subject_kind) if from_object else (subject_kind, object_kind)
    return (not term.subject_types or onto.is_any(s_kind, term.subject_types)) and (
        not term.object_types or onto.is_any(o_kind, term.object_types)
    )


def place_relationship(
    onto: Ontology,
    text: str,
    subject_kind: str,
    object_kind: str,
    tally: Tally | None = None,
    details: str = "",
) -> tuple[PredicateTerm, bool, str]:
    """The relationship a claim's words go on, whether they are said from the object's side,
    and the label to keep with the fact ("" when the relationship's own wording is the label).

    A relationship DMbot knows is used as it is. Words it does not know go to the steward
    (#1303): an existing term it recognises (a near spelling, say) is reused; a rules word, a
    term the meaning check cannot yet vouch for, or anything else it refuses goes on the
    catch-all `associated_with` with the claim's own words as the label. A claim is never lost
    for its relationship, and DMbot never makes a new relationship term from a claim yet:
    until the meaning check (embeddings) exists, an unchecked new term counts as refused."""
    feeling = feeling_of(onto, text, details, (subject_kind, object_kind))
    if feeling is not None:
        return (
            feeling[0],
            False,
            feeling[1],
        )  # (both ways: a feeling is said once, from the subject)
    # "works in", "grew up in" and the like are the place tie with a word (#3363), not new terms
    # (only where the two ends fit the place tie: "visited" between two characters is not one)
    located = onto.predicates.get("located_in")
    if (
        located is not None
        and located.status == "active"
        and place_words.way_of(text) is not None
        and onto.is_any(subject_kind, located.subject_types)
        and onto.is_any(object_kind, located.object_types)
    ):
        return located, False, ""
    known = resolve_relationship(onto, text)
    if known is not None and _fits(known, subject_kind, object_kind, onto):
        return known[0], known[1], ""
    # (a known word whose two ends don't fit, "performed" between two characters, is not lost: it
    # goes on the catch-all with its own words, like a word DMbot does not know, #3033)
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
    if (
        isinstance(decision, Reused)
        and decision.existing in onto.predicates
        and _fits((onto.predicates[decision.existing], False), subject_kind, object_kind, onto)
    ):
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
    # Each entry's source by id: only the party entry is told apart by it (#3176)
    sources: Mapping[str, str] = field(default_factory=dict)

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
        # An entry DMbot made for a described person ("the woman") places nothing by that name
        # until the DM makes it an unnamed person or names it (#3431); a session claim about such
        # a person no longer makes one, so this only holds back the ones made before. One the DM
        # typed, and an unnamed person's stored label, are left to their own rules.
        if not (
            descriptions.is_described(str(e["name"]))
            and not descriptions.matches_alone(str(e["name"]), "full", str(e.get("source") or ""))
        ):
            seen.setdefault(name_key(str(e["name"])), {})[str(e["id"])] = str(e["type"])
    for a in aliases:
        # (a description such as "the woman" never places a line by itself: #3431)
        if str(a["entity_id"]) in kinds and descriptions.matches_alone(
            str(a.get("text") or "Name"), str(a.get("kind") or "full"), str(a.get("source") or "")
        ):
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
    sources = {str(e["id"]): str(e.get("source") or "") for e in entities}
    return Names({k: tuple(v.items()) for k, v in seen.items()}, hidden, roles, sources)


MAX_EXPANDED = 12  # claims one list claim may become: more is a sentence, not a list of names


def _group_led(subjects: list[str], names: Names) -> tuple[str, list[str]] | None:
    """(group, members) when the claim's subject is a list whose first part is a known group and
    whose other parts are names; None otherwise. Only the subject side reads as membership:
    "A met the Ash Wolves, Tavin" says nothing about who belongs."""
    if len(subjects) < 2 or len(subjects) - 1 > MAX_EXPANDED:
        return None
    found = names.by_key.get(name_key(subjects[0]), ())
    if len(found) == 1 and found[0][1] == "faction":
        return subjects[0], subjects[1:]
    return None


def expand_lists(claims: Sequence[Extracted], names: Names) -> tuple[list[Extracted], int]:
    """Claims whose subject or object is a list of names ("Aldmoor, Quillgate, Fenwick") as one
    claim for each part (#3383), so a list is never offered to the DM as one name. A part that is
    already known joins its entry as usual. Returns (the claims, how many lists were split): a
    count, never a name. A bare "and" splits only when every part is known or this same read
    named each part alone (`list_names.split_list`). A claim that would become more than
    MAX_EXPANDED, or pair a name with itself, is kept as it was said or left out of the pairs;
    the same claim twice (a part also said alone) is kept once."""
    alone = {name_key(n) for c in claims for n in (c.subject, c.object)}
    out: list[Extracted] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    split = 0
    for c in claims:
        subjects = split_list(c.subject, known=names.by_key, offered=alone - {name_key(c.subject)})
        objects = split_list(c.object, known=names.by_key, offered=alone - {name_key(c.object)})
        led = _group_led(subjects, names)
        if led is not None:
            # "the Ash Wolves, Tavin and Mira": a known group followed by its members. The members
            # are proposed as members of it (#3383, part 2); the list is not a name.
            split += 1
            group, members = led
            out.extend(
                replace(c, subject=m, relationship="member of", object=group, details="")
                for m in members
            )
            continue
        if (len(subjects) == 1 and len(objects) == 1) or len(subjects) * len(
            objects
        ) > MAX_EXPANDED:
            seen.add((name_key(c.subject), c.relationship, name_key(c.object), c.lines))
            out.append(c)
            continue
        split += 1
        for s in subjects:
            for o in objects:
                key = (name_key(s), c.relationship, name_key(o), c.lines)
                if name_key(s) == name_key(o) or key in seen:
                    continue
                seen.add(key)
                out.append(replace(c, subject=s, object=o))
    return out, split


def _entry_of(names: Names, words: str, picked: str | None) -> tuple[str, str] | str:
    """The entry a name in a stored claim means: the one the DM picked for it (#3238), if it is
    still one the words could be, else what `Names.one` finds (or the reason there is no one)."""
    if picked is not None:
        for entry in names.by_key.get(name_key(words), ()):
            if entry[0] == picked:
                return entry
    return names.one(words)


def judge(    claim: Extracted, onto: Ontology, names: Names, speakers: Sequence[int | None],
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
    # A prediction is never a fact, even from the DM; a single action is not a tie. Both are
    # decided before the names, so a group that only ever "swings at" someone is never made into
    # an entry (#3244). A phrase DMbot already has a relationship for (held, serves, carries) is
    # a lasting state and goes on as it did: only a phrase it cannot place is dropped.
    # A belief or a reading (`claim.reading`) is what someone thinks happened or expects: "Tor
    # believes Vex shot the duke" is a clue, not a tie, so these checks are for claims only.
    if claim.reading is None:
        if one_off.is_prediction(claim.relationship):
            return PREDICTION_REASON
        if (
            one_off.is_one_off(claim.relationship)
            and resolve_relationship(onto, claim.relationship) is None
            and feeling_of(onto, claim.relationship, claim.details or "") is None
        ):
            return ONE_OFF_REASON
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
    term, said_from_object, label = place_relationship(
        onto, claim.relationship, who[1], other[1], tally, claim.details or ""
    )
    s_kind, o_kind = (other[1], who[1]) if said_from_object else (who[1], other[1])
    if term.key == TRAVELS_WITH:
        # The store refuses a companion link whose ends are wrong (#3176), so the DM is never
        # asked a question whose "yes" it would turn down. The reader is a session reader.
        first, second = (other, who) if said_from_object else (who, other)
        problem = _companion_problem(
            {"type": first[1], "role": names.roles.get(first[0], (None, None))[0]},
            {
                "type": second[1],
                "role": names.roles.get(second[0], (None, None))[0],
                "source": names.sources.get(second[0], ""),
            },
            EXTRACTOR,
        )
        if problem is not None:
            return TRAVELS_REASON
    if term.subject_types and not onto.is_any(s_kind, term.subject_types):
        return f"{term.label} doesn't fit those two"
    if term.object_types and not onto.is_any(o_kind, term.object_types):
        return f"{term.label} doesn't fit those two"
    # Who speaks for a character (#2562): a PC's own choice and feeling come from its player, an
    # NPC's from the DM. A player's word about someone else's character is what they believe, never
    # the character's own, so it is not offered to the DM as a truth.
    if _voice_of(names, who, other, said_from_object, term, claim, speakers, dms, label) == PARTY:
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
    feeling: str = "",
) -> str:
    """`own`, `effect` or `party` (`memory.voice`) for a claim about the character it is about.
    `feeling`: the reputation word a `knows` claim carries ("" when it carries none)."""
    holder = other if said_from_object else who
    if holder[1] != "character":
        return OWN  # (a group's attitude stays the DM's)
    role, plays = names.roles.get(holder[0], (None, None))
    return voice(role, term.key, claim.how, speakers, plays, dms, feeling=bool(feeling))


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
    term, said_from_object, label = place_relationship(
        onto, claim.relationship, who[1], other[1], details=claim.details or ""
    )
    if _voice_of(names, who, other, said_from_object, term, claim, speakers, dms, label) != EFFECT:
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
    # (a lie also names who lied: two accusations of different people are two questions)
    held = (
        None
        if reading is None
        else (reading.get("view"), reading.get("status"), reading.get("liar"))
    )
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


def _described_shape(text: str) -> str | None:
    """`not_names.shape_of` for a description (#3736): its head phrase only ("the barmaid" of "the
    barmaid with red hair"), since the tail says which one, and a counted group is a group ("three
    hooded figures"), never a number."""
    shape = not_names.shape_of(descriptions.head_phrase(text))
    if shape == "clause" or (shape == "number" and descriptions.is_described_group(text)):
        return None
    return shape


def _unnamed_kind(end: str) -> str:
    """What an unnamed end is made as: a particular group ("the loud sailors in the corner") is a
    group, anyone else a person (#3736)."""
    return "faction" if descriptions.is_described_group(end) else "character"


def _only_described_unknown(claim: Extracted, names: Names) -> bool:
    """Is every end of the claim that matches no entry a person the story describes but never
    names (#3736)? Then the claim waits for the DM's Keep, which makes or finds them, rather than
    for a name to be proposed."""
    unknown = _unknown_names(claim, names)
    return bool(unknown) and all(descriptions.is_described(n) for n in unknown)


def _a_kind_of_creature(name: str) -> bool:
    """Is this new name a kind of creature the rules hold ("owlbears", "an owlbear", "three
    goblins"), in any edition? A proper name ("Owlbear Mama") is not (`rules.is_monster` looks at
    the kind alone)."""
    from dmbot.rules import index  # late: the rules data loads on first use

    # said as a kind, not as a name: lower case ("owlbears", "an owlbear", "three goblins"); a
    # capitalised word ("Azer", "Wolf", "The Owlbear", "Owlbear Mama") may be somebody's name and
    # is left to the DM. A lower-case article may stand before the kind; nothing else may be a
    # capital.
    rest = name
    for article in ("a ", "an ", "the "):
        if name.startswith(article):
            rest = name[len(article) :]
            break
    if rest != rest.casefold():
        return False
    return index.srd().is_monster(name)


# A trailing bracket is dropped only when it reads as a note about the name ("(no name given yet)",
# "(either Ok or Nysarra)", "(maybe two)", "(unknown)"), never as part of what tells two things
# apart ("Mira (the baker)", "Dragon (Red)").
_BRACKET_TAIL = re.compile(
    r"\s*[(\[][^()\[\]]*\b(?:no name|unnamed|unknown|either|maybe|perhaps|possibly|probably|"
    r"not sure|unsure|name unknown|or)\b[^()\[\]]*[)\]]\s*$|\s*[(\[]\s*\?\s*[)\]]\s*$",
    re.IGNORECASE,
)
# "a" or "an" as an article: lower case, before a lower-case word ("an owlbear"). "A Ling", "An Lu"
# and "A. Smith" are names, and a capitalised "A beggar woman" is left as it is rather than guess.
_INDEFINITE = re.compile(r"^(?:a|an)\s+(?=[a-z])")


def tidy_name(name: str) -> str:
    """How a name is judged (#3634): a trailing bracketed note is not part of it ("(no name given
    yet)", "(either Ok or Nysarra)") and a leading "a" or "an" is not either ("an owlbear"). "The"
    is kept: it is part of a title ("The Everlasting Rime"). Nothing is stripped that would leave
    nothing."""
    out = name.strip()
    for _ in range(3):
        cut = _BRACKET_TAIL.sub("", out)
        if not cut or cut == out:
            break
        out = cut
    out = _INDEFINITE.sub("", out)
    return out or name.strip()


def tidy_ends(claim: Extracted, known: Collection[str] = ()) -> Extracted:
    """The claim with both ends tidied (`tidy_name`); the same claim when nothing changed. A name
    the campaign already holds as said ("a rusty dagger", its own entry) is left as it is: `known`
    holds the name keys it has."""
    subject = claim.subject if name_key(claim.subject) in known else tidy_name(claim.subject)
    obj = claim.object if name_key(claim.object) in known else tidy_name(claim.object)
    if (subject, obj) == (claim.subject, claim.object):
        return claim
    return replace(claim, subject=subject, object=obj)


def _generic_items(
    onto: Ontology, claim: Extracted, unknown: Sequence[str], said: Mapping[str, str]
) -> bool:
    """Is a new name in this claim an item that is not one particular thing? Only a name the
    relationship fixes as an item is judged (`implied_kind`); the words it was said in help
    (`unique_item`): the judgement is "more likely than not", and nobody is asked."""
    context = " ".join(said.get(r, "") for r in claim.lines)
    for name in unknown:
        side = "subject" if name_key(name) == name_key(claim.subject) else "object"
        if implied_kind(onto, claim, side) == "item" and not unique_item.keeps(name, context):
            return True
    return False


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


def which_choices(
    row: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
) -> list[WhichOne]:
    """The "Which one?" choices of a kept claim's row, from the campaign's live entries and other
    names (#3238): one `WhichOne` for each name in it that could mean two entries and has not been
    picked for. Pure, so the website reads the same choices from the facts a database door sends it
    (#3463). A secret name is never used, even to tell two entries apart. The entries come in a
    fixed order (name, kind, id) and each note's other names sorted, whatever order the rows were
    read in: the database gives none without ORDER BY, and the DM's list must not reorder itself
    between two looks (#3776)."""
    by_id = {str(e["id"]): e for e in entities}
    others: dict[str, list[str]] = {}
    for a in aliases:
        if not a.get("secret"):  # never a secret name, even to tell two entries apart
            others.setdefault(str(a["entity_id"]), []).append(str(a["text"]))
    for texts in others.values():
        texts.sort(key=lambda t: (t.casefold(), t))
    names = names_of(entities, aliases)
    out: list[WhichOne] = []
    for side in ("subject", "object"):
        found = names.by_key.get(name_key(str(row[side])), ())
        if len(found) < 2 or row[f"{side}_picked"] is not None:
            continue
        options = []
        for entry_id, _kind in found:
            e = by_id.get(entry_id)
            if e is None:
                continue
            note = (
                " ".join(str(e["description"]).split())[:80]
                or ", ".join(
                    t for t in others.get(entry_id, []) if name_key(t) != name_key(str(e["name"]))
                )[:80]
            )
            options.append(EntryOption(entry_id, str(e["name"]), str(e["type"]), note))
        options.sort(key=lambda o: (o.name.casefold(), o.kind, o.id))
        out.append(WhichOne(str(row[side]), side, tuple(options)))
    return out


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
        kept_unplaced = unplaced_over_cap = 0
        described = described_held = described_dropped = 0
        described_taken = 0  # held claims about people with no name, asked in this read
        rules_dropped = tidied = 0
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
                # oldest first, so a held claim is never passed over for a newer one (#3749)
                for r in await w.select(CLAIMS, " AND status = 'proposed' ORDER BY created_at, id")
                if r["reason"] == WAITING_NAME_REASON and make_names
            ]  # fmt: skip  (a pass that makes no names leaves earlier waiting claims alone)
            people_held = resolved = 0  # held claims about people with no name; all ends known

            players = {
                str(e["id"])
                for e in entities
                if e.get("role") == PLAYER_CHARACTER and e["status"] in (PROPOSED, CONFIRMED)
            }

            def about_a_player(claim: Extracted) -> bool:
                """Does a player character stand at one end of the claim (#3238)?"""
                for n in (claim.subject, claim.object):
                    found = names.by_key.get(name_key(n), ())
                    if len(found) == 1 and found[0][0] in players:
                        return True
                return False

            def judged(claim: Extracted) -> str:
                cited = [speakers.get(ref) for ref in claim.lines]
                reason = judge(claim, onto, names, cited, opted_in, tally, dms)
                if reason == TWO_ENTRIES_REASON and about_a_player(claim):
                    return UNPLACED_REASON  # never dropped unseen: kept for the DM's answer
                return reason

            # First pass: the rules, with the names as they are. A claim that fails only for a
            # name DMbot does not know is a candidate for the linker.
            work: list[tuple[Extracted, str, list[str]]] = []  # claim, reason, unknown names
            # (#3634) "an owlbear (maybe two)" is "owlbear": tidied before a list is split, so a
            # bracketed note with a comma in it goes whole
            tidy = [tidy_ends(x, names.by_key) for x in claims[:limit]]
            tidied = sum(1 for was, now in zip(claims, tidy, strict=False) if was != now)
            claims, lists = expand_lists(tidy, names)
            # How many lines offer each name (a name two lines offer is more likely a real one)
            lines_by_name: dict[str, set[str]] = {}
            not_a_name: Counter[str] = Counter()
            for c in claims:
                for n in (c.subject, c.object):
                    lines_by_name.setdefault(name_key(n), set()).update(c.lines)
            for r in leftovers:
                for n in (r["subject"], r["object"]):
                    lines_by_name.setdefault(name_key(n), set()).update(r["lines"])
            if lists:
                logger.info("name lists split: %d", lists)  # a count, never a name
            for claim in claims:  # (the limit was applied once, before the lists were split)
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
                    elif any(
                        kinds := [
                            # (a person or group the story describes is no name and is not meant
                            # to be one, #3736: only the shape of its head phrase can drop it)
                            _described_shape(n)
                            if descriptions.is_described(n)
                            else not_names.why_not(
                                n,
                                text="\n".join((said or {}).get(r, "") for r in claim.lines),
                                offered=len(lines_by_name.get(name_key(n), ())),
                                cued=cued,
                            )
                            for n in unknown
                        ]
                    ):  # a number, measure, time, address or plain word (#3394)
                        for k in kinds:
                            if k:
                                not_a_name[k] += 1
                        reason = NOT_A_NAME_REASON
                        unknown = []
                    elif any(_a_kind_of_creature(n) for n in unknown):
                        reason, unknown = RULES_REASON, []
                        rules_dropped += 1
                    elif _generic_items(onto, claim, unknown, said or {}):
                        # A new item that is clearly everyday gear is not made into an entry, so
                        # its claim goes with it (#3373): no owns, carries or kept-in is stored.
                        reason, unknown = ITEM_REASON, []
                    elif any(descriptions.is_described(n) for n in unknown):
                        # A person the story describes but never names ("the woman") is never
                        # made into a name to check (#3431), and is never dropped either (owner,
                        # 2026-11-07, #3736): Keep makes or finds each described end. Every
                        # unknown end described (one beside a known end, two in a chain, or one
                        # alone with a fact about them): kept for the DM's answer. A described
                        # end beside a new name: the name goes to the names to check, and the
                        # claim waits on it like any claim with a new name.
                        named = [n for n in unknown if not descriptions.is_described(n)]
                        if condition_of(claim) is None and name_key(claim.subject) == name_key(
                            claim.object
                        ):
                            reason, unknown = SAME_ENTRY_REASON, []  # ("the woman knows the woman")
                        elif named:
                            unknown = named
                        else:
                            reason = DESCRIBED_REASON
                            unknown = []
                work.append((claim, reason, unknown))
            # Claims kept from an earlier session for a name that was past its limit come first:
            # their names are proposed before this session's own (#1367).
            old: list[tuple[dict[str, Any], Extracted, list[str]]] = []
            for r in leftovers:
                kept = tidy_ends(
                    Extracted(
                        r["subject"], r["relationship"], r["object"], r["details"],
                        tuple(r["lines"]), r["how"], r.get("reading"),
                    ),
                    names.by_key,
                )  # fmt: skip
                if not r["speaker_ids"] or any(x not in opted_in for x in r["speaker_ids"]):
                    continue  # its speaker stopped: `purge_stopped` wipes it when it is read
                # a described end is never made into a name to check (#3431), here either: only
                # the claim's new names are proposed, and Keep makes the person later (#3736)
                unknown = [
                    n for n in _unknown_names(kept, names) if not descriptions.is_described(n)
                ]
                if any(name_key(n) in refused_names for n in unknown):
                    await w.update(
                        CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": NAME_REJECTED_REASON}
                    )
                elif any(name_key(n) in skip or not_names.is_not_name(n) for n in unknown):
                    await w.update(
                        CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": UNKNOWN_NAME_REASON}
                    )
                elif unknown:
                    # (#3394) this claim's own lines are not part of this read, so only a shape
                    # (a number, measure, time or lone address) drops it; a plain word is judged
                    # on the lines of a read that has them
                    kinds_old = [not_names.shape_of(n) for n in unknown]
                    if any(kinds_old):
                        for k in kinds_old:
                            if k:
                                not_a_name[k] += 1
                        await w.update(
                            CLAIMS,
                            r["id"],
                            {"status": CLAIM_DROPPED, "reason": NOT_A_NAME_REASON},
                        )
                    else:
                        old.append((r, kept, unknown))
                elif _only_described_unknown(kept, names):
                    people_held += 1
                    old.append((r, kept, unknown))  # (its name is known now: asked below)
                elif not _unknown_names(kept, names):
                    # every end is known now (the DM named it meanwhile, or a held person's words
                    # came to match an entry): judged again below, so it never waits unseen
                    resolved += 1
                    old.append((r, kept, unknown))
            old_mentions = [name_key(n) for _, _, unknown in old for n in unknown]
            chosen_old, left_old = link.most_mentioned(old_mentions, new_names)
            fresh = [name_key(n) for _, _, unknown in work for n in unknown]
            fresh = [m for m in fresh if m not in chosen_old]
            # A new name in a claim about a player character is always made, ahead of the limit
            # (#3238): a family member narrated about one of the table's characters is likely to
            # matter later. The rest share what is left of the limit.
            about_players = {
                name_key(n) for claim, _, unknown in work if about_a_player(claim) for n in unknown
            }
            for_players = list(dict.fromkeys(m for m in fresh if m in about_players))
            others = [m for m in fresh if m not in about_players]
            chosen_new, left_new = link.most_mentioned(
                others, max(0, new_names - len(chosen_old) - len(for_players))
            )
            chosen_new = [*for_players, *chosen_new]
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
                described_left = _only_described_unknown(kept, names)
                if _unknown_names(kept, names) and not described_left:
                    continue
                holders = [int(x) for x in r["speaker_ids"]]
                reason = judge(kept, onto, names, holders, opted_in, tally, dms)
                if described_left and reason == UNKNOWN_NAME_REASON:
                    # its new name is made and the rest is described: asked now, and Keep makes
                    # the person (#3736), under the same per-session cap as this session's own
                    if described >= UNPLACED_LIMIT:
                        continue  # (stays held for its name's sake; asked in a later read)
                    await w.update(CLAIMS, r["id"], {"reason": DESCRIBED_REASON})
                    described += 1
                    described_taken += 1
                    waiting += 1
                    continue
                if reason:
                    await w.update(CLAIMS, r["id"], {"status": CLAIM_DROPPED, "reason": reason})
                else:
                    await w.update(CLAIMS, r["id"], {"reason": ""})
                    waiting += 1
            # (only claims still waiting on a real name count toward `WAITING_NAME_CLAIMS`: held
            # claims about people with no name are never let go, #3749)
            kept_waiting = len(leftovers) - people_held - resolved
            if not_a_name:
                logger.info("not a name, by reason: %s", dict(not_a_name))  # counts only
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
                    if reason == UNKNOWN_NAME_REASON and _only_described_unknown(claim, names):
                        reason = DESCRIBED_REASON  # (its new name is made: the rest is described)
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
                if reason == DESCRIBED_REASON:
                    if described >= UNPLACED_LIMIT:
                        # Past this read's cap: held, never dropped (owner's rule, #3749). Stored
                        # as waiting for its name, which every list and count leaves out, so the
                        # next read takes it up first (the held-claims loop above), within its own
                        # cap. Not under `WAITING_NAME_CLAIMS`: that limit lets go of claims.
                        described_held += 1
                        asked.add(key)
                        rows.append(
                            self._row(
                                claim, cited, prompt_version, model, session_started_at, w.now,
                                status=CLAIM_PROPOSED, reason=WAITING_NAME_REASON,
                            )
                        )  # fmt: skip
                        continue
                    described += 1
                    waiting += 1
                    asked.add(key)
                    rows.append(
                        self._row(
                            claim, cited, prompt_version, model, session_started_at, w.now,
                            status=CLAIM_PROPOSED, reason=reason,
                        )
                    )  # fmt: skip
                    continue
                if reason == UNPLACED_REASON:
                    # kept for the DM's answer (#3238), up to the session's cap; past it the claim
                    # is counted and not stored, so a runaway read cannot fill the DM's list
                    if kept_unplaced >= UNPLACED_LIMIT:
                        unplaced_over_cap += 1
                        continue
                    kept_unplaced += 1
                    waiting += 1
                    asked.add(key)
                    rows.append(
                        self._row(
                            claim, cited, prompt_version, model, session_started_at, w.now,
                            status=CLAIM_PROPOSED, reason=reason,
                        )
                    )  # fmt: skip
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
            pile = people_held - described_taken + described_held
            if pile or described_taken:
                # counts only, whenever the pile of held claims about people with no name is not
                # empty, so one that never shrinks shows in the logs (#3749)
                logger.info(
                    "people with no name held: %d new this read, %d taken up, %d held now",
                    described_held, described_taken, pile,
                )  # fmt: skip
        return Proposed(
            waiting,
            dropped,
            repeated,
            malformed,
            dict(why),
            len(new),
            tuple(new),
            joined,
            deferred,
            len(for_players),
            kept_unplaced,
            unplaced_over_cap,
            described,
            described_held,
            described_dropped,
            rules_dropped,
            tidied,
        )

    @staticmethod
    def _detail(predicate: str, claim: Claim, subject_kind: str = "") -> str:
        """What a confirmed fact keeps as its detail: for the place tie, the one wording of the
        word the table used ("lives in"), so two ways of saying it are one word (#3363)."""
        if predicate == "located_in":
            # (the word may be said as the relationship or, for "is in", as the detail)
            return (
                place_words.canonical(claim.relationship, subject_kind)
                or place_words.canonical(claim.details, subject_kind)
                or claim.details
            )
        return claim.details

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
                        "needs_look": False,
                        # an item reaches here only when `_generic_items` judged it one particular
                        # thing (#3618), so a second owner is flagged like a named item's
                        "unique_item": True if onto.is_a(kind, "item") else None,
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

    async def which_one(self, guild_id: int, campaign_id: str, claim_id: str) -> list[WhichOne]:
        """The "Which one?" choices of a claim kept because a name in it could be two entries
        (#3238): one `WhichOne` for each such name the DM has not picked for yet. Empty when the
        claim is gone, is not one of those, or nothing is left to pick."""
        async with self._store._read(guild_id, campaign_id) as scope:
            row = await scope.get(CLAIMS, claim_id, lock=False)
            if row is None or row["status"] != CLAIM_PROPOSED or row["reason"] != UNPLACED_REASON:
                return []
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
        return which_choices(row, entities, aliases)

    async def pick_one(
        self, guild_id: int, campaign_id: str, claim_id: str, side: str, entry_id: str
    ) -> Claim:
        """The DM's "Which one?" (#3238): the name on this `side` of a kept claim is the entry
        `entry_id` (one of `which_one`'s). The pick is recorded by the entry's id (two entries can
        share the very same name), and the ordinary Keep then uses it ahead of the words. Once no
        name in the claim is left to pick for, it is an ordinary waiting claim again. Refused, with
        nothing changed, if the claim or the choice is not there any more."""
        if side not in ("subject", "object"):
            raise MemoryRuleError(GONE)
        async with self._store._write(guild_id, campaign_id, DM) as w:
            row = await w.get(CLAIMS, claim_id)
            if row is None or row["status"] != CLAIM_PROPOSED or row["reason"] != UNPLACED_REASON:
                raise MemoryRuleError(GONE)
            entities = await w.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await w.select(ALIASES, " AND status <> 'rejected'")
            names = names_of(entities, aliases)
            options = {i for i, _ in names.by_key.get(name_key(str(row[side])), ())}
            if entry_id not in options:
                raise MemoryRuleError(GONE)
            other = "object" if side == "subject" else "subject"
            left = (
                row[f"{other}_picked"] is None
                and len(names.by_key.get(name_key(str(row[other])), ())) > 1
            )
            changes: dict[str, Any] = {f"{side}_picked": entry_id}
            changes["reason"] = UNPLACED_REASON if left else ""
            return claim_of(await w.update(CLAIMS, claim_id, changes))

    async def unpick(self, guild_id: int, campaign_id: str, claim_id: str) -> bool:
        """The DM's Undo of "Which one?" (#3463): the pick (or picks) made on this kept claim are
        taken back and it waits for its answer again, with the same choices. Claims are not in the
        change log, so this is the claim's own undo, and it holds only while the claim is still
        proposed (not kept, turned down or dropped since) and has a pick to take back; otherwise
        nothing changes and it returns False. A claim of another campaign is never found. Both
        sides' picks go together: the claim returns to the state it was kept in."""
        async with self._store._write(guild_id, campaign_id, DM) as w:
            row = await w.get(CLAIMS, claim_id)
            if (
                row is None
                or row["status"] != CLAIM_PROPOSED
                or row["reason"] not in (UNPLACED_REASON, "")
                or (row["subject_picked"] is None and row["object_picked"] is None)
            ):
                return False
            entities = await w.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await w.select(ALIASES, " AND status <> 'rejected'")
            names = names_of(entities, aliases)
            ambiguous = any(
                len(names.by_key.get(name_key(str(row[side])), ())) > 1
                for side in ("subject", "object")
            )
            await w.update(
                CLAIMS,
                claim_id,
                {
                    "subject_picked": None,
                    "object_picked": None,
                    "reason": UNPLACED_REASON if ambiguous else "",
                },
            )
            return True

    async def unplaced_count(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int] | None = None
    ) -> int:
        """How many claims about the player characters are kept for the DM's answer (#3238), for the
        count the import or recap result, the names-to-check list and "Questions for you" show.
        A count only. With `opted_in` it counts exactly the rows `unplaced` lists (a reading is
        not one, and only claims whose every speaker is recorded now), so the Family ties count
        and card agree (#3463); without it, every kept claim."""
        if opted_in is not None:
            return len(await self.unplaced(guild_id, campaign_id, opted_in=opted_in, limit=None))
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_claims WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'proposed' AND reason = %s",
                (*scope.ids, UNPLACED_REASON),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def unplaced(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        opted_in: Collection[int],
        limit: int | None = 50,
    ) -> list[Claim]:
        """The claims kept for the DM's answer (#3238) that `unplaced_count` counts, oldest first,
        for the Family ties card (#3463): the very rows the count counts, read only (`waiting` wipes
        claims of speakers no longer recorded and drops claims whose names are not all confirmed
        yet, so its list can be shorter than the count). Only those whose every speaker is
        recorded now (`opted_in`)."""
        async with self._store._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                CLAIMS,
                " AND status = 'proposed' AND reason = %s AND reading IS NULL"
                " ORDER BY created_at, id",
                (UNPLACED_REASON,),
            )
        mine = set(opted_in)
        found = [claim_of(r) for r in rows]
        ready = [c for c in found if set(c.speaker_ids) <= mine]
        return ready if limit is None else ready[:limit]

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

    @staticmethod
    def _split(
        rows: Sequence[Mapping[str, Any]],
        entities: Sequence[Mapping[str, Any]],
        aliases: Sequence[Mapping[str, Any]],
        rejected: Collection[str],
    ) -> tuple[list[Claim], list[Claim], list[tuple[Claim, str]]]:
        """(ready, waiting on a new name, gone): the proposed claims split by their names (#1364),
        pure, so the list and its count cannot differ."""
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
            if claim.reason == DESCRIBED_REASON:
                # a described end is made by Keep, not named first (#3736): the claim is ready
                # once every other end is confirmed (a new name beside it waits for its answer)
                said = [
                    n
                    for n in said
                    if live.by_key.get(name_key(n)) or not descriptions.is_described(n)
                ]
            found = [live.by_key.get(name_key(n), ()) for n in said]
            unnamed = [n for n, f in zip(said, found, strict=True) if not f]
            if any(name_key(n) in rejected for n in unnamed):
                gone.append((claim, NAME_REJECTED_REASON))  # the DM said it isn't a name
            elif claim.reason == WAITING_NAME_REASON:
                # held for a later read (its name not proposed yet, or past a read's cap, #3749):
                # in no list until a read asks it, as `waiting_for_check` and the web lists do
                pending.append(claim)
            elif unnamed:
                ready.append(claim)  # a name that went away: asking says why it can't be written
            elif all(any(e in sure for e, _ in f) for f in found):
                ready.append(claim)
            else:
                pending.append(claim)
        return ready, pending, gone

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
        ready, pending, gone = self._split(rows, entities, aliases, rejected)
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

    async def readings_waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> int:
        """How many readings (what people believe, or a suspected lie) wait for the DM, counted
        without changing anything (#3435). The same rows the website's readings list counts
        (`dmbot_web_readings`), and only those whose every speaker is recorded now."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_claims WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'proposed' AND reading IS NOT NULL AND how <> 'plan'"
                " AND cardinality(speaker_ids) > 0 AND speaker_ids <@ %s::bigint[]"
                " AND reason <> %s",
                (*scope.ids, list(opted_in), WAITING_NAME_REASON),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def waiting_for_check(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        opted_in: Collection[int],
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

    async def listed_count(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> int:
        """How many claims the DM's list shows now (#3435): `waiting` without its limit, counted
        WITHOUT changing anything. The same split as the list (`_split`): a claim is listed when
        every name in it is a confirmed entry; one naming a still-proposed entry, or waiting for
        its name, is not. Claims citing someone not recorded now are left out, as the list wipes
        them first. `waiting_for_check` is higher by the claims that are not listed yet."""
        async with self._store._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                CLAIMS, " AND status = 'proposed' AND reading IS NULL ORDER BY created_at, id"
            )
            rows = [
                r
                for r in rows
                if r["speaker_ids"] and all(int(x) in set(opted_in) for x in r["speaker_ids"])
            ]
            if not rows:
                return 0
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            aliases = await scope.select(ALIASES, " AND status <> 'rejected'")
            rejected = await _turned_down(scope)
        ready, _pending, _gone = self._split(rows, entities, aliases, rejected)
        return len(ready)

    async def forget_speaker(self, guild_id: int, user_id: int) -> int:
        """Stop recording me: in every campaign of the server, delete the claims that cite
        this person's words and were not turned into a fact (waiting, turned down or dropped).
        What the DM confirmed is the DM's fact and stays. A reading the DM placed is held to the
        same line (`forget_readings`): what the DM kept stays, a guess that only this person's
        words produced goes with what it made. Returns how many claims, readings, deeds, reveals
        and scenes went."""

        # (imported here: they need this module's names)
        from dmbot.memory import deed_proposals, reveal_proposals

        async with self._store._db.guild(guild_id) as conn:
            readings = await forget_readings.forget(conn, guild_id, user_id)
            readings += await deed_proposals.forget(conn, guild_id, user_id)
            readings += await reveal_proposals.forget(conn, guild_id, user_id)  # (#2996)
            readings += await scenes.forget(conn, guild_id, user_id)
            # (DMbot's graph check reports that quote them, #3741)
            readings += await graph_review.forget(conn, guild_id, user_id)
            answers = await who_answers.forget(conn, guild_id, user_id)
            cur = await conn.execute(
                "DELETE FROM memory_claims WHERE guild_id = %s AND %s = ANY(speaker_ids)"
                " AND status <> 'confirmed' RETURNING id",
                (guild_id, user_id),
            )
            gone = [str(r["id"]) for r in await cur.fetchall()]
            await evidence.placeholder_claims(conn, guild_id, gone)  # their links keep no target
            # and what the DM confirmed stays, without this person's line references (#2676)
            await evidence.scrub_claim_lines(conn, guild_id, user_id=user_id)
            return len(gone) + readings + answers  # (claims, readings and Who-is-this answers)

    async def reject(self, guild_id: int, campaign_id: str, claim_id: str) -> Claim:
        """The DM says no: it is not asked again, and nothing is written to memory."""
        async with self._store._write(guild_id, campaign_id, DM) as w:
            row = await w.get(CLAIMS, claim_id)
            held = row is not None and row["reason"] == WAITING_NAME_REASON
            if row is None or row["status"] != CLAIM_PROPOSED or held:
                raise MemoryRuleError(GONE)  # (a held claim is in no list: never answered, #3749)
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

    @staticmethod
    async def _same_session_person(
        w: Changes, claim: Claim, described: str, kind: str = "character"
    ) -> str | None:
        """The unnamed person an earlier Keep of this same session already made for the same
        words (#3431): within one session "the woman" is one woman. Across sessions it is a new
        person, and the same-person question offers the join; never campaign-wide, since two
        women a month apart are often two women."""
        if claim.session_started_at is None:
            return None
        same = unnamed_mod.same_person_key(described, None)
        kept = await w.select(
            CLAIMS,
            " AND status = 'confirmed' AND reason = %s AND session_started_at = %s"
            " AND fact_id IS NOT NULL",
            [DESCRIBED_REASON, claim.session_started_at],
        )
        facts = [str(r["fact_id"]) for r in kept]
        if not facts:
            return None
        # a kept fact is a connection, a condition (its state row) or the entry itself (a condition
        # that changed nothing): the person is an end of the first, the state's entry, or the third
        ends: set[str] = set(facts)
        for row in await w.select(RELATIONS, " AND id = ANY(%s)", [facts]):
            ends |= {str(row["subject_id"]), str(row["object_id"])}
        for row in await w.select(STATES, " AND id = ANY(%s)", [facts]):
            ends.add(str(row["entity_id"]))
        for row in await w.select(
            ENTITIES,
            " AND unnamed AND status = 'confirmed' AND unnamed_anchor IS NULL AND type = %s"
            " AND id = ANY(%s)"
            " ORDER BY created_at, id",
            [kind, sorted(ends)],
        ):
            if unnamed_mod.same_person_key(str(row["unnamed_what"]), row["unnamed_role"]) == same:
                return str(row["id"])
        return None

    @staticmethod
    def _described_ends(claim: Claim, names: Names, *, condition: bool) -> list[str]:
        """The ends of a kept "person with no name" claim that still match no entry (#3431): one
        beside a known end, two for a chain of unnamed people, or the subject of a fact about one
        alone (#3736). None left when the DM named them since: the fact is then written the
        ordinary way. A claim with an unknown end that is not a described person is refused."""
        said = (claim.subject,) if condition else (claim.subject, claim.object)
        ends: list[str] = []
        for n in said:  # (one end per person: "The woman" and "the woman" are one)
            if not names.by_key.get(name_key(n)) and name_key(n) not in map(name_key, ends):
                ends.append(n)
        if any(not descriptions.is_described(n) for n in ends):
            raise MemoryRuleError(GONE)
        return ends

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
        # A held claim (waiting for its name, or past a read's cap, #3749) is in no list the DM
        # sees: an id that reaches here anyway is gone, never written.
        if claim is None or claim.status != CLAIM_PROPOSED or claim.reason == WAITING_NAME_REASON:
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
        described: list[str] = []
        if claim.reason == DESCRIBED_REASON:
            # The person is made inside the same write as the fact (below), so a fact the rules
            # refuse leaves nothing behind; here each end only stands for them (#3431), with a
            # stand-in of its own, so the two ends of a chain are two people (#3736).
            described = self._described_ends(
                claim, names, condition=condition_of(wording) is not None
            )
            if described:
                found = dict(names.by_key)
                for i, end in enumerate(described):
                    found[name_key(end)] = ((f"{NEW_PERSON}:{i}", _unnamed_kind(end)),)
                names = replace(names, by_key=found)
        waiting_ids = {str(e["id"]) for e in entities if e["status"] == PROPOSED}
        # Asked again against what is known now: a name may have been merged or renamed since.
        who = _entry_of(names, claim.subject, claim.subject_picked)
        if isinstance(who, str):
            raise MemoryRuleError(f"Can't write that now: {who}.")
        if who[0] in waiting_ids:
            raise MemoryRuleError(NAME_FIRST)
        resolved: tuple[PredicateTerm, bool, str] | None = None
        other: tuple[str, str] | str = ""
        condition = condition_of(wording)
        if condition is None:
            other = _entry_of(names, claim.object, claim.object_picked)
            if isinstance(other, str):
                raise MemoryRuleError(f"Can't write that now: {other}.")
            if other[0] in waiting_ids:
                raise MemoryRuleError(NAME_FIRST)
            resolved = place_relationship(
                onto, claim.relationship, who[1], other[1], details=claim.details or ""
            )
        # One transaction (#1301): the question is taken, the fact written and the claim told
        # which fact it became, or none of it happens. The campaign's write lock is held, so a
        # second press waits and then finds the question gone. A fact the rules refuse rolls
        # the whole thing back and leaves the question waiting. The check at the top of
        # `confirm` is only an early exit: this one, under the lock, is the one that counts.
        async with self._store._write(guild_id, campaign_id, DM) as w:
            current = await w.get(CLAIMS, claim_id)
            if (
                current is None
                or current["status"] != CLAIM_PROPOSED
                or current["reason"] == WAITING_NAME_REASON
            ):
                raise MemoryRuleError(GONE)
            made: dict[str, tuple[str, str]] = {}  # stand-in -> the person, made or found
            for i, end in enumerate(described):
                kind = _unnamed_kind(end)  # (a person, or a particular group, #3736)
                person_id = await self._same_session_person(w, claim, end, kind)
                if person_id is None:
                    added = await self._store._add_unnamed_in(
                        w, end, None, "", None, seen_at=claim.session_started_at, kind=kind
                    )
                    person_id = added.entity.id
                person = await w.update(ENTITIES, person_id, {"status": CONFIRMED})
                made[f"{NEW_PERSON}:{i}"] = (str(person["id"]), kind)
            who = made.get(who[0], who)
            if other and not isinstance(other, str):
                other = made.get(other[0], other)
            if before is not None:
                await before(w)
            shown = False
            if condition is not None:
                state, flags = await self._store.set_condition_in(
                    w, who[0], condition, claim.session_started_at
                )
                fact_id = who[0] if state is None else state.id
            else:
                assert resolved is not None and not isinstance(other, str)
                term, from_object, label = resolved
                subject_id, object_id = (other[0], who[0]) if from_object else (who[0], other[0])
                # A claim the reader could only file under the catch-all, between two entries the
                # story already holds a connection back for, may be that connection in other
                # words: it is written held back too, never shown beside it (#3975). The DM
                # decides with 👁️ Reveal; DMbot never guesses which one it means.
                hold = term.key == FALLBACK_PREDICATE and await held_back_between(
                    w, subject_id, object_id
                )
                # On the catch-all, the words it was said in are the fact's label and nothing
                # else: the card sets `detail` between the two names, so side notes can't go there.
                subject_kind = other[1] if from_object else who[1]
                relation, flags = await self._store.add_relation_in(
                    w, subject_id, term.key, object_id, confidence=1.0, status=CONFIRMED,
                    detail=label or self._detail(term.key, claim, subject_kind),
                    from_session_at=claim.session_started_at,
                    secret=secret,
                    unrevealed=hold,
                    # whether they've met, from the reader's own words for it (#3881)
                    knows_how=knows_how.how_from(
                        claim.relationship, claim.details, a=claim.subject, b=claim.object
                    )
                    if term.key == KNOWS_PREDICATE
                    else "",
                )  # fmt: skip
                fact_id = relation.id
                # (the table said it: a connection the story held back is the party's now)
                shown = not secret and not hold and await reveal_stated_in(w, relation.id)
            row = await w.update(
                CLAIMS,
                claim_id,
                {"status": CLAIM_CONFIRMED, "decided_at": w.now, "fact_id": fact_id},
            )
            batch = w.batch
        return Confirmed(claim_of(row), tuple(flags), batch, shown)
