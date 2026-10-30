"""The one gate: what a viewer may know of a campaign's story (#2995; docs/STORY_MEMORY.md §9b).

Every statement (an entry existing under a name, a property of an entry, a connection, an other
name) has a list of the characters who know it (`memory_knowers`). This module works out, from those
lists alone, what each kind of viewer may know; a reader of story memory asks `visible_to` and
never reads a flag of its own. The words ("secret", "partly revealed", ...) come from the same
lists (`word_of`), never from stored flags.

The rules, all in `visible` (pure, so they are tested without a database):
- **The DM** knows everything and is never listed.
- **A player** knows what their own characters know (the union of them).
- **One character** knows what they know; the DM *seeing as* one character sees exactly that.
- **The party** is never a knower: seeing as the party, or anything read to the whole table, is only
  what **every** player character in play knows.
- A group grants nothing by itself: what Tran knows, Clyde does not.
- Learning a statement includes learning that the entries it names exist (§9b item 4), so a known
  connection, property or other name makes its entries known.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, LiteralString

from dmbot.memory._changes import (
    ALIASES,
    ENTITIES,
    KNOWERS,
    PERSPECTIVES,
    RELATIONS,
    STATES,
    Scope,
    Table,
)
from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER

ENTRY, PROPERTY, CONNECTION, ALIAS = "entry", "property", "connection", "alias"
KINDS = (ENTRY, PROPERTY, CONNECTION, ALIAS)

# The words for who knows a statement (§9b item 3), worked out and never stored.
SECRET = "secret"  # nobody but the DM
HIDDEN_FROM_THE_PLAYERS = "hidden_from_the_players"  # some NPCs, no player character
KNOWN_TO_ONE_NPC = "known_to_an_npc"  # one NPC (about themself, or the one who was told)
PARTLY_REVEALED = "partly_revealed"  # some player characters, not all
KNOWN_TO_THE_PARTY = "known_to_the_party"  # every player character


@dataclass(frozen=True, slots=True)
class Viewer:
    """Who is asking. Build it with the constructors, not by hand."""

    dm: bool = False
    # "any": what any of `characters` knows (a player's own characters, one character).
    # "all": what every one of `characters` knows (the table, the party). With none, nothing.
    mode: Literal["any", "all"] = "any"
    characters: frozenset[str] = frozenset()
    party: bool = False  # "every player character of the campaign", worked out by the loader

    @staticmethod
    def the_dm() -> Viewer:
        return Viewer(dm=True)

    @staticmethod
    def player(character_ids: Iterable[str]) -> Viewer:
        """A player: the union of their own characters (none: nothing)."""
        return Viewer(mode="any", characters=frozenset(character_ids))

    @staticmethod
    def character(character_id: str) -> Viewer:
        """One character, a player character or an NPC (the DM seeing as them)."""
        return Viewer(mode="any", characters=frozenset((character_id,)))

    @staticmethod
    def the_party() -> Viewer:
        """The DM seeing as the party: what every player character knows."""
        return Viewer(mode="all", party=True)

    @staticmethod
    def the_table(character_ids: Iterable[str]) -> Viewer:
        """Anything read to the whole table: what every one of these player characters (those at
        the session) knows."""
        return Viewer(mode="all", characters=frozenset(character_ids))


@dataclass(frozen=True, slots=True)
class Statements:
    """What the loader read: who knows each statement, and what each one names."""

    # (kind, id) -> who knows it: character or group entry ids, each with how sure they are (0.0
    # to 1.0, no row = does not know of it). A plain set of ids means everyone is sure (1.0).
    knowers: Mapping[tuple[str, str], Collection[str] | Mapping[str, float]]
    # member -> the groups it belongs to (a confirmed member_of connection): a group's row is what
    # its members commonly know, unless the member has a row of their own (#2995).
    groups: Mapping[str, frozenset[str]] = field(default_factory=dict)
    states: Mapping[str, str] = field(default_factory=dict)  # property id -> its entry
    connections: Mapping[str, tuple[str, str]] = field(default_factory=dict)  # id -> its two ends
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # name id -> its entries
    player_characters: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Visible:
    """What one viewer may know, by kind. `everything`: the DM, who is never listed."""

    entries: frozenset[str] = frozenset()  # (empty for the DM: always ask `knows`)
    properties: frozenset[str] = frozenset()
    connections: frozenset[str] = frozenset()
    aliases: frozenset[str] = frozenset()
    everything: bool = False
    # How sure the viewer is of each statement it knows (the player's own characters' highest; the
    # table's lowest, since each must know it). Not set for the DM, who knows the truth.
    sureness: Mapping[tuple[str, str], float] = field(default_factory=dict)

    def how_sure(self, kind: str, statement_id: str) -> float | None:
        """The viewer's certainty (0.0 to 1.0) in a statement, or None when the viewer does not
        know of it (the DM: None as well; the DM is never asked how sure they are)."""
        return self.sureness.get((kind, statement_id))

    def knows(self, kind: str, statement_id: str) -> bool:
        if self.everything:
            return True
        held = {
            ENTRY: self.entries,
            PROPERTY: self.properties,
            CONNECTION: self.connections,
            ALIAS: self.aliases,
        }[kind]
        return statement_id in held


def word_of(knowers: Collection[str], player_characters: Collection[str]) -> str:
    """The word for a statement known by `knowers` (character entry ids) in a campaign whose
    player characters are `player_characters` (§9b item 3)."""
    known = set(knowers)
    pcs = set(player_characters)
    if not known:
        return SECRET
    on_pcs = known & pcs
    if not on_pcs:
        return KNOWN_TO_ONE_NPC if len(known) == 1 else HIDDEN_FROM_THE_PLAYERS
    if pcs <= on_pcs:
        return KNOWN_TO_THE_PARTY
    return PARTLY_REVEALED


def _sure(listed: Collection[str] | Mapping[str, float]) -> Mapping[str, float]:
    return listed if isinstance(listed, Mapping) else dict.fromkeys(listed, 1.0)


def reach(
    character: str, listed: Mapping[str, float], groups: Mapping[str, frozenset[str]]
) -> float | None:
    """How sure one character is of a statement, or None when they do not know of it. Their own
    row wins; otherwise a group they belong to gives its row's certainty (the highest of them).
    A friend is not a member, so what Tran knows tells Clyde nothing."""
    if character in listed:
        return listed[character]
    found = [listed[g] for g in groups.get(character, ()) if g in listed]
    return max(found) if found else None


def visible(viewer: Viewer, data: Statements) -> Visible:
    """What `viewer` may know, from the lists alone."""
    if viewer.dm:
        return Visible(everything=True)
    who = data.player_characters if viewer.party else viewer.characters

    sureness: dict[tuple[str, str], float] = {}
    for key, listed in data.knowers.items():
        sure = [reach(c, _sure(listed), data.groups) for c in who]
        known = [x for x in sure if x is not None]
        if viewer.mode == "any":
            if known:
                sureness[key] = max(known)
        elif who and len(known) == len(who):  # every one of them must know it
            sureness[key] = min(known)

    def of(kind: str) -> set[str]:
        return {i for (k, i) in sureness if k == kind}

    entries = of(ENTRY)
    properties, connections, aliases = of(PROPERTY), of(CONNECTION), of(ALIAS)
    # Learning a statement is learning that the entries it names exist (§9b item 4).
    for state in properties:
        if state in data.states:
            entries.add(data.states[state])
    for connection in connections:
        entries.update(data.connections.get(connection, ()))
    for alias in aliases:
        entries.update(data.aliases.get(alias, ()))
    return Visible(
        frozenset(entries),
        frozenset(properties),
        frozenset(connections),
        frozenset(aliases),
        sureness=sureness,
    )


async def load(scope: Scope) -> Statements:
    """Read the campaign's knowledge lists and what each statement names, in one pass."""
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(PERSPECTIVES, " AND entity_id IS NOT NULL")
    }
    knowers: dict[tuple[str, str], dict[str, float]] = {}
    for row in await scope.select(KNOWERS):
        character = views.get(row["perspective_id"])
        if character is None:
            continue
        for kind, column in (
            (ENTRY, "about_entity_id"),
            (PROPERTY, "state_id"),
            (CONNECTION, "relation_id"),
            (ALIAS, "alias_id"),
        ):
            if row[column] is not None:
                knowers.setdefault((kind, row[column]), {})[character] = float(row["confidence"])
    pcs = frozenset(
        r["id"]
        for r in await scope.select(
            ENTITIES, " AND type = 'character' AND role = %s AND status = %s",
            [PLAYER_CHARACTER, CONFIRMED],
        )
    )  # fmt: skip
    wanted_states = [i for k, i in knowers if k == PROPERTY]
    wanted_relations = [i for k, i in knowers if k == CONNECTION]
    wanted_aliases = [i for k, i in knowers if k == ALIAS]
    wanted_entries = [i for k, i in knowers if k == ENTRY]
    # A rejected or merged-away statement is gone: its rows stay (history, Undo) but grant nothing.
    live = {
        r["id"]
        for r in await _by_id(
            scope, ENTITIES, wanted_entries, " AND status NOT IN ('rejected', 'merged')"
        )
    }
    states = {r["id"]: r["entity_id"] for r in await _by_id(scope, STATES, wanted_states)}
    relations = {
        r["id"]: (r["subject_id"], r["object_id"])
        for r in await _by_id(scope, RELATIONS, wanted_relations, " AND status <> 'rejected'")
    }
    aliases = {
        r["id"]: tuple(i for i in (r["entity_id"], r["used_by"]) if i is not None)
        for r in await _by_id(scope, ALIASES, wanted_aliases, " AND status <> 'rejected'")
    }
    # Groups: whoever is a member of a group that has a row has the group's certainty unless they
    # have a row of their own (#2995). A member is a confirmed `member_of` connection.
    listed = {c for who in knowers.values() for c in who}
    groups: dict[str, set[str]] = {}
    if listed:
        members = await scope.select(
            RELATIONS,
            " AND predicate = 'member_of' AND status = 'confirmed' AND object_id = ANY(%s)",
            [sorted(listed)],
        )
        for r in members:
            groups.setdefault(r["subject_id"], set()).add(r["object_id"])
    kept = {
        key: value
        for key, value in knowers.items()
        if (key[0] == ENTRY and key[1] in live)
        or (key[0] == PROPERTY and key[1] in states)
        or (key[0] == CONNECTION and key[1] in relations)
        or (key[0] == ALIAS and key[1] in aliases)
    }
    return Statements(
        knowers=kept,
        groups={m: frozenset(g) for m, g in groups.items()},
        states=states,
        connections=relations,
        aliases=aliases,
        player_characters=pcs,
    )


async def _by_id(
    scope: Scope, table: Table, ids: list[str], extra: LiteralString = ""
) -> list[dict[str, Any]]:
    """The rows of `table` with these ids (and `extra`), not the whole table."""
    if not ids:
        return []
    return await scope.select(table, " AND id = ANY(%s)" + extra, [ids])


async def visible_to(scope: Scope, viewer: Viewer) -> Visible:
    """What `viewer` may know of this campaign. The DM's answer needs no read at all."""
    if viewer.dm:
        return Visible(everything=True)
    return visible(viewer, await load(scope))
