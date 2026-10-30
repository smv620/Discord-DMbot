"""Campaign memory data: what EntityBot stores about a campaign (docs/PLAN.md, #126).

Internal words only (entity, alias, relation). Anything shown to people uses plain
words instead ("DMbot remembers Belleros is Cerric's mentor").
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

# Entity status: new names start as proposed and are used for matching only. The NPC
# tracker and PlotBot read confirmed data only.
PROPOSED, CONFIRMED, REJECTED, MERGED = "proposed", "confirmed", "rejected", "merged"
ENTITY_STATUSES = (PROPOSED, CONFIRMED, REJECTED, MERGED)
FACT_STATUSES = (PROPOSED, CONFIRMED, REJECTED)  # aliases and relations
LIVE = (PROPOSED, CONFIRMED)

# "old": the spelling a name had before the DM fixed it (#2170): matched, never listened for.
ALIAS_KINDS = ("full", "short", "nickname", "title", "misheard", "old")
MENTION_METHODS = ("exact", "sound", "spelling", "context", "dm")
FIX, KEEP = "fix", "keep"  # correction actions

# Where a fact came from. "dm" is the DM's own word; everything else needs confirming.
BACKGROUND_SOURCE = "background"  # a suggestion read from the DM's own story (#2167)
PARTY_SOURCE = "party"  # the faction "The party", made by the rule that links the players (#3036)
SOURCES = (
    *("dm", "character", "scan", "cleaner", "undo", "entitybot", "backup", "extractor"),
    BACKGROUND_SOURCE,
    PARTY_SOURCE,
)

# What the story extractor's claims are (docs/STORY_MEMORY.md, section 1; #1297): proposals
# only, in their own table. A claim is dropped (with a reason) when the rules or consent say no,
# rejected when the DM says no, and confirmed when the DM says yes and the fact is written.
CLAIM_PROPOSED, CLAIM_CONFIRMED, CLAIM_REJECTED, CLAIM_DROPPED = (
    "proposed", "confirmed", "rejected", "dropped",
)  # fmt: skip
CLAIM_STATUSES = (CLAIM_PROPOSED, CLAIM_CONFIRMED, CLAIM_REJECTED, CLAIM_DROPPED)
# How a claim was said: what the DM told the table, what a player said or believes, or what
# someone means to do (never a fact).
# A suggestion read from the DM's own story starts its note with this and keeps a short quote
# until the DM decides; confirming, rejecting or merging it clears the note (#2167).
BACKGROUND_NOTE = "From your background"
HOW_DM, HOW_PLAYER, HOW_PLAN = "dm_said", "player_said", "plan"
HOWS = (HOW_DM, HOW_PLAYER, HOW_PLAN)

# What a character is in the story, one at a time (#1034, owner decision 2026-10-10). A role
# is never a kind of entry: the kind says what a thing fundamentally is ("character"), and
# whether it is a player's character or the DM's NPC is said here. Every character is one or the
# other (owner, 2026-10-23, #2562): a god or a named creature is an NPC, "god" going in its words.
PLAYER_CHARACTER, NPC = "player_character", "npc"
ROLES = (PLAYER_CHARACTER, NPC)
ROLE_LABELS = {PLAYER_CHARACTER: "PC", NPC: "NPC"}

# What a character links to in the rules (by source and name), never kinds of their own:
# "race" is shown only as the 2014 name, `[Legacy 2014]`. One species and one creature type
# for each character; any number of classes.
SPECIES, CREATURE_TYPE, STAT_BLOCK, CLASS, BACKGROUND = (
    "species", "creature_type", "stat_block", "class", "background",
)  # fmt: skip
LINK_KINDS = (SPECIES, CREATURE_TYPE, STAT_BLOCK, CLASS, BACKGROUND)
LINK_LABELS = {
    SPECIES: "species",
    CREATURE_TYPE: "type",
    STAT_BLOCK: "stat block",
    CLASS: "class",
    BACKGROUND: "background",
}
LINK_SOURCE_MAX = 40
LINK_NAME_MAX = 100
LINK_EDITIONS = ("2014", "2024")

NAME_MAX = 100
DESCRIPTION_MAX = 300
DETAIL_MAX = 100
LINE_REF_MAX = 100
LIST_MAX = 50  # examples, mention IDs: the same cap on write and in backups
DM = "dm"

# What state facts say (docs/STORY_MEMORY.md, "State facts"): beings have a condition among
# the first group, items and places among the second. The rules for changing one live in
# `ontology.STATE_CHANGES`.
ALIVE, DEAD, MISSING, CAPTURED, TRANSFORMED, UNKNOWN = (
    "alive", "dead", "missing", "captured", "transformed", "unknown",
)  # fmt: skip
INTACT, DESTROYED, LOST = "intact", "destroyed", "lost"
# A story thread's state (#1662): where it is in the story, in the DM's words. Kept in the same
# table as a condition (one open at a time, a new one closes the old). Not in CONDITIONS: a
# claim's words are never read as one of these, since "the door is open" is not a thread.
HINTED, OPEN, GOING, RESOLVED, ABANDONED = "hinted", "open", "active", "resolved", "abandoned"
THREAD_STATES = (HINTED, OPEN, GOING, RESOLVED, ABANDONED)
# The core kinds of story thread. Threads are the DM's plot notes (CLAUDE.md: DM-screen content),
# so lookup and recall keep them from anyone who may not see secrets.
THREAD_TYPES = ("thread", "quest", "mystery", "hook", "goal", "promise")
# A deed the DM recorded (#1732): something a player character or the party did that mattered to
# someone. A kind under `event`. Like a thread it is the DM's own note, so lookup and recall keep it
# from anyone who may not see secrets.
DEED = "deed"
DID, AFFECTED = "did", "affected"  # who did it (doer → deed); who it mattered to (deed → them)
DM_ONLY_TYPES = (*THREAD_TYPES, DEED)
DEED_TEXT_MAX = 200  # what the DM types for a deed
DEED_NAME_MAX = 100  # an entry's name is at most this; the full words are its description
DEED_VIEWERS_MAX = 5  # whom one deed can matter to
BEING_CONDITIONS = (ALIVE, DEAD, MISSING, CAPTURED, TRANSFORMED, UNKNOWN)
THING_CONDITIONS = (INTACT, DESTROYED, LOST, UNKNOWN)
CONDITIONS = (*BEING_CONDITIONS, INTACT, DESTROYED, LOST)
# Only these stop something from acting; the rest are soft (a missing NPC turning up is news).
ENDED = (DEAD, DESTROYED)

_ID = re.compile(r"^[0-9a-f]{32}$")


class MemoryRuleError(ValueError):
    """A write the memory rules refuse. The message is plain enough to show a DM."""


class TooLateToUndo(MemoryRuleError):
    """Undo of a change the change log no longer holds: older than the days Undo works
    for (#164), so it was pruned after a session."""


def days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


@dataclass(frozen=True, slots=True)
class Heard:
    """How many lines of one speaker named an entry in a session, kept at its end
    (memory_heard) for ranking hints."""

    entity_id: str
    speaker_id: int
    times: int


@dataclass(frozen=True, slots=True)
class HeardCount:
    entity_id: str
    times: int
    last_session_at: int | None  # when the last session it was said in started


def new_id() -> str:
    return uuid.uuid4().hex


def is_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def clean_text(text: str, limit: int = NAME_MAX) -> str:
    """Collapse spaces and check length. Raises MemoryRuleError when empty or too long."""
    cleaned = " ".join(unicodedata.normalize("NFC", text).split())
    if not cleaned:
        raise MemoryRuleError("A name can't be empty.")
    if len(cleaned) > limit:
        raise MemoryRuleError(f"That's too long (at most {limit} characters).")
    return cleaned


def name_key(text: str) -> str:
    """How names are compared: case, accents, apostrophes and hyphens ignored.

    "Ka'zeth", "kazeth" and "Ka-Zeth" are the same key; "Ka Zeth" is not (a split name
    is a different thing heard, which the Transcript Cleaner handles).
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in decomposed if not unicodedata.combining(c))
    letters = re.sub(r"['’ʼ`\-]", "", letters)
    return " ".join(re.sub(r"[^\w\s]", " ", letters).split())


_POSSESSIVE = re.compile(r"['’]s\b")


def spelling_key(text: str) -> str:
    """A name or a stretch of speech as the secret-said-aloud question compares it (#1407):
    "Auril's frost" is Auril too, so a possessive goes before the usual `name_key` (on both sides,
    or "Dragon's Rest" never meets itself). Entries with one key are one name to the question."""
    return name_key(_POSSESSIVE.sub("", text))


# A spelling box holding "Name pronounced …" is the sound, not the spelling (#2332). It needs
# words after the cue, so a name like "Pronouncer Vex" or a bare "Sounds Like" still saves. Shared
# by Discord's name card and the website's All names page, so both refuse it.
PRONOUNCED = re.compile(r"\b(?:pronounced|sounds? like)\s+\S", re.IGNORECASE)


def lookup_key(text: str) -> str:
    """`name_key`, falling back to plain lower case for names that are all punctuation,
    so every name has a key. Raises MemoryRuleError when the key is too long."""
    key = name_key(text) or " ".join(text.casefold().split())
    if not 0 < len(key) <= NAME_MAX:
        raise MemoryRuleError(f"That's too long (at most {NAME_MAX} characters).")
    return key


def check_status_change(old: str | None, new: str, source: str) -> None:
    """Only the DM confirms, un-rejects, or changes something confirmed. Anything else
    may only propose, or drop a proposal ("Only DM-confirmed facts count")."""
    if source == DM or old == new:
        return
    if new == CONFIRMED or old in (CONFIRMED, REJECTED):
        raise MemoryRuleError("Only the DM can confirm or change that.")


@dataclass(frozen=True, slots=True)
class NewName:
    """One name from a list the DM gave (📥 Add many): saved with its other names and
    secret names, all in one change."""

    name: str
    type: str
    status: str
    others: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()
    role: str | None = None  # for a character: player character, NPC or god
    note: str = ""  # shown with a suggestion until the DM decides (a quote, how it was found)


@dataclass(frozen=True, slots=True)
class RuleLink:
    """A character's link to a rules entry: its species, creature type, stat block, class
    or background, by source and name. A name the rules data doesn't have keeps its plain
    words and is marked not known ("not in DMbot's rules")."""

    id: str
    entity_id: str
    kind: str  # LINK_KINDS
    rules_source: str  # "srd52", "srd51", "homebrew", ...; "" when not known
    name: str
    edition: str | None  # "2014" is shown as [Legacy 2014]
    known: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class MoreNames:
    """New other names and secret names for a name DMbot already knows, from a list the
    DM gave (📥 Add many, #369): saved in the same change as the list's new names."""

    entity_id: str
    others: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    type: str
    name: str
    description: str
    status: str
    merged_into: str | None
    source: str
    created_at: int
    played_by: int | None = None  # the Discord user playing this player character
    role: str | None = None  # player character, NPC or god: for a character only (ROLES)
    needs_look: bool = False  # moved here from an older kind: the DM keeps, links or deletes it
    # An item of which only one exists (Heartseeker, not a longbow). None: nobody said, so a
    # proper name means yes (`looks_named`); the DM's switch on the name card sets it.
    unique_item: bool | None = None
    # A clue only some people know (#1649): the DM's switch. Who knows it is its confirmed
    # `knows` facts. Meaningful for a clue only; off for everything else.
    audience_limited: bool = False
    # The campaign's party (#1655): one group per campaign whose members are its confirmed player
    # characters, worked out in code and not stored fact by fact.
    party: bool = False
    # The DM's "Players don't know this one yet" (#2321): the entry is the DM's alone for now, like
    # a clue only some people know, but with no audience to track. Any kind of entry.
    hidden_from_players: bool = False

    # Written before pronouns were kept out of the suggestions (#2358): such a suggestion that is a
    # pronoun ("We", "I'll") is not offered again. Set by the database for the rows that existed.
    made_before_name_gate: bool = False
    # For an event, clue or item entry: the evidence it faked (the planted ledger), by id (#2409).
    manipulated_by: str | None = None
    # A person the story describes but never names (#2790): "Ken's sister", "a hooded figure". It
    # has no alias, so no name match finds it; `unnamed_what` is what the story calls them,
    # `unnamed_anchor` the entry they hang off (none for a hooded figure) and `unnamed_role` the
    # relationship. The label is built from these when shown (`unnamed.label`), never stored.
    unnamed: bool = False
    unnamed_anchor: str | None = None
    unnamed_role: str | None = None
    unnamed_what: str | None = None

    @property
    def unseen_by_players(self) -> bool:
        """The DM alone sees this entry: a clue only some know, one the players don't know yet, or
        a person the story never named (#2790). What lists, cards, answers and recaps hide from
        everyone but a DM."""
        # (an unnamed person is also kept out of the names a spelling fix may complete: safe, and
        # at worst it fixes one line fewer)
        return self.audience_limited or self.hidden_from_players or self.unnamed

    @property
    def is_unique(self) -> bool:
        """Only one of this item exists (the DM's word, else whether its name is a proper
        name). Meaningless for anything but an item."""
        return looks_named(self.name) if self.unique_item is None else self.unique_item

    @property
    def is_player_character(self) -> bool:
        return self.role == PLAYER_CHARACTER

    @property
    def kind_key(self) -> str:
        """The key the lists and the kind menus use: `npc` or `player_character` for a character
        with that role, else the kind itself."""
        if self.type == "character":
            return {PLAYER_CHARACTER: "player_character", NPC: "npc"}.get(
                self.role or "", "character"
            )
        return self.type


@dataclass(frozen=True, slots=True)
class Opinion:
    """An impression (a viewer's feeling about a subject) or an attitude (a group's or place's
    feeling toward a whole people) the DM set (#1655). A newer one for the same pair closes the
    older, so the history stays: `closed_at` is None while it is the current one."""

    id: str
    holder_id: str  # the viewer (an impression) or the group or place (an attitude)
    target_id: str  # the subject (an impression) or the people (an attitude)
    score: int
    weight: int
    source: str
    from_session_at: int | None
    created_at: int
    closed_at: int | None


_ARTICLES = frozenset({"a", "an", "the"})


def looks_named(name: str) -> bool:
    """Is this a proper name (Heartseeker, The Sun Blade) rather than a kind of thing (a
    longbow)? A guess to start from: any capitalised word counts, once a leading article is
    set aside. The DM can switch it on the item's name card, so it never has to be right."""
    words = name.split()
    if words and words[0].casefold() in _ARTICLES and len(words) > 1:
        words = words[1:]
    return any(w[:1].isupper() for w in words)


@dataclass(frozen=True, slots=True)
class State:
    """One thing's condition for a time: alive, dead, intact, destroyed… The one with no
    `closed_at` is the current one; a new condition closes it and the history is kept, with
    when it changed (`from_session_at`, else `created_at`) and who said so (`source`)."""

    id: str
    entity_id: str
    condition: str
    from_session_at: int | None
    source: str
    created_at: int
    closed_at: int | None = None

    @property
    def since(self) -> int:
        return self.created_at if self.from_session_at is None else self.from_session_at


@dataclass(frozen=True, slots=True)
class Alias:
    id: str
    entity_id: str
    text: str
    key: str
    kind: str
    used_by: str | None
    secret: bool  # DM-only: a secret identity ("the hooded stranger" is Belleros)
    status: str
    sound_codes: tuple[str, ...]
    source: str
    created_at: int
    said_as: str | None = None  # how the recap's narrator says it (#1419); never on a secret name


@dataclass(frozen=True, slots=True)
class Relation:
    id: str
    subject_id: str
    predicate: str
    object_id: str
    detail: str
    confidence: float
    status: str
    source: str
    mention_ids: tuple[str, ...]
    from_session_at: int | None
    to_session_at: int | None
    from_game_time: int | None
    to_game_time: int | None
    secret: bool
    created_at: int
    # The version of its relationship's definition it was written under (#1189).
    definition_version: int = 1
    # Kept from the players until the table has said it (#2404); a backstory connection starts so.
    unrevealed: bool = False
    # A family connection whose detail is a role that reads one way, written by a DM (#2457).
    directional: bool = False
    # How a pronoun was settled (#2790), in fixed words: the DM's card only, never a player's.
    decided: str | None = None


@dataclass(frozen=True, slots=True)
class Correction:
    id: str
    heard: str
    heard_key: str
    entity_id: str | None
    action: str
    source: str
    created_at: int


@dataclass(frozen=True, slots=True)
class Flag:
    id: str
    kind: str
    relation_id: str
    other_id: str | None
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class SaidAsSuggestion:
    """A spelling to offer for how the recap narrator says a name (#2171): the name's entry, its
    name as the DM knows it, the other spelling it keeps being heard as, and how often."""

    entity_id: str
    name: str
    heard: str
    times: int


@dataclass(frozen=True, slots=True)
class PeopleSuggestion:
    """One question for the DM from "a name, then an answer" (#1188): `name`, a new name
    for the person `user_id` (kind "name", `row_id` the waiting name), or `name`, a
    character that person may play (kind "plays", `row_id` the waiting link). `times` is
    how often they answered to it; `shown` the person's name as the DM knows it."""

    kind: str
    user_id: int
    name: str
    row_id: str
    character_id: str | None = None
    times: int = 0
    shown: str = ""


@dataclass(frozen=True, slots=True)
class Written[T]:
    """A write's result, with the change-log batch to pass to `undo` (None: nothing
    changed, for example the fact was already known)."""

    value: T
    batch: int | None


# Names one merge takes: the bot, the website and the database door agree (#2426).
MERGE_MANY_MAX = 50

# ---- beliefs: who believes a connection, and how strongly (#2409; STORY_MEMORY section 9) ----

TRUTH, PARTY, PC_VIEW, NPC_VIEW = "truth", "party", "pc", "npc"
PERSPECTIVE_KINDS = (TRUTH, PARTY, PC_VIEW, NPC_VIEW)
THEORY, ACCUSATION, REVEALED_FACT = "theory", "accusation", "revealed_fact"
FALSE_EVIDENCE, SECRET_PLOT = "false_evidence", "secret_plot"
MAGICALLY_VERIFIED, EVASION_UNDER_MAGIC = "magically_verified", "evasion_under_magic"
BELIEF_STATUSES = (
    THEORY, ACCUSATION, REVEALED_FACT, FALSE_EVIDENCE, SECRET_PLOT, MAGICALLY_VERIFIED,
    EVASION_UNDER_MAGIC,
)  # fmt: skip
KNOWN = 1.0  # a belief at this confidence is something the perspective holds as fact
EVIDENCE_MAX = 20  # pointers a belief may cite
EVIDENCE_ID_MAX = 100


@dataclass(frozen=True, slots=True)
class Perspective:
    """Who holds beliefs: The truth, The party, or one character (a player character or an NPC)."""

    id: str
    kind: str
    entity_id: str | None
    created_at: int


@dataclass(frozen=True, slots=True)
class Belief:
    """One perspective's belief in one connection (the assertion), with the evidence it points at
    by id. History is kept; the row with the highest `seq` for a pair is the current one."""

    id: str
    perspective_id: str
    relation_id: str
    confidence: float
    status: str
    session_at: int | None
    evidence_ids: tuple[str, ...]
    seq: int
    recorded_at: int
    pinned: bool = False  # the DM's 📌 100% truth (#2447): only on The truth's belief

    @property
    def known(self) -> bool:
        return self.confidence >= KNOWN
