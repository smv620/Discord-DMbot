"""How an entry's kind, role and links to the rules read in plain words (#1034).

An entry's kind says what it fundamentally is (character, place, item, group, event, idea).
A character's role (player character, NPC, god) and its links to the rules (species,
creature type, stat block, class, background) are said beside it: "character · NPC · goblin
(humanoid) · Goblin Warrior". Pure: nothing here reads a database.
"""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.memory.models import (
    BACKGROUND,
    CLASS,
    CREATURE_TYPE,
    GOD,
    NPC,
    PLAYER_CHARACTER,
    ROLE_LABELS,
    SPECIES,
    STAT_BLOCK,
    Entity,
    RuleLink,
)

KIND_LABELS = {
    "character": "character",
    "place": "place",
    "faction": "group",
    "item": "item",
    "event": "event",
    "concept": "idea",
}
# The older kinds, in the words people used for them.
OLD_KIND_LABELS = {
    "player_character": "player character",
    "npc": "NPC",
    "deity": "god",
    "creature": "creature",
    "spell": "spell",
}
LEGACY_TAG = "[Legacy 2014]"
NOT_IN_RULES = "not in DMbot's rules"

# The key the lists and the kind menus use for a (kind, role): the older kind's name for a
# character with a role, the kind itself otherwise.
_ROLE_KEYS = {PLAYER_CHARACTER: "player_character", NPC: "npc", GOD: "deity"}


def kind_key(kind: str, role: str | None) -> str:
    """The key for a kind and role in lists and menus: a character with a role is
    `npc`, `player_character` or `deity`; any other entry is its kind."""
    if kind == "character" and role in _ROLE_KEYS:
        return _ROLE_KEYS[role]
    return kind


def _shown(link: RuleLink) -> str:
    text = link.name
    if link.edition == "2014":
        text += f" {LEGACY_TAG}"
    if not link.known:
        text += f" ({NOT_IN_RULES})"
    return text


def links_text(links: Sequence[RuleLink]) -> list[str]:
    """The links as they read on a card, in a fixed order: species with its creature type
    in brackets, then the stat block, classes and background."""
    by_kind: dict[str, list[RuleLink]] = {}
    for link in links:
        by_kind.setdefault(link.kind, []).append(link)
    parts: list[str] = []
    species = by_kind.get(SPECIES, [])
    kind = by_kind.get(CREATURE_TYPE, [])
    if species and kind:
        parts.append(f"{_shown(species[0])} ({kind[0].name})")
    elif species:
        parts.append(_shown(species[0]))
    elif kind:
        parts.append(kind[0].name)
    for key in (STAT_BLOCK, CLASS, BACKGROUND):
        parts += [_shown(link) for link in by_kind.get(key, [])]
    return parts


def kind_phrase(entity: Entity, links: Sequence[RuleLink] = ()) -> str:
    """ "character · NPC · goblin (humanoid) · Goblin Warrior": the kind, a character's role,
    and its links to the rules; "needs a look" for an entry moved from an older kind."""
    parts = [KIND_LABELS.get(entity.type, OLD_KIND_LABELS.get(entity.type, entity.type))]
    if entity.role is not None:
        parts.append(ROLE_LABELS.get(entity.role, entity.role))
    parts += links_text(links)
    if entity.needs_look:
        parts.append("needs a look")
    return " · ".join(parts)
