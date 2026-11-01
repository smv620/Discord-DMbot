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

from collections.abc import Collection, Iterable, Mapping, Sequence
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
from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER, lookup_key

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


SOMEONE = "someone"  # what a public place says when no name is open to every player


def display_name(
    seen: Visible, entity_id: str, main: str, others: Sequence[tuple[str, str]]
) -> str | None:
    """The name a viewer is shown for an entry (#3037 ruling): the main name only when they know
    the entry under its own name (a row of their own for it, or the DM), otherwise the first other
    name they know (`others`: (name id, text) in the order they learned them), otherwise None, and
    the entry is not shown to them at all. Knowing a name or a connection makes the entry known,
    but never makes its main name known: a join must not tell anyone what it was meant to keep."""
    if seen.everything or (ENTRY, entity_id) in seen.sureness:
        return main
    for alias_id, text in others:
        if alias_id in seen.aliases:
            return text
    return None


def public_name(seen: Visible, entity_id: str, main: str, others: Sequence[tuple[str, str]]) -> str:
    """The name for a place that is not one character's (the table's answers, recaps): `seen` is
    what EVERY player knows (`Viewer.the_party()`), so this is the main name only if it is open to
    all, else the first other name open to all, else "someone"."""
    return display_name(seen, entity_id, main, others) or SOMEONE


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
        # (a character knows its own entry without a row, so in the party's view it counts for
        # itself, with one player character or many)
        sure = [1.0 if key == (ENTRY, c) else reach(c, _sure(listed), data.groups) for c in who]
        known = [x for x in sure if x is not None]
        if viewer.mode == "any":
            if known:
                sureness[key] = max(known)
        elif who and len(known) == len(who):  # every one of them must know it
            sureness[key] = min(known)

    # A character knows that it exists itself (§9b, self-knowledge): never written as a row.
    for c in who if viewer.mode == "any" or len(who) == 1 else ():
        sureness[(ENTRY, c)] = 1.0

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


async def party_entries(scope: Scope) -> frozenset[str] | None:
    """The entries the party's view holds as entries in their own right (`(ENTRY, id)` in
    `visible(Viewer.the_party()).sureness`), without reading every list row: the lookup asks this
    on each rebuild, and reading a campaign's whole `memory_knowers` costs hundreds of
    milliseconds (#2995). One aggregate over the entry rows of the player characters' views.
    None when there is no player character (no party to ask). Exact for lists held by characters;
    a list held by a group (a faction as a knower) is reach the aggregate does not follow, so then
    the full gate answers."""
    pcs = {
        str(r["id"]): str(r["id"])
        for r in await scope.select(
            ENTITIES, " AND type = 'character' AND role = %s AND status = %s",
            [PLAYER_CHARACTER, CONFIRMED],
        )
    }  # fmt: skip
    if not pcs:
        return None
    views = {
        str(r["entity_id"]): str(r["id"])
        for r in await scope.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [list(pcs)])
    }
    cur = await scope.conn.execute(
        "SELECT 1 FROM memory_knowers k"
        " JOIN memory_perspectives p ON p.guild_id = k.guild_id AND p.campaign_id = k.campaign_id"
        "   AND p.id = k.perspective_id"
        " JOIN memory_entities e ON e.guild_id = p.guild_id AND e.campaign_id = p.campaign_id"
        "   AND e.id = p.entity_id"
        " WHERE k.guild_id = %s AND k.campaign_id = %s AND k.about_entity_id IS NOT NULL"
        " AND cardinality(k.hidden_by) = 0 AND e.type <> 'character' LIMIT 1",
        scope.ids,
    )
    if await cur.fetchone() is not None:
        return None  # (a group holds an entry: let the full gate follow its members)
    cur = await scope.conn.execute(
        "SELECT about_entity_id, array_agg(DISTINCT perspective_id) AS holders"
        " FROM memory_knowers WHERE guild_id = %s AND campaign_id = %s"
        " AND about_entity_id IS NOT NULL AND cardinality(hidden_by) = 0"
        " AND perspective_id = ANY(%s) GROUP BY about_entity_id",
        (*scope.ids, list(views.values())),
    )
    everyone = set(views.values())
    own = {entity: view for entity, view in views.items()}
    seen = set()
    for row in await cur.fetchall():
        entry = str(row["about_entity_id"])
        missing = everyone - set(row["holders"])
        # (a character knows its own entry without a row)
        if not missing or missing == {own.get(entry)}:
            seen.add(entry)
    if len(everyone) == 1:  # a lone player character knows its own entry whatever the lists say
        seen.update(views)
    return frozenset(seen)


async def visible_to(scope: Scope, viewer: Viewer) -> Visible:
    """What `viewer` may know of this campaign. The DM's answer needs no read at all."""
    if viewer.dm:
        return Visible(everything=True)
    return visible(viewer, await load(scope))


def _public_entry(row: Mapping[str, Any], own_name_secret: bool) -> bool:
    """An entry the players are shown by its main name: not hidden from them, not limited to some,
    not an unnamed person, and its main name is not a secret one (#3037 ruling: "a confirmed,
    non-secret main name" is a name every player knows)."""
    return (
        row["status"] == CONFIRMED
        and row["merged_into"] is None
        and not (
            row["hidden_from_players"]
            or row["audience_limited"]
            or row["unnamed"]
            or own_name_secret
        )
    )


def _unnamed_label(row: Mapping[str, Any], anchor_name: str | None) -> str:
    """An unnamed person's label as THIS viewer may read it: it hangs off an entry the viewer may
    name, else "someone's sister (name unknown)". The stored name embeds the anchor's name as it
    was when the person was made, which a hidden anchor must never leak."""
    from dmbot.memory import unnamed

    if row["unnamed_anchor"] is None:  # a hooded figure hangs off nobody: nothing to hide
        return unnamed.label(str(row["unnamed_what"] or ""), None)
    return unnamed.label(str(row["unnamed_what"] or ""), anchor_name or "someone")


@dataclass(frozen=True, slots=True)
class NameRows:
    """The rows `name_entries` reads, whoever reads them: the bot from its scope, the website from
    one database door. `entities` holds the entries asked for, what an unnamed one hangs off, and
    the rows joined into them (any status); `aliases` every other name of those entries and the
    secret names of what unnamed people hang off; `learned` each (name, character, when) a
    character learned an other name (rows put to sleep left out)."""

    entities: Mapping[str, Mapping[str, Any]]
    aliases: Sequence[Mapping[str, Any]]
    learned: Sequence[tuple[str, str, int]] = ()


async def names_for(
    scope: Scope,
    viewer: Viewer,
    entity_ids: Sequence[str],
    *,
    public: bool = False,
    texts_out: dict[str, str] | None = None,
) -> dict[str, str | None]:
    """`display_name` for each of these entries, read in the caller's scope (so a reader that
    already holds a snapshot names its entries from the same one). The other names are taken in
    the order the viewer's own characters learned them. An entry the players are shown by name is
    named by its main name; an unnamed person is named by a label built from what the viewer may
    see. A name belongs to the fact: a character that knows an entry only through a secret other
    name (no row of its own for the entry) is shown that name even when the entry's main name is
    public. `public`: an entry nobody may name comes back as "someone" rather than None.
    `texts_out`, when given, is filled with every other name of these entries as this viewer may
    read it (alias id -> text), so a reader never sends the stored text of a joined label."""
    seen = await visible_to(scope, viewer)
    entries = {
        str(r["id"]): r
        for r in await scope.select(
            ENTITIES,
            " AND id = ANY(%s) AND status NOT IN ('merged', 'rejected')",
            [list(entity_ids)],
        )
    }
    names = await scope.select(
        ALIASES,
        " AND entity_id = ANY(%s) AND status <> 'rejected' ORDER BY created_at, id",
        [list(entries)],
    )
    anchors = {
        str(r["unnamed_anchor"])
        for r in entries.values()
        if r["unnamed"] and r["unnamed_anchor"] is not None
    }
    anchor_rows = (
        {
            str(r["id"]): r
            for r in await scope.select(ENTITIES, " AND id = ANY(%s)", [sorted(anchors)])
        }
        if anchors
        else {}
    )
    anchor_names = (
        await scope.select(
            ALIASES,
            " AND entity_id = ANY(%s) AND secret AND status <> 'rejected'",
            [sorted(anchors)],
        )
        if anchor_rows
        else []
    )
    # a person joined to a named entry leaves their label as an other name: the merged-away row
    # still says what it described and who it hung off
    merged = await scope.select(
        ENTITIES,
        " AND merged_into = ANY(%s) AND unnamed",
        [list(entries)],
    )
    learned: list[tuple[str, str, int]] = []
    if not seen.everything and names:
        mine = await scope.select(
            KNOWERS, " AND alias_id = ANY(%s)", [[str(a["id"]) for a in names]]
        )
        perspective = {
            str(p["id"]): str(p["entity_id"])
            for p in await scope.select(PERSPECTIVES, " AND entity_id IS NOT NULL")
        }
        learned = [
            (str(k["alias_id"]), perspective[str(k["perspective_id"])], int(k["created_at"]))
            for k in mine
            if str(k["perspective_id"]) in perspective
        ]
    rows = NameRows(
        {**{str(r["id"]): r for r in merged}, **anchor_rows, **entries},
        [*names, *anchor_names],
        learned,
    )
    return name_entries(seen, viewer, entity_ids, rows, public=public, texts_out=texts_out)


@dataclass(frozen=True, slots=True)
class Split:
    """The facts a viewer learned through a secret other name, which the website draws on a node
    of that name ("alias:<id>") beside the person's own node (#3085: a name belongs to the fact)."""

    links: Mapping[str, str]  # connection id -> the other name it was learned through
    nodes: Mapping[str, tuple[str, str]]  # other name id -> (the entry it names, its text)


def drawn_connections(
    seen: Visible, links: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The known connections that are drawn at all. A name belongs to the fact (#3037): a
    connection that came through another name is drawn on that name, but if that name was
    rejected or is gone, or is a secret one the viewer does not hold, it is not shown (it must not
    fall back onto the person's own name). `links` carry `via`; `aliases` the other names (`id`,
    `secret`, `status`), rejected ones left out."""
    by_id = {str(a["id"]): a for a in aliases}
    out = []
    for link in links:
        via = link["via"]
        if via is None:
            out.append(link)
            continue
        a = by_id.get(str(via))
        if (
            a is not None
            and a["status"] == CONFIRMED
            and (not a["secret"] or seen.knows(ALIAS, str(via)))
        ):
            out.append(link)
    return out


def split_connections(
    seen: Visible,
    links: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    texts: Mapping[str, str],
) -> Split:
    """Which known connections belong on a node of the secret other name they were learned
    through. `links` are the live confirmed connections (`id`, `via`: the other name it was
    learned through or None, `secret`, `unrevealed`); `aliases` the other names (`id`, `entity_id`,
    `secret`, `status`); `texts` each other name as this viewer may read it (`name_entries`).

    A connection is split when the viewer knows it, it was learned through a confirmed secret
    other name the viewer holds, and it is itself held back (secret or not yet said at the table).
    Once the other name is made public the two fold back into one person: nothing is split. A node
    exists only for a name that has such a connection."""
    by_id = {str(a["id"]): a for a in aliases}
    held = {
        i
        for i, a in by_id.items()
        if a["secret"] and a["status"] == CONFIRMED and seen.knows(ALIAS, i)
    }
    chosen: dict[str, str] = {}
    for link in links:
        via = link["via"]
        # (no text for the name: it is not one this viewer may read, so nothing is split onto it)
        if via is None or str(via) not in held or str(via) not in texts:
            continue
        if (link["secret"] or link["unrevealed"]) and seen.knows(CONNECTION, str(link["id"])):
            chosen[str(link["id"])] = str(via)
    nodes = {a: (str(by_id[a]["entity_id"]), texts[a]) for a in set(chosen.values())}
    return Split(chosen, nodes)


def name_entries(
    seen: Visible,
    viewer: Viewer,
    entity_ids: Sequence[str],
    rows: NameRows,
    *,
    public: bool = False,
    texts_out: dict[str, str] | None = None,
    on_their_own: Collection[str] = (),
) -> dict[str, str | None]:
    """`names_for` without the reading: the same rules over rows already read, so the website
    names an entry exactly as Discord does. `on_their_own`: other names that facts were learned
    through, which the website draws on a node of their own (`split_connections`); such a name is
    never also the name of the person's own node, or the one person would be drawn twice."""
    entries = {
        k: r
        for k, r in rows.entities.items()
        if k in set(entity_ids) and r["status"] not in ("merged", "rejected")
    }
    names = [a for a in rows.aliases if str(a["entity_id"]) in entries]
    anchors = {
        str(r["unnamed_anchor"])
        for r in entries.values()
        if r["unnamed"] and r["unnamed_anchor"] is not None
    }
    anchor_rows = {k: r for k, r in rows.entities.items() if k in anchors}
    secret_own_names = [
        a
        for a in rows.aliases
        if str(a["entity_id"]) in anchor_rows
        and a["secret"]
        and a["key"] == lookup_key(str(anchor_rows[str(a["entity_id"])]["name"]))
    ]
    anchor_secret = {str(a["entity_id"]) for a in secret_own_names}
    anchor_held = {
        str(a["entity_id"]) for a in secret_own_names if str(a["id"]) in seen.aliases
    }  # (a row on the secret name itself is what makes it known)
    merged = [
        r
        for r in rows.entities.values()
        if r["merged_into"] is not None and str(r["merged_into"]) in entries and r["unnamed"]
    ]
    learned: dict[str, int] = {}
    if not seen.everything and names:
        listed = {str(a["id"]) for a in names}
        for alias_id, character, at in rows.learned:
            # the order THIS viewer learned them in: only its own characters' rows count
            if alias_id not in listed or (viewer.characters and character not in viewer.characters):
                continue
            learned[alias_id] = min(learned.get(alias_id, at), at)

    def anchor_name(row: Mapping[str, Any]) -> str | None:
        """The name of what an unnamed person hangs off, if THIS viewer may read it."""
        anchor = anchor_rows.get(str(row["unnamed_anchor"])) if row["unnamed_anchor"] else None
        if (
            anchor is None
            or anchor["unnamed"]
            or anchor["status"] != CONFIRMED
            or anchor["merged_into"] is not None
        ):
            return None
        known = seen.everything or (
            (ENTRY, str(anchor["id"])) in seen.sureness
            and (str(anchor["id"]) not in anchor_secret or str(anchor["id"]) in anchor_held)
        )
        if known or _public_entry(anchor, str(anchor["id"]) in anchor_secret):
            return str(anchor["name"])
        return None

    def safe_text(entity_id: str, text: str) -> str:
        """An other name as this viewer may read it: a joined person's label is rebuilt."""
        if not text.endswith(" (name unknown)") or seen.everything:
            return text
        for gone in merged:
            if str(gone["merged_into"]) == entity_id and str(gone["name"]) == text:
                return _unnamed_label(gone, anchor_name(gone))
        return "someone (name unknown)"

    out: dict[str, str | None] = {}
    for entity_id in entity_ids:
        row = entries.get(entity_id)
        if row is None:
            out[entity_id] = None
            continue
        main = str(row["name"])
        # (an entry's own name is stored as a name too: it stands for the main name, which the
        # entry's own row governs, so it is not one of the "other" names)
        others = sorted(
            (
                (str(a["id"]), safe_text(entity_id, str(a["text"])))
                for a in names
                if str(a["entity_id"]) == entity_id
                and a["key"] != lookup_key(main)
                and str(a["id"]) not in on_their_own
            ),
            key=lambda pair: (learned.get(pair[0], 0), pair[0]),
        )
        own_name = [
            str(a["id"])
            for a in names
            if str(a["entity_id"]) == entity_id and a["key"] == lookup_key(main)
        ]
        own_name_secret = any(
            a["secret"]
            for a in names
            if str(a["entity_id"]) == entity_id and a["key"] == lookup_key(main)
        )
        if row["unnamed"] and not seen.everything:
            main = _unnamed_label(row, anchor_name(row))
        if any(alias in seen.aliases for alias in own_name):
            shown: str | None = main
        elif own_name_secret and not seen.everything:
            # (an entry row does not make a secret name known: only a row on that name does)
            shown = next((text for alias_id, text in others if alias_id in seen.aliases), None)
        else:
            shown = display_name(seen, entity_id, main, others)
            if shown is None and _public_entry(row, own_name_secret):
                shown = main  # (a name every player may read, for a viewer who knows no other)
        out[entity_id] = SOMEONE if shown is None and public else shown
        if texts_out is not None:
            for a in names:
                if str(a["entity_id"]) == entity_id:
                    texts_out[str(a["id"])] = safe_text(entity_id, str(a["text"]))
    return out
