"""The campaign memory rules (the ontology): which kinds of things and relationships
exist, and what each relationship may connect (docs/PLAN.md, "Campaign memory rules").

The **core** is fixed in code and changes only in a reviewed release. Each campaign may
**extend** it (stored in memory_types / memory_predicates), never change it. Extensions
must reuse what exists when they can, are only ever added or deprecated, and come with a
description, examples and a reason.
"""

from __future__ import annotations

import difflib
import functools
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory.models import (
    ABANDONED,
    ALIVE,
    BEING_CONDITIONS,
    CAPTURED,
    CONDITIONS,
    DEAD,
    DESCRIPTION_MAX,
    DESTROYED,
    DM,
    GOING,
    HINTED,
    INTACT,
    LOST,
    MISSING,
    NPC,
    OPEN,
    PLAYER_CHARACTER,
    RESOLVED,
    THING_CONDITIONS,
    THREAD_STATES,
    TRANSFORMED,
    UNKNOWN,
    MemoryRuleError,
)

log = logging.getLogger(__name__)

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
    # Carries a character's own attitude, loyalty or vow, so only the character's own voice may
    # state it (#2562, `memory.voice`; #2619). A flag on the term rather than a list in code, so a
    # relationship can carry it. Not part of the stored definition or its meaning: setting it
    # changes who may speak for the character, not what the relationship says, so no new version.
    inner_state: bool = False


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
    # A storyline the table is following (#1662), and the four kinds the DM tells apart. Each
    # subtype is a kind of its own under `thread`, like a clue under an idea, so anything that
    # takes a thread takes all of them.
    _t("thread", "story thread", "A storyline the table is following: a quest, a mystery, a hook."),
    _t("quest", "quest", "A job someone wants done, with a goal and a reward.", "thread"),
    _t("mystery", "mystery", "Something the table is trying to find out.", "thread"),
    _t("hook", "hook", "A lead that could become a quest if the table follows it.", "thread"),
    _t("goal", "goal", "Something a character or the party wants to achieve.", "thread"),
    _t(
        "promise",
        "promise",
        "Something someone swore or agreed to do for someone else, kept or broken later.",
        "thread",
    ),
    # Something a player character or the party did that mattered to someone (#1732): the DM
    # records it in two presses and it moves how the people it mattered to feel about the doer.
    # A kind under `event`, so everything that takes an event takes it.
    _t(
        "deed",
        "deed",
        "Something a player character or the party did that mattered to someone.",
        "event",
    ),
    # A piece of information that can be learned (#1649): its own entry, so "who knows it" is an
    # ordinary `knows` fact. A subtype of `concept`, so every range that takes an idea takes it.
    _t(
        "clue",
        "clue or secret",
        "A piece of information that can be learned: a rumour, a secret.",
        "concept",
    ),
    # A whole people or kind: the tabaxi, elves, outsiders (#1655). Its own entry, so a group or
    # place can have an attitude toward it. The DM says who is of it; it is never copied from a
    # character's species link.
    _t("people", "people", "A whole people or kind: the tabaxi, the elves, outsiders.", "concept"),
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
    "deity": ("character", NPC),  # a god is an NPC (#2562)
    "creature": ("character", NPC),
}
# What the migration (and an older backup, and an old change-log row) made of an older kind:
# (kind now, role, needs a look). A named creature is a character the DM decides about; a
# spell is an idea to look at.
MIGRATED_KINDS: Mapping[str, tuple[str, str | None, bool]] = {
    "player_character": ("character", PLAYER_CHARACTER, False),
    "npc": ("character", NPC, False),
    "deity": ("character", NPC, False),
    "creature": ("character", None, True),
    "spell": ("concept", None, True),
}
GOD_NOTE = "A god."


def god_is_an_npc(description: str) -> str:
    """The description of an entry that was a god and is an NPC now (#2562): it keeps "god" in its
    words, as a note, unless they already say so. Within the description limit; nothing else is
    changed."""
    if re.search(r"\b(god|goddess|deity)\b", description, re.IGNORECASE):
        return description
    room = DESCRIPTION_MAX - len(GOD_NOTE) - 1
    return f"{description[:room].rstrip()} {GOD_NOTE}".strip()


SPELL_NOT_KEPT = "Spells aren't kept in campaign memory. To look one up, use /dmbot rule."


UNNAMED_MONSTER = (
    "That's a kind of monster, not one character. Name them (like 'Snot the goblin') "
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
    t.key
    for t in CORE_TYPES
    if t.parent is None and t.status == ACTIVE and t.key not in ("person", "thread")
)  # (a thread only takes its own connections, #1662)

# The catch-all relationship a refused term's fact is kept on, with the steward's words as its
# label (in the fact's `detail`), so a refusal never loses the fact (#1298, #1303).
FALLBACK_PREDICATE = "associated_with"

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
        "promised_by",
        "was promised by",
        "Who made a promise: the character or group that swore or agreed to do it. The DM's word.",
        ("character", "faction"),
        ("promise",),
        covers=("the character or group who swore, vowed or agreed to do something",),
        not_this=(("a character who takes part in a thread", "in_thread"),),
        examples=("The promise to bring the healer back was made by Kael.",),
        inverse_label="made the promise",
        synonyms=("vowed", "swore_to", "pledged_by"),
    ),
    PredicateTerm(
        "promised_to",
        "was promised to",
        "Who a promise was made to: the character, group or place it was sworn to. The DM's word.",
        ("character", "faction", "place"),
        ("promise",),
        covers=("the character, group or place a promise was made to",),
        not_this=(("who made the promise", "promised_by"),),
        examples=("The promise to bring the healer back was made to the village of Dunhollow.",),
        inverse_label="was promised",
        synonyms=("owed_to",),
    ),
    PredicateTerm(
        "in_thread",
        "is involved in",
        "Has a part in a story thread, and the part is the detail: the quest giver, the target, "
        "the villain, a victim, the reward.",
        ("character", "faction", "item", "place", "event", "concept"),
        ("thread",),
        covers=(
            "a character who gives, chases or is the target of a quest",
            "an item that is the reward, or a place where a mystery centres",
        ),
        not_this=(
            ("an event that moves a thread along", "advances"),
            ("an event that ends a thread", "resolves"),
        ),
        examples=("Mira is involved in the Missing Heir (the quest giver).",),
        inverse_label="has taking part",
        synonyms=("part_of_quest", "quest_giver_of", "target_of"),
    ),
    PredicateTerm(
        "advances",
        "moves along",
        "Something that happened moved a story thread forward, without ending it.",
        ("event",),
        ("thread",),
        covers=("a scene where the party learns a new lead for a quest",),
        not_this=(("an event that finishes the thread", "resolves"),),
        examples=("The Ambush at the Ford moves along the Missing Heir.",),
        inverse_label="was moved by",
        synonyms=("progresses", "pushes_forward"),
    ),
    PredicateTerm(
        "resolves",
        "ends",
        "Something that happened ended a story thread: the quest done, the mystery solved.",
        ("event",),
        ("thread",),
        covers=("the final fight that finishes a quest",),
        not_this=(("an event that only moves a thread along", "advances"),),
        examples=("The Heir Found ends the Missing Heir.",),
        inverse_label="was ended by",
        synonyms=("concludes", "completes"),
    ),
    PredicateTerm(
        "led_to",
        "led to",
        "One thing led to another, as the DM says: a clue led to a quest, an event led to a "
        "mystery. The DM's word, never guessed.",
        ("clue", "event", "thread"),
        ("thread", "event"),
        covers=("a clue that sent the party to a place or into a quest",),
        not_this=(
            ("an event that moves a thread along", "advances"),
            ("an event that ends a thread", "resolves"),
        ),
        examples=("The torn map led to the Missing Heir.",),
        inverse_label="came from",
        synonyms=("sparked", "set_off"),
    ),
    PredicateTerm(
        "of_people",
        "is one of",
        "Belongs to a whole people or kind, as the DM says: a tabaxi, an elf, an outsider. "
        "The DM's word; it is not read from the character's species.",
        ("character",),
        ("people",),
        covers=("a character who is a tabaxi, a dwarf or an outsider",),
        not_this=(
            ("belonging to a guild or an army", "member_of"),
            ("living in a town", "located_in"),
        ),
        examples=("Kesh is one of the tabaxi.",),
        inverse_label="has among them",
        synonyms=("is_a", "race_of", "belongs_to_people"),
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
        inner_state=True,
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
        inner_state=True,
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
        "For an item of which only one exists, one holder at a time: a new holder is flagged "
        "until the old fact is ended.",
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
        inner_state=True,
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
        inner_state=True,
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
    PredicateTerm(
        "did",
        "did",
        "Who did a deed: a player character or the party. The DM records it; nobody guesses it.",
        ("character", "faction"),
        ("deed",),
        covers=("a player character or the party who did something that mattered to someone",),
        not_this=(("a character who merely took part in an event", "appears_in"),),
        examples=("The party did Saved the mill.",),
        inverse_label="was done by",
        synonyms=("performed", "committed"),
    ),
    PredicateTerm(
        "affected",
        "mattered to",
        "Who a deed mattered to, and how it landed in the detail: helped them, pleased them, "
        "annoyed them or hurt them. An NPC, a group or a place.",
        ("deed",),
        ("character", "faction", "place"),
        covers=("an NPC, a group or a place a deed helped or hurt",),
        not_this=(("an event that took place in a location", "located_in"),),
        examples=("Saved the mill mattered to Brindle (helped them).",),
        inverse_label="was affected by",
        synonyms=("mattered_to", "touched"),
    ),
    PredicateTerm(
        FALLBACK_PREDICATE,
        "is associated with",
        "The catch-all for a link the table said that DMbot has no relationship for. Each fact "
        "keeps the words it was said in as its own label, so a refused new term never loses the "
        "fact. Never chosen on purpose: a better-fitting relationship always wins.",
        _ANY,
        _ANY,
        covers=("a link in the story's own words that no named relationship fits",),
        not_this=(
            ("one thing is inside another", "located_in"),
            ("a person who belongs to a group", "member_of"),
            ("a person who has heard of another", "knows"),
        ),
        examples=(
            "Aldric is blood-bound to Cerric (the table's words; no relationship is named so).",
        ),
        inverse_label="has an association with",
    ),
)

# State facts (docs/STORY_MEMORY.md, "State facts"): what a being or a thing is right now. Only
# beings (characters) and items and places have one; anything else is refused. The changes
# allowed are this small table: from a condition, which ones may follow. Anything not listed
# (a character "missing" to "intact") is no change at all.
STATE_CHANGES: Mapping[str, frozenset[str]] = {
    # A story thread moves as the DM says; going back from an end is the DM's alone.
    HINTED: frozenset({OPEN, GOING, RESOLVED, ABANDONED}),
    OPEN: frozenset({HINTED, GOING, RESOLVED, ABANDONED}),
    GOING: frozenset({HINTED, OPEN, RESOLVED, ABANDONED}),
    RESOLVED: frozenset({OPEN, GOING}),
    ABANDONED: frozenset({HINTED, OPEN, GOING}),
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
    {
        (DEAD, ALIVE),
        (DEAD, TRANSFORMED),
        (DESTROYED, INTACT),
        (RESOLVED, OPEN),
        (RESOLVED, GOING),
        (ABANDONED, HINTED),
        (ABANDONED, OPEN),
        (ABANDONED, GOING),
    }
)
STATE_NOT_KEPT = "DMbot keeps track of this only for characters, items, places and story threads."
STATE_BAD = "That isn't one of the things DMbot keeps track of for {label}."
STATE_NEEDS_DM = "Only the DM can say that {name} is back."


def conditions_for(onto: Ontology, type_key: str) -> tuple[str, ...] | None:
    """The conditions an entry of this kind can have, or None when it has no state."""
    if onto.is_a(type_key, "thread"):
        return THREAD_STATES
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

# ---- the ontology steward's rules (docs/STORY_MEMORY.md, "How the ontology steward decides a new
# term"; #1298). Fixed rules decide, never an AI, and the same proposal always gets the same
# answer: nothing here reads a campaign.

# Words from the game's rules. They live in the rules data, and campaign memory only links to them,
# so none of them may become a kind of thing or a relationship. Compared as plain words (case,
# spaces, _ and - ignored). The SRD's condition names are added from the rules data itself.
RULES_WORDS = frozenset(
    {
        "feat", "spell", "cantrip", "class", "subclass", "species", "race", "background",
        "ability", "ability score", "skill", "saving throw", "death saving throw",
        "proficiency", "proficiency bonus", "expertise", "condition", "action", "bonus action",
        "reaction", "attack", "initiative", "hit points", "hit die", "armor class",
        "difficulty class", "damage type", "spell slot", "concentration", "advantage",
        "disadvantage", "attunement", "inspiration", "short rest", "long rest", "cover",
        "ability check", "level", "experience points", "stat block", "creature type", "round",
    }
)  # fmt: skip
# Plain-English words that mean a rules word in the game, so a proposal named like this is the
# rules concept, not a story relationship. Each says why (the value is the rules word it is).
# These are kept apart from SYNONYMS, which map a word to a story relationship to reuse.
RULES_SYNONYMS: Mapping[str, str] = {
    "check": "ability check",  # "make a check" is an ability check, not an inspection
    "save": "saving throw",  # "a save" is a saving throw, not rescuing someone
    "saves": "saving throw",  # the plural, said the same way
    "turn": "round",  # a turn in a round, or turning undead: never a story link
    "round": "round",  # six seconds of combat
    "rest": "short rest",  # a short or long rest, not a pause
    "attune": "attunement",  # bonding to a magic item, not a link between two names
    "attuned": "attunement",  # the same, as it is said at the table
    "ac": "armor class",  # how hard something is to hit
    "hp": "hit points",  # a number on a sheet
    "dc": "difficulty class",  # the number a roll must reach
    "prof": "proficiency",  # short for proficiency
    "proficient": "proficiency",  # trained in a skill or tool
    "dis": "disadvantage",  # said for "rolls with disadvantage"
    "adv": "advantage",  # said for "rolls with advantage"
    "slot": "spell slot",  # not a place in a list
    "reach": "reach",  # how far a creature can strike in melee
    "bless": "spell",  # a spell's name, not an act of favour between names
}


@functools.cache
def rules_words() -> frozenset[str]:
    """Every rules word as plain words: the list above and the SRD's condition names (read
    from the rules data, once, so they are not typed twice), without the words slice 1 of
    story memory uses for what a being or thing is now (alive, dead, intact…)."""
    from dmbot.rules.index import srd  # the data is only read when a term is first judged

    conditions = {_words(n) for n in srd().names("condition", "2024")}
    state_words = {_words(c) for c in CONDITIONS}
    return frozenset(_words(w) for w in RULES_WORDS) | (conditions - state_words)


def rules_word_for(*texts: str, everyday: bool = True) -> str | None:
    """The rules word that any of these texts is (or, with `everyday`, names through
    RULES_SYNONYMS), or None. The everyday words ("reach", "turn", "rest") can be a fair
    label for a story relationship, so the live new-term check passes everyday=False for a
    label and only refuses them as a key."""
    known = rules_words()
    for text in texts:
        words = _words(text)
        if words in known:
            return words
        if everyday and words in RULES_SYNONYMS:
            return RULES_SYNONYMS[words]
    return None


REASON_RULES_WORD = "rules word"
REASON_SYNONYM = "synonym of an existing term"
REASON_EXACT = "same name or label as an existing term"
REASON_SPELLING = "spelled like an existing term"
REASON_SHAPE = "does not say what it connects"
REASON_CLOSE_MEANING = "means the same as an existing term"
REASON_BETWEEN = "not clearly different from an existing term"


@dataclass(frozen=True, slots=True)
class Proposal:
    """A term the steward is asked about: a kind of thing or a relationship, in the words the
    AI used, with (for a relationship) the kinds of things it connects."""

    key: str
    label: str
    kind: str = "relationship"  # "relationship" or "type"
    subject_types: tuple[str, ...] = ()
    object_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Accepted:
    """A new term the rules let in. `meaning_checked` is False until the meaning check
    (embeddings) exists, so the health numbers can say how many passed unchecked."""

    key: str
    meaning_checked: bool = False


@dataclass(frozen=True, slots=True)
class Reused:
    """An existing term the proposal should use instead."""

    existing: str
    reason: str


@dataclass(frozen=True, slots=True)
class Fallback:
    """Where a refused term's fact goes: the catch-all, labelled with the proposal's words."""

    predicate: str
    label: str


@dataclass(frozen=True, slots=True)
class Refused:
    """No new term. The fact is not lost: it goes on `fallback` (never the term itself)."""

    reason: str
    fallback: Fallback


Decision = Accepted | Reused | Refused
# What the meaning check says about a proposal: "far" (accept), "close" (reuse `existing`),
# "between" (refuse), or "not_checked" (nothing to say yet).
Meaning = tuple[str, str | None]
MeaningCheck = Callable[["Proposal", "Ontology"], Meaning]


def not_checked_yet(proposal: Proposal, onto: Ontology) -> Meaning:
    """Step 3, meaning by embedding, is not built (it needs its own measurement). Until it is,
    it has nothing to say against a proposal that passed the exact steps."""
    return ("not_checked", None)


def decide_term(
    onto: Ontology, proposal: Proposal, meaning: MeaningCheck = not_checked_yet
) -> Decision:
    """Whether a proposed term is accepted, replaced by an existing one, or refused. Fixed
    rules in a fixed order, so the same proposal always gets the same answer:
    1. a rules word is refused; 2. a synonym of an existing term reuses it; 3. the same name
    or label reuses it; 4. a spelling at least 85% alike reuses it; 5. then its meaning (a
    stub until embeddings exist): very close reuses, in between is refused, far is accepted.
    A refusal always names where the fact goes (`Fallback`), so the fact is never lost."""
    fallback = Fallback(FALLBACK_PREDICATE, proposal.label.strip() or proposal.key)
    if rules_word_for(proposal.key, proposal.label) is not None:
        return Refused(REASON_RULES_WORD, fallback)
    key = _words(proposal.key).replace(" ", "_")
    label_key = _words(proposal.label).replace(" ", "_")
    for text in (key, label_key):
        if text in SYNONYMS:
            return Reused(SYNONYMS[text], REASON_SYNONYM)
    terms: Sequence[TypeTerm | PredicateTerm] = list(
        onto.types.values() if proposal.kind == "type" else onto.predicates.values()
    )
    live = [t for t in terms if t.status == ACTIVE]
    for term in live:
        if key == term.key or label_key in (term.key, _words(term.label).replace(" ", "_")):
            return Reused(term.key, REASON_EXACT)
    for term in live:
        if difflib.SequenceMatcher(None, key, term.key).ratio() >= SIMILAR_KEY_RATIO:
            return Reused(term.key, REASON_SPELLING)
    if proposal.kind == "relationship" and not _connects_something(onto, proposal):
        return Refused(REASON_SHAPE, fallback)
    verdict, existing = meaning(proposal, onto)
    if verdict == "close" and existing is not None:
        return Reused(existing, REASON_CLOSE_MEANING)
    if verdict == "between":
        return Refused(REASON_BETWEEN, fallback)
    return Accepted(key, meaning_checked=verdict == "far")


def _connects_something(onto: Ontology, proposal: Proposal) -> bool:
    """A relationship says what it connects, and only kinds of things that exist."""
    return bool(proposal.subject_types and proposal.object_types) and all(
        t in onto.types and onto.types[t].status == ACTIVE
        for t in (*proposal.subject_types, *proposal.object_types)
    )


@dataclass(slots=True)
class Tally:
    """The steward's health numbers for one session: how many terms were refused (and why),
    reused and accepted (and how many of those without a meaning check). For the server log
    only; never shown to a player or a DM."""

    refused: Counter[str] = field(default_factory=Counter)
    reused: int = 0
    accepted: int = 0
    unchecked: int = 0

    def record(self, decision: Decision) -> None:
        if isinstance(decision, Refused):
            self.refused[decision.reason] += 1
        elif isinstance(decision, Reused):
            self.reused += 1
        else:
            self.accepted += 1
            self.unchecked += not decision.meaning_checked

    def line(self) -> str:
        """Counts only, never a term's words (those are the campaign's)."""
        refused = ", ".join(f"{n} {why}" for why, n in sorted(self.refused.items())) or "none"
        return (
            f"accepted {self.accepted} ({self.unchecked} without a meaning check), "
            f"reused {self.reused}, refused: {refused}"
        )

    def write_to_log(self) -> None:
        log.info("Ontology steward this session: %s", self.line())


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
        if (
            rules_word_for(key) is not None  # a key may not be any rules word, even an everyday one
            or rules_word_for(label, everyday=False) is not None  # a label only a true one
        ):
            raise MemoryRuleError(
                f"{label.strip() or key} is a word from the game's rules: DMbot links to the "
                "rules, it doesn't make a term of it."
            )
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
