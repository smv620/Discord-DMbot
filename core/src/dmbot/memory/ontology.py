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

from dmbot.memory.models import DESCRIPTION_MAX, GOD, NPC, PLAYER_CHARACTER, MemoryRuleError

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


def _old(key: str, label: str, replaced_by: str) -> TypeTerm:
    """A kind from before #1034, kept so old data and backups still read: not offered for
    new entries (a role or a rules category is not a kind of thing)."""
    return TypeTerm(
        key, None, label, f"Now {replaced_by}.", status=DEPRECATED, replaced_by=replaced_by
    )


# Story kinds: unique things in this campaign. One kind for each entry, what it
# fundamentally is (#1034, owner decision 2026-10-10). Roles (player character, NPC, god) are
# traits of a character (`Entity.role`); species, creature type, stat block, class and
# background are links to the rules (`RuleLink`), never kinds.
CORE_TYPES: tuple[TypeTerm, ...] = (
    _t(
        "character",
        "character",
        "Always unique: a player's character, an NPC, a god, a named monster, a named horse.",
    ),
    _t("place", "place", "A town, building, region, room or any other location."),
    _t("faction", "group", "A group, guild, army, cult, family or other organization."),
    _t("item", "item", "An object: a weapon, amulet, letter, ship."),
    _t("event", "event", "Something that happened or will happen: a battle, a wedding."),
    _t("concept", "idea", "Anything else worth remembering: a prophecy, a curse, a law."),
    _old("player_character", "player character", "character"),
    _old("npc", "NPC", "character"),
    _old("deity", "god", "character"),
    _old("creature", "creature", "character"),
    _old("spell", "spell", "concept"),
)

# The old kinds as a (kind, role) to write now. A named creature or monster is a character
# the DM plays; a spell is not an entry (it is in the rules data).
LEGACY_KINDS: Mapping[str, tuple[str, str | None]] = {
    "player_character": ("character", PLAYER_CHARACTER),
    "npc": ("character", NPC),
    "deity": ("character", GOD),
    "creature": ("character", NPC),
}
# What the migration (and an older backup, and an old change-log row) made of an older kind:
# (kind now, role, needs a look). A named creature is a character the DM decides about; a
# spell is an idea to look at.
MIGRATED_KINDS: Mapping[str, tuple[str, str | None, bool]] = {
    "player_character": ("character", PLAYER_CHARACTER, False),
    "npc": ("character", NPC, False),
    "deity": ("character", GOD, False),
    "creature": ("character", None, True),
    "spell": ("concept", None, True),
}
SPELL_NOT_KEPT = "Spells aren't kept in campaign memory. To look one up, use /dmbot rule."


def resolve_kind(kind: str, role: str | None = None) -> tuple[str, str | None]:
    """(kind, role) to store for a kind a caller names. The older kinds (npc,
    player_character, deity, creature) become a character with that role; a spell is
    refused. A kind that is already current passes through with its role."""
    if kind == "spell":
        raise MemoryRuleError(SPELL_NOT_KEPT)
    if kind in LEGACY_KINDS:
        legacy_kind, legacy_role = LEGACY_KINDS[kind]
        if role not in (None, legacy_role):
            raise MemoryRuleError("A character has one role at a time.")
        return legacy_kind, legacy_role
    return kind, role


_BEINGS = ("character",)
_ANY = tuple(t.key for t in CORE_TYPES if t.parent is None and t.status == ACTIVE)

CORE_PREDICATES: tuple[PredicateTerm, ...] = (
    PredicateTerm(
        "located_in",
        "is in",
        "Where someone or something is.",
        ("character", "place", "faction", "item", "event"),
        ("place",),
        max_per_subject=1,
    ),
    PredicateTerm(
        "member_of",
        "is a member of",
        "Belongs to a group.",
        ("character",),
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
        ("item", "place", *_BEINGS),
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

# A campaign can't add a kind that is really a role or a rules category (#1034): those are a
# character's role or its links to the rules. Compared as plain words (case, spaces, _ and -
# ignored): the roles, the rules vocabulary, the classes, the species and the creature types.
BLOCKED_KINDS = frozenset(
    {
        # roles
        "npc", "pc", "player", "player character", "god", "goddess", "deity",
        # rules vocabulary
        "monster", "creature", "beast", "race", "species", "creature type", "class",
        "background", "feat", "spell", "skill", "ability", "stat block",
        # classes
        "barbarian", "bard", "cleric", "druid", "fighter", "monk", "paladin", "ranger", "rogue",
        "sorcerer", "warlock", "wizard",
        # species
        "human", "elf", "dwarf", "halfling", "gnome", "orc", "tiefling", "dragonborn",
        "goliath", "aasimar", "half elf", "half orc",
        # creature types
        "aberration", "celestial", "construct", "dragon", "elemental", "fey", "fiend", "giant",
        "humanoid", "monstrosity", "ooze", "plant", "undead",
        # the usual monsters (a named one is a character with a stat block)
        "goblin", "hobgoblin", "bugbear", "kobold", "gnoll", "lizardfolk", "troll", "ogre",
    }
)  # fmt: skip
NOT_A_KIND = (
    "{label} can't be a kind of thing: it is a role, or something from the rules. Make it a "
    "character (an NPC, a god…) or link it to the rules."
)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


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
        terms: list[TypeTerm | PredicateTerm] = [*self.types.values(), *self.predicates.values()]
        for existing in terms:
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
        if _words(term.key) in BLOCKED_KINDS or _words(term.label) in BLOCKED_KINDS:
            raise MemoryRuleError(NOT_A_KIND.format(label=term.label.strip() or term.key))
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
