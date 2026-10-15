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

from dmbot.memory.models import (
    ALIVE,
    BEING_CONDITIONS,
    CAPTURED,
    DEAD,
    DESCRIPTION_MAX,
    DESTROYED,
    DM,
    GOD,
    INTACT,
    LOST,
    MISSING,
    NPC,
    PLAYER_CHARACTER,
    THING_CONDITIONS,
    TRANSFORMED,
    UNKNOWN,
    MemoryRuleError,
)

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
    # The full definition (#1189), kept in the database by version so a fact can say which one
    # it was written under. `covers`: the D&D cases it is for. `not_this`: near cases, each
    # with the relationship it belongs to. `synonyms`: other keys that mean this one.
    covers: tuple[str, ...] = ()
    not_this: tuple[tuple[str, str], ...] = ()
    examples: tuple[str, ...] = ()
    inverse_label: str = ""  # reads from the object's side: "Cerric *is allied with* Belleros"
    synonyms: tuple[str, ...] = ()
    # Up when the MEANING changes (what it covers, what it connects, its synonyms). A
    # wording-only fix keeps the version.
    version: int = 1
    # At any one time, for one object that is one of a kind (`carries`: one holder for a unique
    # item). Core terms only; a campaign's own relationships have no such limit.
    max_per_object: int | None = None


NOT_THIS_SEP = " -> "


def encode_not_this(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """The "not this" cases as plain text for the database: "near case -> relationship"."""
    return [f"{near}{NOT_THIS_SEP}{key}" for near, key in pairs]


def decode_not_this(items: Iterable[str]) -> tuple[tuple[str, str], ...]:
    out = []
    for item in items:
        near, _, key = item.rpartition(NOT_THIS_SEP)
        if near and key:
            out.append((near, key))
    return tuple(out)


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
    # The one kind that is not part of the story (#1188): a real person at this campaign's table,
    # tied to their Discord account. Its names are what the table calls them. PlotBot, NPCBot
    # and the story checks ignore it (it is left out of `_ANY` below).
    _t(
        "person",
        "player or DM",
        "A real person at the table: a player or the DM. Not part of the story.",
    ),
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


UNNAMED_MONSTER = (
    "That's a kind of monster, not one person. Name them (like 'Snot the goblin') "
    "or link it as a stat block on a character."
)


PERSON_NOT_LISTED = (
    "DMbot adds players and the DM by itself, so you don't need to list them. Add their "
    "character instead, then say who plays it."
)
# "DM" is the person running the game, never a name in the story (#1188): kept out of
# every name and other name so a line like "the DM says" can't turn into an entry.
DM_WORDS = frozenset({"dm", "gm", "dungeon master", "game master"})
DM_WORD_REFUSED = (
    '"DM" is the person running the game, so it can\'t be a name in the story. '
    "Try the character's own name."
)


def is_dm_word(text: str) -> bool:
    return " ".join(text.casefold().replace(".", "").split()) in DM_WORDS


def check_named(kind: str, name: str) -> None:
    """A caller that says `creature` means one named creature. A name that is a monster in
    the rules ("goblin", "three goblins", "Goblin Warrior") is a kind of monster, not an
    entry: refused (decided on #1034). "DM" and its likes are never a name."""
    if kind != "person" and is_dm_word(name):
        raise MemoryRuleError(DM_WORD_REFUSED)
    if kind != "creature":
        return
    from dmbot.rules import index  # late: the rules data loads on first use

    if index.srd().is_monster(name):
        raise MemoryRuleError(UNNAMED_MONSTER)


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
# The story's kinds: every active core kind except `person`, who sits at the table and not in
# the story, so "knows" and "appears in" never reach them (#1188).
_ANY = tuple(
    t.key for t in CORE_TYPES if t.parent is None and t.status == ACTIVE and t.key != "person"
)

CORE_PREDICATES: tuple[PredicateTerm, ...] = (
    PredicateTerm(
        "located_in",
        "is in",
        "Where someone or something is, or sits: a character in a town, a shop in a city, an "
        "item in a vault. One place at a time; a move ends the old fact.",
        ("character", "place", "faction", "item", "event"),
        ("place",),
        max_per_subject=1,
        covers=(
            "a character who is at, lives in or is held in a place",
            "a place inside a larger place (a tavern in a town)",
            "an item kept somewhere, an event held somewhere",
        ),
        not_this=(
            ("a character who belongs to a group that rules a place", "member_of"),
            ("a character who owns a place", "owns"),
        ),
        examples=("Belleros is in Waterdeep.", "The Rusty Anchor is in Port Nyanzaru."),
        inverse_label="has inside it",
        synonyms=("lives_in", "based_in", "is_in", "found_in"),
    ),
    PredicateTerm(
        "member_of",
        "is a member of",
        "Belongs to a group such as a guild, an army, a cult, a family house or an adventuring "
        "company. About joining the group, not about what the group is for.",
        ("character",),
        ("faction",),
        covers=(
            "a guild member, a soldier in an army, a cultist",
            "a member of a noble house or an adventuring company",
        ),
        not_this=(
            ("taking orders from a ruler without belonging to a group", "serves"),
            ("bound by a sworn oath to a person or group", "sworn_to"),
            ("related by blood or marriage", "kin_of"),
        ),
        examples=("Cerric is a member of the Harpers.", "Snot is a member of the Red Fang."),
        inverse_label="has as a member",
        synonyms=("belongs_to", "part_of", "in_group"),
    ),
    PredicateTerm(
        "ally_of",
        "is an ally of",
        "On the same side, and acting on it: friends, partners, allied groups. Both ways.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
        symmetric=True,
        conflicts_with=("enemy_of",),
        covers=(
            "friends and companions who help each other",
            "two groups that fight together or trade as partners",
        ),
        not_this=(
            ("one side works for the other", "serves"),
            ("a formal oath binds one to the other", "sworn_to"),
            ("they merely know each other", "knows"),
        ),
        examples=("Cerric is an ally of Belleros.", "The Harpers are an ally of the Order."),
        inverse_label="is an ally of",
        synonyms=("friend_of", "friends_with", "allied_with", "allies_with"),
    ),
    PredicateTerm(
        "enemy_of",
        "is an enemy of",
        "On opposite sides, and acting on it: rivals, enemies, groups at war. Both ways.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
        symmetric=True,
        conflicts_with=("ally_of",),
        covers=(
            "a villain and the heroes who oppose them",
            "two groups at war or in a feud",
        ),
        not_this=(
            ("they merely dislike each other in passing", "knows"),
            ("one of them rules the other", "serves"),
        ),
        examples=("Strahd is an enemy of Cerric.", "The Zhentarim are an enemy of the Harpers."),
        inverse_label="is an enemy of",
        synonyms=("rival_of", "enemies_with", "hates", "opposes"),
    ),
    PredicateTerm(
        "kin_of",
        "is family of",
        "Related by family: blood, marriage or adoption. The detail says how (sister, father). "
        "Both ways.",
        _BEINGS,
        _BEINGS,
        symmetric=True,
        covers=("siblings, parents and children, spouses, adopted family",),
        not_this=(
            ("a member of the same noble house with no family tie", "member_of"),
            ("a friend called a brother", "ally_of"),
        ),
        examples=("Cerric is family of Mira (his sister).",),
        inverse_label="is family of",
        synonyms=("related_to", "sibling_of", "parent_of", "child_of", "family_of"),
    ),
    PredicateTerm(
        "owns",
        "owns",
        "Has or holds something that is theirs: an item, a place, a ship, even a pet.",
        (*_BEINGS, "faction"),
        ("item", "place", *_BEINGS),
        covers=(
            "a character who owns an item, whoever has it in hand",
            "a character or group that owns a building, a ship or land",
        ),
        not_this=(
            ("the thing is only in that place", "located_in"),
            ("a person is bound to a master", "serves"),
            ("who has an item in hand right now", "carries"),
        ),
        examples=("Cerric owns the Sun Blade.", "The Order owns the Silver Tower."),
        inverse_label="is owned by",
        synonyms=("has", "possesses"),
        version=2,
    ),
    PredicateTerm(
        "carries",
        "carries",
        "Who has an item right now: in a pack, on a belt, in a hand. Owning is something else. "
        "For an item of which only one exists, one holder at a time; a move ends the old fact.",
        _BEINGS,
        ("item",),
        max_per_object=1,
        covers=(
            "a character who carries, wields or wears an item",
            "an item that changes hands: stolen, given, lent, dropped and picked up",
        ),
        not_this=(
            ("the item belongs to them, wherever it is", "owns"),
            ("the item is lying in a place", "located_in"),
        ),
        examples=("Cerric carries Heartseeker.", "Mira carries a longbow."),
        inverse_label="is held by",
        synonyms=("carrying", "holds", "wields", "wearing", "has_on"),
    ),
    PredicateTerm(
        "knows",
        "knows",
        "Knows someone or about something: has met them, or has learned of it. The weakest tie "
        "between two entries; use a stronger one when it fits.",
        _BEINGS,
        _ANY,
        covers=(
            "a character who has met another character",
            "a character who knows about a place, an item, an event or a secret",
        ),
        not_this=(
            ("they fight on the same side", "ally_of"),
            ("they are on opposite sides", "enemy_of"),
            ("a character who is in a place", "located_in"),
        ),
        examples=("Belleros knows Cerric.", "Cerric knows the Sun Blade."),
        inverse_label="is known by",
        synonyms=("knows_of", "met", "acquainted_with"),
    ),
    PredicateTerm(
        "serves",
        "serves",
        "Works for, obeys or worships, with no binding oath: a servant, a hireling, a follower of "
        "a god or a ruler.",
        (*_BEINGS, "faction"),
        (*_BEINGS, "faction"),
        covers=(
            "a servant, retainer or hired guard and their employer",
            "a worshipper and their god",
        ),
        not_this=(
            ("a sworn oath or pact binds them", "sworn_to"),
            ("they belong to the group as a member", "member_of"),
            ("they are partners, not master and servant", "ally_of"),
        ),
        examples=("Mira serves Lord Neverember.", "The cultist serves Tiamat."),
        inverse_label="is served by",
        synonyms=("worships", "works_for", "obeys"),
    ),
    PredicateTerm(
        "sworn_to",
        "is sworn to",
        "Bound by a sworn oath, vow or pact to a person, a group or an idea: a paladin's oath, "
        "a warlock's pact, a knight's fealty.",
        _BEINGS,
        (*_BEINGS, "faction", "concept"),
        covers=(
            "a paladin sworn to an oath or a deity",
            "a warlock bound by a pact to a patron",
            "a knight who swore fealty to a lord or an order",
        ),
        not_this=(
            ("service or worship with no binding oath", "serves"),
            ("belonging to an order as a member", "member_of"),
        ),
        examples=("Cerric is sworn to the Oath of Vengeance.", "Mira is sworn to Lord Aldric."),
        inverse_label="has sworn",
        synonyms=("pledged_to", "oath_to", "pact_with", "bound_to"),
    ),
    PredicateTerm(
        "plays",
        "plays",
        "A real person at the table plays this character (read from the character's side: "
        "played by). A character has one player at a time; a person may play several.",
        ("person",),
        ("character",),
        covers=("a player and the player character they control",),
        not_this=(("a DM voicing an NPC in the story", "knows"),),
        examples=("Mia plays Cerric.",),
        inverse_label="is played by",
        synonyms=("controls", "controlled_by"),
    ),
    PredicateTerm(
        "appears_in",
        "appears in",
        "Takes part in or shows up in an event: a battle, a wedding, a heist.",
        tuple(k for k in _ANY if k != "event"),
        ("event",),
        covers=("a character who fights in a battle or attends a wedding",),
        not_this=(
            ("an event that took place in a location", "located_in"),
            ("a character who merely heard about it", "knows"),
        ),
        examples=("Cerric appears in the Battle of the Bridge.",),
        inverse_label="has taking part",
        synonyms=("takes_part_in", "involved_in", "present_at"),
    ),
)

# State facts (docs/STORY_MEMORY.md, "State facts"): what a being or a thing is right now. Only
# beings (characters) and items and places have one; anything else is refused. The changes
# allowed are this small table: from a condition, which ones may follow. Anything not listed
# (a character "missing" to "intact") is no change at all.
STATE_CHANGES: Mapping[str, frozenset[str]] = {
    ALIVE: frozenset({DEAD, MISSING, CAPTURED, TRANSFORMED, UNKNOWN}),
    DEAD: frozenset({ALIVE, TRANSFORMED}),
    MISSING: frozenset({ALIVE, DEAD, CAPTURED, TRANSFORMED, UNKNOWN}),
    CAPTURED: frozenset({ALIVE, DEAD, MISSING, TRANSFORMED, UNKNOWN}),
    TRANSFORMED: frozenset({ALIVE, DEAD, MISSING, CAPTURED, UNKNOWN}),
    UNKNOWN: frozenset({ALIVE, DEAD, MISSING, CAPTURED, TRANSFORMED}),
    INTACT: frozenset({DESTROYED, LOST, UNKNOWN}),
    DESTROYED: frozenset({INTACT}),
    LOST: frozenset({INTACT, DESTROYED, UNKNOWN}),
}
# The way back from an end is never inferred from a proposal: only the DM's word ("brought
# back") brings the dead or a destroyed thing back, so a clear-sounding claim can't undo it.
DM_ONLY_CHANGES: frozenset[tuple[str, str]] = frozenset(
    {(DEAD, ALIVE), (DEAD, TRANSFORMED), (DESTROYED, INTACT)}
)
STATE_NOT_KEPT = "DMbot keeps track of this only for characters, items and places."
STATE_BAD = "That isn't one of the things DMbot keeps track of for {label}."
STATE_NEEDS_DM = "Only the DM can say that {name} is back."


def conditions_for(onto: Ontology, type_key: str) -> tuple[str, ...] | None:
    """The conditions an entry of this kind can have, or None when it has no state."""
    if onto.is_a(type_key, "character"):
        return BEING_CONDITIONS
    if onto.is_a(type_key, "item") or onto.is_a(type_key, "place"):
        return THING_CONDITIONS
    return None


def check_condition_change(
    onto: Ontology, type_key: str, name: str, old: str | None, new: str, source: str
) -> None:
    """Refuse a condition the kind can't have, a change the table doesn't allow, or a way
    back from an end that isn't the DM's. No old condition: anything the kind can have."""
    allowed = conditions_for(onto, type_key)
    if allowed is None:
        raise MemoryRuleError(STATE_NOT_KEPT)
    if new not in allowed:
        raise MemoryRuleError(STATE_BAD.format(label=name))
    if old is None or old == new:
        return
    if old not in allowed or new not in STATE_CHANGES.get(old, frozenset()):
        raise MemoryRuleError(f"{name} can't go from {old} to {new}.")
    if (old, new) in DM_ONLY_CHANGES and source != DM:
        raise MemoryRuleError(STATE_NEEDS_DM.format(name=name))


# A campaign can't add a kind that is really a role or a rules category (#1034): those are a
# character's role or its links to the rules. Compared as plain words (case, spaces, _ and -
# ignored): the roles, the rules vocabulary, the classes, the species and the creature types.
BLOCKED_KINDS = frozenset(
    {
        # roles
        "npc", "pc", "player", "player character", "god", "goddess", "deity",
        # the table's own people (#1188)
        "person", "player or dm", "dm", "gm", "dungeon master", "game master",
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


# Words an AI might reach for that mean a core relationship: reuse, don't create. Built from
# each relationship's own `synonyms` (#1189), so there is one place to say it.
SYNONYMS: Mapping[str, str] = {s: p.key for p in CORE_PREDICATES for s in p.synonyms}


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
