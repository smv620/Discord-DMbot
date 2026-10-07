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

ALIAS_KINDS = ("full", "short", "nickname", "title", "misheard")
MENTION_METHODS = ("exact", "sound", "spelling", "context", "dm")
FIX, KEEP = "fix", "keep"  # correction actions

# Where a fact came from. "dm" is the DM's own word; everything else needs confirming.
SOURCES = ("dm", "character", "scan", "cleaner", "undo", "entitybot", "backup")

NAME_MAX = 100
DESCRIPTION_MAX = 300
DETAIL_MAX = 100
LINE_REF_MAX = 100
LIST_MAX = 50  # examples, mention IDs: the same cap on write and in backups
DM = "dm"

_ID = re.compile(r"^[0-9a-f]{32}$")


class MemoryRuleError(ValueError):
    """A write the memory rules refuse. The message is plain enough to show a DM."""


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
    letters = re.sub(r"['’`\-]", "", letters)
    return " ".join(re.sub(r"[^\w\s]", " ", letters).split())


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
class Written[T]:
    """A write's result, with the change-log batch to pass to `undo` (None: nothing
    changed, for example the fact was already known)."""

    value: T
    batch: int | None
