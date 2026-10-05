"""The campaign memory rules (the ontology): which kinds of things and relationships
exist, and what each relationship may connect (docs/PLAN.md, "Campaign memory rules").

The **core** is fixed in code and changes only in a reviewed release. Each campaign may
**extend** it (stored in memory_types / memory_predicates), never change it. Extensions
must reuse what exists when they can, are only ever added or deprecated, and come with a
description, examples and a reason.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory.models import DESCRIPTION_MAX, MemoryRuleError

ACTIVE, DEPRECATED = "active", "deprecated"
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
LABEL_MAX = 60
# Limited growth: new terms per campaign per session (rule 9).
MAX_NEW_TERMS_PER_SESSION = 5
# Keys this close in spelling to an existing one must reuse it.
SIMILAR_KEY_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class TypeTerm:
    key: str
    parent: str | None
    label: str
    description: str
    core: bool = True
    status: str = ACTIVE
    replaced_by: str | None = None


@dataclass(frozen=True, slots=True)
class PredicateTerm:
    key: str
    label: str  # reads between two names: "Belleros *is an ally of* Cerric"
    description: str
    subject_types: tuple[str, ...]
    object_types: tuple[str, ...]
    symmetric: bool = False
    max_per_subject: int | None = None  # at any one time
    conflicts_with: tuple[str, ...] = ()
    parent: str | None = None
    core: bool = True
    status: str = ACTIVE
    replaced_by: str | None = None


def _t(key: str, label: str, description: str, parent: str | None = None) -> TypeTerm:
    return TypeTerm(key, parent, label, description)


CORE_TYPES: tuple[TypeTerm, ...] = (
    _t("character", "character", "A person in the story."),
    _t("player_character", "player character", "A character a player plays.", "character"),
    _t("npc", "NPC", "A character the DM plays.", "character"),
    _t("creature", "creature", "An animal, monster or other being that isn't a character."),
    _t("place", "place", "A town, building, region, room or any other location."),
    _t("faction", "group", "A group, guild, army, cult, family or other organization."),
    _t("item", "item", "An object: a weapon, amulet, letter, ship."),
    _t("spell", "spell", "A spell or magical effect."),
    _t("deity", "god", "A god or other worshipped power."),
    _t("event", "event", "Something that happened or will happen: a battle, a wedding."),
    _t("concept", "idea", "Anything else worth remembering: a prophecy, a curse, a law."),
)

_BEINGS = ("character", "creature", "deity")
_ANY = tuple(t.key for t in CORE_TYPES if t.parent is None)

CORE_PREDICATES: tuple[PredicateTerm, ...] = (
    PredicateTerm(
        "located_in",
        "is in",
        "Where someone or something is.",
        ("character", "creature", "place", "faction", "item", "event"),
        ("place",),
        max_per_subject=1,
    ),
    PredicateTerm(
        "member_of",
        "is a member of",
        "Belongs to a group.",
        ("character", "creature"),
        ("faction",),
    ),
    PredicateTerm(
        "ally_of",
        "is an ally of",
        "On the same side.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
        symmetric=True,
        conflicts_with=("enemy_of",),
    ),
    PredicateTerm(
        "enemy_of",
        "is an enemy of",
        "On opposite sides.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
        symmetric=True,
        conflicts_with=("ally_of",),
    ),
    PredicateTerm(
        "kin_of",
        "is family of",
        "Related by family. The detail says how (sister, father).",
        _BEINGS,
        _BEINGS,
        symmetric=True,
    ),
    PredicateTerm(
        "owns",
        "owns",
        "Has or holds something.",
        (*_BEINGS, "faction"),
        ("item", "place", "creature"),
    ),
    PredicateTerm(
        "knows",
        "knows",
        "Knows someone or about something.",
        _BEINGS,
        _ANY,
    ),
    PredicateTerm(
        "serves",
        "serves",
        "Works for, obeys or worships.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
    ),
    PredicateTerm(
        "appears_in",
        "appears in",
        "Takes part in or shows up in an event.",
        tuple(k for k in _ANY if k != "event"),
        ("event",),
    ),
)

# Words an AI might reach for that mean a core relationship: reuse, don't create.
SYNONYMS: Mapping[str, str] = {
    "friend_of": "ally_of",
    "friends_with": "ally_of",
    "allied_with": "ally_of",
    "allies_with": "ally_of",
    "rival_of": "enemy_of",
    "enemies_with": "enemy_of",
    "hates": "enemy_of",
    "opposes": "enemy_of",
    "lives_in": "located_in",
    "based_in": "located_in",
    "is_in": "located_in",
    "found_in": "located_in",
    "belongs_to": "member_of",
    "part_of": "member_of",
    "in_group": "member_of",
    "related_to": "kin_of",
    "sibling_of": "kin_of",
    "parent_of": "kin_of",
    "child_of": "kin_of",
    "family_of": "kin_of",
    "has": "owns",
    "carries": "owns",
    "wields": "owns",
    "possesses": "owns",
    "worships": "serves",
    "works_for": "serves",
    "obeys": "serves",
    "knows_of": "knows",
    "met": "knows",
    "acquainted_with": "knows",
    "takes_part_in": "appears_in",
    "involved_in": "appears_in",
    "present_at": "appears_in",
}


@dataclass(slots=True)
class Ontology:
    """The core plus one campaign's extensions."""

    types: dict[str, TypeTerm] = field(default_factory=dict)
    predicates: dict[str, PredicateTerm] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        extra_types: Iterable[TypeTerm] = (),
        extra_predicates: Iterable[PredicateTerm] = (),
    ) -> Ontology:
        onto = cls({t.key: t for t in CORE_TYPES}, {p.key: p for p in CORE_PREDICATES})
        for t in extra_types:
            onto.types.setdefault(t.key, t)  # the core always wins
        for p in extra_predicates:
            onto.predicates.setdefault(p.key, p)
        return onto

    def is_a(self, type_key: str, ancestor: str) -> bool:
        seen: set[str] = set()
        key: str | None = type_key
        while key is not None and key not in seen:
            if key == ancestor:
                return True
            seen.add(key)
            term = self.types.get(key)
            key = term.parent if term else None
        return False

    def is_any(self, type_key: str, allowed: Sequence[str]) -> bool:
        return any(self.is_a(type_key, a) for a in allowed)

    def active_type(self, key: str) -> TypeTerm:
        term = self.types.get(key)
        if term is None or term.status != ACTIVE:
            raise MemoryRuleError(f"Unknown kind of thing: {key}.")
        return term

    def active_predicate(self, key: str) -> PredicateTerm:
        term = self.predicates.get(key)
        if term is None or term.status != ACTIVE:
            raise MemoryRuleError(f"Unknown kind of relationship: {key}.")
        return term

    # ---- extensions ------------------------------------------------------------------

    def existing_match(self, key: str, label: str) -> str | None:
        """An existing term this new one should reuse, or None (rule 3)."""
        if key in SYNONYMS:
            return SYNONYMS[key]
        label_key = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if label_key in SYNONYMS:
            return SYNONYMS[label_key]
        for existing in (*self.types.values(), *self.predicates.values()):
            if existing.status != ACTIVE:
                continue
            existing_label = re.sub(r"[^a-z0-9]+", "_", existing.label.casefold()).strip("_")
            if key == existing.key or label_key in (existing.key, existing_label):
                return existing.key
            if difflib.SequenceMatcher(None, key, existing.key).ratio() >= SIMILAR_KEY_RATIO:
                return existing.key
        return None

    def check_new_term(
        self, key: str, label: str, description: str, examples: Sequence[str], reason: str
    ) -> None:
        """The checks every new type or relationship passes (rules 3 and 4)."""
        if not KEY_PATTERN.match(key):
            raise MemoryRuleError(
                f"{key!r} isn't a valid key (lowercase letters, digits and _, 2-40 long)."
            )
        if key in self.types or key in self.predicates:
            raise MemoryRuleError(f"{key} already exists.")
        match = self.existing_match(key, label)
        if match is not None:
            raise MemoryRuleError(f"Use the existing {match} instead of adding {key}.")
        for text, what, limit in (
            (label, "a label", LABEL_MAX),
            (description, "a description", DESCRIPTION_MAX),
            (reason, "a reason", DESCRIPTION_MAX),
        ):
            if not text.strip() or len(text) > limit:
                raise MemoryRuleError(f"A new term needs {what} (at most {limit} characters).")
        if not examples or any(not e.strip() or len(e) > DESCRIPTION_MAX for e in examples):
            raise MemoryRuleError("A new term needs at least one example.")

    def check_new_type(self, term: TypeTerm) -> None:
        if term.parent is None:
            raise MemoryRuleError("A new kind of thing needs a parent kind.")
        self.active_type(term.parent)

    def check_new_predicate(self, term: PredicateTerm) -> None:
        if not term.subject_types or not term.object_types:
            raise MemoryRuleError("A new relationship needs the kinds of things it connects.")
        for key in (*term.subject_types, *term.object_types):
            self.active_type(key)
        if term.parent is not None:
            self.active_predicate(term.parent)
        for key in term.conflicts_with:
            self.active_predicate(key)
        if term.symmetric and set(term.subject_types) != set(term.object_types):
            raise MemoryRuleError("A two-way relationship must connect the same kinds.")
