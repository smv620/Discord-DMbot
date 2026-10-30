"""Who knows a hidden connection or an other name (#2935): the middle level between "only the DM"
and "everyone at the table".

A connection known by some characters stays secret (or not yet revealed) for The party, so every
public reader, which asks only The party, keeps hiding it with no change; each knowing character's
own point of view (`memory_perspectives`, kind `pc`) holds a 1.0 belief in it. An other name known
by some characters stays a secret spelling and has one row per character in
`memory_alias_knowers`. Only the DM writes either (CLAUDE.md, DM authority), and only for player
characters. Readers that show this (the DM's views, and one player's own view of what their
character knows) are separate and ask `characters_of` for who a viewer plays.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import beliefs
from dmbot.memory._changes import (
    ALIAS_KNOWERS,
    ALIASES,
    BELIEFS,
    ENTITIES,
    KNOWERS,
    PERSPECTIVES,
    RELATIONS,
    STATES,
    Changes,
    Scope,
)
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    KNOWN,
    NPC_VIEW,
    PC_VIEW,
    PLAYER_CHARACTER,
    REVEALED_FACT,
    SECRET_PLOT,
    MemoryRuleError,
    new_id,
)

ONLY_THE_DM = "Only the DM can say who knows this."
NOT_HIDDEN = "Everyone at the table already knows this one."
NOT_A_PLAYER_CHARACTER = "Pick player characters from this campaign."
NOT_SECRET = "Only a secret name can be known by some characters."
MAX_KNOWERS = 12  # a table's characters; more than this is a mistake, not a party
MAX_KNOWERS_ANY = 60  # the characters of a campaign, NPCs included (a whole faction told at once)


async def _characters(w: Scope, character_ids: Sequence[str]) -> list[str]:
    """The confirmed player characters among these ids, in order and without repeats; anything
    else (another campaign's entry, an NPC, a name not yet confirmed) is refused, so nothing
    outside this campaign's player characters can hold a point of view."""
    chosen = list(dict.fromkeys(character_ids))
    if len(chosen) > MAX_KNOWERS:
        raise MemoryRuleError(NOT_A_PLAYER_CHARACTER)
    if not chosen:
        return []
    found = {
        r["id"]
        for r in await w.select(
            ENTITIES,
            " AND id = ANY(%s) AND type = 'character' AND role = %s AND status = %s",
            [chosen, PLAYER_CHARACTER, CONFIRMED],
        )
    }
    if len(found) != len(chosen):
        raise MemoryRuleError(NOT_A_PLAYER_CHARACTER)
    return chosen


async def _view_of(w: Changes, entity_id: str) -> str:
    """A character's point of view, made the first time it is needed."""
    have = await w.select(PERSPECTIVES, " AND entity_id = %s", [entity_id])
    if have:
        if have[0]["kind"] != PC_VIEW:  # made as an NPC's before the entry was a player's
            await w.update(PERSPECTIVES, have[0]["id"], {"kind": PC_VIEW})
        return str(have[0]["id"])
    made = new_id()
    await w.insert(
        PERSPECTIVES, {"id": made, "kind": PC_VIEW, "entity_id": entity_id, "created_at": w.now}
    )
    return made


NOT_A_CHARACTER = "Pick characters from this campaign."
NO_SUCH_STATEMENT = "That is gone or was never here."
BAD_CERTAINTY = "How sure someone is goes from 0 to 1."
_COLUMNS = {
    "entry": ("about_entity_id", ENTITIES),
    "property": ("state_id", STATES),
    "connection": ("relation_id", RELATIONS),
    "alias": ("alias_id", ALIASES),
}


async def _known_characters(w: Scope, character_ids: Sequence[str]) -> dict[str, bool]:
    """The confirmed characters among these ids, player characters and NPCs, each with whether it
    is a player's (§9b: a point of view for each character). Anything else (another campaign's
    entry, a place, a name not yet confirmed) is refused."""
    chosen = list(dict.fromkeys(character_ids))
    if len(chosen) > MAX_KNOWERS_ANY:
        raise MemoryRuleError(NOT_A_CHARACTER)
    if not chosen:
        return {}
    found = {
        r["id"]: r["role"] == PLAYER_CHARACTER
        for r in await w.select(
            ENTITIES,
            " AND id = ANY(%s) AND type IN ('character', 'faction') AND status = %s",
            [chosen, CONFIRMED],
        )
    }
    if len(found) != len(chosen):
        raise MemoryRuleError(NOT_A_CHARACTER)
    return {i: found[i] for i in chosen}


async def _view_for(w: Changes, entity_id: str, player: bool) -> str:
    """A character's point of view, made the first time it is needed (a player's is `pc`)."""
    if player:
        return await _view_of(w, entity_id)
    have = await w.select(PERSPECTIVES, " AND entity_id = %s", [entity_id])
    if have:
        return str(have[0]["id"])
    made = new_id()
    await w.insert(
        PERSPECTIVES, {"id": made, "kind": NPC_VIEW, "entity_id": entity_id, "created_at": w.now}
    )
    return made


async def set_statement_knowers(
    w: Changes, kind: str, statement_id: str, character_ids: Sequence[str] | Mapping[str, float]
) -> list[str]:
    """Exactly these characters know this statement (none: only the DM) (#2995, §9b). `kind` is
    entry, property, connection or alias. A list means each is sure (1.0); a mapping gives each
    one's certainty, 0.0 (knows of it, believes it false) to 1.0. Only the DM; the statement must
    exist and not be rejected; each knower must be a confirmed character or group of this
    campaign (a group's row is what its members commonly know, #2995: the gate gives a member the
    group's certainty unless the member has a row of their own). Returns who now knows."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    if kind not in _COLUMNS:
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    column, table = _COLUMNS[kind]
    statement = await w.get(table, statement_id)
    if statement is None or statement.get("status") in ("rejected", "merged"):
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    sure = (
        dict(character_ids)
        if isinstance(character_ids, Mapping)
        else dict.fromkeys(character_ids, 1.0)
    )
    if any(not 0.0 <= c <= 1.0 for c in sure.values()):
        raise MemoryRuleError(BAD_CERTAINTY)
    chosen = await _known_characters(w, list(sure))
    views = {i: await _view_for(w, i, player) for i, player in chosen.items()}
    have = {
        r["perspective_id"]: r
        for r in await w.select(KNOWERS, f" AND {column} = %s", [statement_id])
    }
    for entity_id, view_id in views.items():
        if view_id not in have:
            await w.insert(
                KNOWERS,
                {"id": new_id(), "perspective_id": view_id, "about_entity_id": None,
                 "state_id": None, "relation_id": None, "alias_id": None,
                 column: statement_id, "created_at": w.now, "confidence": sure[entity_id]},
            )  # fmt: skip
        elif have[view_id]["confidence"] != sure[entity_id]:
            await w.update(KNOWERS, have[view_id]["id"], {"confidence": sure[entity_id]})
    for view_id, row in have.items():
        if view_id not in views.values():
            await w.delete(KNOWERS, row["id"])
    return list(chosen)


async def statement_knowers(
    scope: Scope, kind: str, statement_ids: Sequence[str]
) -> dict[str, list[str]]:
    """For each of these statements, the characters who know it (their entry ids)."""
    out: dict[str, list[str]] = {}
    if not statement_ids:
        return out
    column, _ = _COLUMNS[kind]
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(PERSPECTIVES, " AND entity_id IS NOT NULL")
    }
    for row in await scope.select(KNOWERS, f" AND {column} = ANY(%s)", [list(statement_ids)]):
        if row["perspective_id"] in views:
            out.setdefault(row[column], []).append(views[row["perspective_id"]])
    return out


async def set_connection_knowers(
    w: Changes, relation_id: str, character_ids: Sequence[str]
) -> list[str]:
    """Exactly these characters know this hidden connection (none: only the DM). A character not
    listed who knew it stops knowing (a 0.0 belief, so the history stays). The connection must be
    confirmed and still held back (secret, or not revealed yet): one everyone knows has nothing
    to choose. Returns the characters now knowing."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    relation = await w.get(RELATIONS, relation_id)
    if relation is None or relation["status"] != CONFIRMED:
        raise MemoryRuleError(NOT_HIDDEN)
    if not (relation["secret"] or relation["unrevealed"]):
        raise MemoryRuleError(NOT_HIDDEN)
    chosen = await _characters(w, character_ids)
    now = await connection_knowers(w, [relation_id])
    current = set(now.get(relation_id, []))
    for entity_id in chosen:
        if entity_id not in current:
            await beliefs.add_belief(
                w, await _view_of(w, entity_id), relation_id, KNOWN, REVEALED_FACT
            )
    for entity_id in current - set(chosen):
        await beliefs.add_belief(w, await _view_of(w, entity_id), relation_id, 0.0, SECRET_PLOT)
    # The same list in the one table the gate reads (#2995, slice 2b); the old beliefs stay what
    # the old readers read until slice 3.
    await _follow_player_list(w, "connection", relation_id, chosen)
    return chosen


async def set_alias_knowers(w: Changes, alias_id: str, character_ids: Sequence[str]) -> list[str]:
    """Exactly these characters know this secret other name (none: only the DM). Returns the
    characters now knowing."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    alias = await w.get(ALIASES, alias_id)
    if alias is None or alias["status"] == "rejected" or not alias["secret"]:
        raise MemoryRuleError(NOT_SECRET)
    chosen = await _characters(w, character_ids)
    views = {entity_id: await _view_of(w, entity_id) for entity_id in chosen}
    have = {
        r["perspective_id"]: r["id"]
        for r in await w.select(ALIAS_KNOWERS, " AND alias_id = %s", [alias_id])
    }
    for view_id in views.values():
        if view_id not in have:
            await w.insert(
                ALIAS_KNOWERS,
                {"id": new_id(), "alias_id": alias_id, "perspective_id": view_id,
                 "created_at": w.now},
            )  # fmt: skip
    for view_id, row_id in have.items():
        if view_id not in views.values():
            await w.delete(ALIAS_KNOWERS, row_id)
    await _follow_player_list(w, "alias", alias_id, chosen)
    return chosen


async def _follow_player_list(
    w: Changes, kind: str, statement_id: str, chosen: Sequence[str]
) -> None:
    """The old "Who knows this?" writers pick player characters only: make the player characters
    in the new list exactly those, keeping every other row (an NPC's, a group's) and the
    certainty of a player character who stays (#2995, slice 2b)."""
    column, _ = _COLUMNS[kind]
    views = {i: await _view_of(w, i) for i in chosen}
    pcs = {str(r["id"]) for r in await w.select(PERSPECTIVES, " AND kind = %s", [PC_VIEW])}
    have = {
        r["perspective_id"]: r
        for r in await w.select(KNOWERS, f" AND {column} = %s", [statement_id])
    }
    for view_id in views.values():
        if view_id not in have:
            await w.insert(KNOWERS, _row(column, statement_id, view_id, w.now))
    for view_id, row in have.items():
        if view_id in pcs and view_id not in views.values():
            await w.delete(KNOWERS, row["id"])


async def connection_knowers(scope: Scope, relation_ids: Sequence[str]) -> dict[str, list[str]]:
    """For each of these connections, the player characters whose own point of view holds it at
    1.0 now (their entry ids). The DM's view and a player's own view read this; no public reader
    does."""
    out: dict[str, list[str]] = {}
    if not relation_ids:
        return out
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(PERSPECTIVES, " AND kind = %s", [PC_VIEW])
    }
    if not views:
        return out
    held_back = {
        r["id"]
        for r in await scope.select(
            RELATIONS,
            " AND id = ANY(%s) AND status = %s AND (secret OR unrevealed)",
            [list(relation_ids), CONFIRMED],
        )
    }  # (a connection everyone now knows, or one turned down, has no knowers: it is public)
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in await scope.select(
        BELIEFS,
        " AND relation_id = ANY(%s) AND perspective_id = ANY(%s) ORDER BY seq",
        [list(held_back), list(views)],
    ):
        newest[(row["relation_id"], row["perspective_id"])] = row
    for (relation_id, view_id), row in newest.items():
        if row["confidence"] >= KNOWN:
            out.setdefault(relation_id, []).append(views[view_id])
    return {k: sorted(v) for k, v in out.items()}


async def alias_knowers(scope: Scope, alias_ids: Sequence[str]) -> dict[str, list[str]]:
    """For each of these other names, the player characters who know it (their entry ids)."""
    out: dict[str, list[str]] = {}
    if not alias_ids:
        return out
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(PERSPECTIVES, " AND kind = %s", [PC_VIEW])
    }
    secret = {
        r["id"]
        for r in await scope.select(
            ALIASES, " AND id = ANY(%s) AND secret AND status <> 'rejected'", [list(alias_ids)]
        )
    }  # (a name made public, or turned down, is known to everyone or nobody: no knowers)
    for row in await scope.select(ALIAS_KNOWERS, " AND alias_id = ANY(%s)", [list(alias_ids)]):
        if row["perspective_id"] in views and row["alias_id"] in secret:
            out.setdefault(row["alias_id"], []).append(views[row["perspective_id"]])
    return {k: sorted(v) for k, v in out.items()}


async def characters_of(scope: Scope, user_id: int) -> list[str]:
    """The confirmed player characters this person plays (their entry ids): what a player's own
    view of "what my character knows" is built from. Nobody else's."""
    rows = await scope.select(
        ENTITIES,
        " AND type = 'character' AND role = %s AND status = %s AND played_by = %s ORDER BY id",
        [PLAYER_CHARACTER, CONFIRMED, user_id],
    )
    return [str(r["id"]) for r in rows]


@dataclass(frozen=True, slots=True)
class MineConnection:
    """A hidden connection one of this player's characters knows: the two names and the words
    between them as stored (the display layer turns them into the card's words), and the other
    characters who know it, for "You and Mara know this"."""

    relation_id: str
    subject: str
    predicate: str
    detail: str
    object: str
    directional: bool
    also: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MineName:
    """A secret other name one of this player's characters knows, whose it is, who else knows."""

    alias_id: str
    text: str
    of: str
    also: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Mine:
    """What a player's own characters know that the table does not (#2935): their view and nobody
    else's. Empty for someone who plays no character here."""

    characters: tuple[str, ...] = ()
    connections: tuple[MineConnection, ...] = ()
    names: tuple[MineName, ...] = ()


async def what_my_characters_know(scope: Scope, user_id: int) -> Mine:
    """The hidden connections and secret other names that the characters this person plays know,
    and nothing else. Started from this person's own characters' points of view, so it reads
    nothing of anyone else's. Only confirmed items show: a connection or name everyone now knows
    is public (not here), one the DM took back is gone, one still only proposed, rejected or ended
    is not shown, and neither is one whose names are not confirmed entries. Another campaign's
    never shows. `user_id` must be the signed-in person's own (never a command option or a request
    parameter): this trusts it. The other characters who know an item are named by their entry so
    the page can say "You and Mara's player"; only confirmed player characters are named."""
    mine = await characters_of(scope, user_id)
    if not mine:
        return Mine()
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(
            PERSPECTIVES, " AND kind = %s AND entity_id = ANY(%s)", [PC_VIEW, mine]
        )
    }
    knows: set[str] = set()
    if views:
        newest: dict[tuple[str, str], float] = {}
        for row in await scope.select(
            BELIEFS, " AND perspective_id = ANY(%s) ORDER BY seq", [list(views)]
        ):
            newest[(row["relation_id"], row["perspective_id"])] = row["confidence"]
        knows = {rel for (rel, _), c in newest.items() if c >= KNOWN}
    hidden = (
        await scope.select(
            RELATIONS,
            " AND id = ANY(%s) AND status = %s AND (secret OR unrevealed)"
            " AND to_session_at IS NULL ORDER BY id",
            [sorted(knows), CONFIRMED],
        )
        if knows
        else []
    )
    alias_ids = (
        sorted(
            {
                r["alias_id"]
                for r in await scope.select(
                    ALIAS_KNOWERS, " AND perspective_id = ANY(%s)", [list(views)]
                )
            }
        )
        if views
        else []
    )
    aliases = (
        await scope.select(
            ALIASES,
            " AND id = ANY(%s) AND secret AND status = %s ORDER BY id",
            [alias_ids, CONFIRMED],
        )
        if alias_ids
        else []
    )
    who = await connection_knowers(scope, [r["id"] for r in hidden])
    a_who = await alias_knowers(scope, [a["id"] for a in aliases])
    wanted = {*mine}
    for r in hidden:
        wanted |= {r["subject_id"], r["object_id"], *who.get(r["id"], [])}
    for a in aliases:
        wanted |= {a["entity_id"], *a_who.get(a["id"], [])}
    entries = {
        r["id"]: r
        for r in await scope.select(ENTITIES, " AND id = ANY(%s)", [sorted(wanted)])
        if r["status"] == CONFIRMED
    }
    mine_set = set(mine)

    def others(knowing: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                str(entries[c]["name"])
                for c in knowing
                if c not in mine_set and c in entries and entries[c]["role"] == PLAYER_CHARACTER
            )
        )

    def name(entity_id: str) -> str | None:
        found = entries.get(entity_id)
        return None if found is None else str(found["name"])

    connections = []
    for r in hidden:
        subject, target = name(r["subject_id"]), name(r["object_id"])
        if subject is None or target is None:
            continue
        connections.append(
            MineConnection(
                r["id"], subject, r["predicate"], r["detail"] or "", target,
                bool(r.get("directional")), others(who.get(r["id"], [])),
            )
        )  # fmt: skip
    names = []
    for a in aliases:
        owner = name(a["entity_id"])
        if owner is not None:
            names.append(MineName(a["id"], a["text"], owner, others(a_who.get(a["id"], []))))
    return Mine(
        characters=tuple(sorted(str(entries[c]["name"]) for c in mine if c in entries)),
        connections=tuple(connections),
        names=tuple(names),
    )


async def check_characters(scope: Scope, character_ids: Sequence[str]) -> None:
    """Refuse, before anything is written, a pick that `set_connection_knowers` would refuse: too
    many, or anything but this campaign's confirmed player characters. A multi-step edit calls it
    first so a bad pick cannot leave it half done."""
    await _characters(scope, character_ids)


async def fold_knowers(
    w: Changes, keep_id: str, gone_id: str, views: tuple[str, str] | None
) -> None:
    """Two entries merge (#2995): whoever knew the merged-away entry exists now knows the kept
    one does, and the merged-away character's knowledge becomes the kept character's (`views` is
    its point of view and the kept one's, from `beliefs.fold_perspective`). A statement both knew
    keeps one row. One read, and nothing more when no list is touched."""
    gone_view, keep_view = views if views is not None else ("", "")
    rows = await w.select(
        KNOWERS,
        " AND (about_entity_id = ANY(%s) OR perspective_id = ANY(%s))",
        [[keep_id, gone_id], [gone_view, keep_view]],
    )
    columns = ("about_entity_id", "state_id", "relation_id", "alias_id")

    def after(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "perspective_id": keep_view
            if gone_view and row["perspective_id"] == gone_view
            else row["perspective_id"],
            "about_entity_id": keep_id
            if row["about_entity_id"] == gone_id
            else row["about_entity_id"],
        }

    def key(row: dict[str, Any], moved: dict[str, Any]) -> tuple[str, str, str]:
        column = next(c for c in columns if moved.get(c, row[c]) is not None)
        return column, str(moved.get(column, row[column])), str(moved["perspective_id"])

    seen: set[tuple[str, str, str]] = set()
    changing: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        moved = after(row)
        if moved["perspective_id"] == row["perspective_id"] and (
            moved["about_entity_id"] == row["about_entity_id"]
        ):
            seen.add(key(row, moved))
        else:
            changing.append((row, moved))
    for row, moved in changing:
        k = key(row, moved)
        if k in seen:
            await w.delete(KNOWERS, row["id"])
        else:
            seen.add(k)
            await w.update(KNOWERS, row["id"], moved)


# ---- the old flags keep the lists in step (slice 2b) --------------------------------------------


def entry_shown(row: dict[str, Any]) -> bool:
    """An entry the players are shown: confirmed, not hidden from them, not a limited clue and not
    an unnamed person (the old readers' rule, and the move's)."""
    return bool(
        row["status"] == CONFIRMED
        and not (row["hidden_from_players"] or row["audience_limited"] or row["unnamed"])
    )


def relation_shown(row: dict[str, Any]) -> bool:
    """A connection the players are shown: confirmed and neither secret nor not yet revealed."""
    return bool(row["status"] == CONFIRMED and not (row["secret"] or row["unrevealed"]))


def alias_shown(row: dict[str, Any]) -> bool:
    """An other name the players are shown: not rejected and not secret."""
    return bool(row["status"] != "rejected" and not row["secret"])


_KNOWN_COLUMN_OF = {
    "memory_entities": "about_entity_id",
    "memory_states": "state_id",
    "memory_relations": "relation_id",
    "memory_aliases": "alias_id",
}


async def _player_views(w: Changes) -> list[str]:
    """The player characters' points of view, made for a player's character that has none. Cached
    for the write and dropped when a character entry is written (`Changes._knowers_follow`). A
    player character added later knows nothing that was shown before it (Supervisor's answer on
    #2995: it learns from then on; the DM adds older knowledge with Who knows this)."""
    cached = w.player_views
    if cached is None:
        pcs = await w.select(
            ENTITIES,
            " AND type = 'character' AND role = %s AND status = %s",
            [PLAYER_CHARACTER, CONFIRMED],
        )
        cached = [await _view_of(w, str(e["id"])) for e in pcs]
        w.player_views = cached
    return cached


def _row(column: str, statement_id: str, view_id: str, now: int) -> dict[str, Any]:
    return {
        "id": new_id(), "perspective_id": view_id, "about_entity_id": None, "state_id": None,
        "relation_id": None, "alias_id": None, column: statement_id, "created_at": now,
        "confidence": 1.0,
    }  # fmt: skip


async def _entities_by_id(w: Scope, ids: Collection[str]) -> dict[str, dict[str, Any]]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    return {r["id"]: r for r in await w.select(ENTITIES, " AND id = ANY(%s)", [wanted])}


async def _open(w: Changes, column: str, items: Sequence[tuple[str, bool]]) -> None:
    """Make these statements known to every player character (`items`: id, is it new). A statement
    that existed may already have rows (the DM's own list): those stay."""
    views = await _player_views(w)
    if not views or not items:
        return
    have: set[tuple[str, str]] = set()
    old = [i for i, new in items if not new]
    if old:
        for r in await w.select(KNOWERS, f" AND {column} = ANY(%s)", [old]):
            have.add((r[column], r["perspective_id"]))
    made = [_row(column, i, v, w.now) for i, _ in items for v in views if (i, v) not in have]
    if all(new for _, new in items):
        await w.insert_unlogged(KNOWERS, made)  # new statements: gone with them on Undo
    else:
        await w.insert_many(KNOWERS, made)


async def follow_flags(
    w: Changes, table_name: str, rows: Sequence[tuple[dict[str, Any] | None, dict[str, Any]]]
) -> None:
    """Statements were written (`before` None: new): a statement that is shown to the players now
    and was not before is known to every player character, one that stops being shown is known to
    nobody (§9b item 9). The old flags are what the players see until the readers go through the
    gate, so the list follows them: an entry by its own flags, a name or connection by its own
    flags and its entries' (a name of a hidden entry is hidden with it, a connection with a hidden
    end likewise), a condition with its entry. Only the transition counts: a statement the DM gave
    its own list (`set_statement_knowers`) keeps it while its flags do not change."""
    column = _KNOWN_COLUMN_OF[table_name]
    opened: list[tuple[str, bool]] = []  # (statement id, is it new)
    closed: list[str] = []
    if table_name == "memory_entities":
        for before, after in rows:
            now, was = entry_shown(after), before is not None and entry_shown(before)
            if now and not was:
                opened.append((after["id"], before is None))
            elif was and not now:
                closed.append(after["id"])
        await _follow_dependents(w, [i for i, new in opened if not new], closed)
    elif table_name == "memory_states":  # a condition is shown with its entry, once, when made
        fresh = [after for before, after in rows if before is None]
        owners = await _entities_by_id(w, {a["entity_id"] for a in fresh})
        opened = [
            (a["id"], True) for a in fresh
            if a["entity_id"] in owners and entry_shown(owners[a["entity_id"]])
        ]  # fmt: skip
    elif table_name == "memory_aliases":
        owners = await _entities_by_id(w, {a["entity_id"] for _, a in rows})
        for before, after in rows:
            ok = after["entity_id"] in owners and entry_shown(owners[after["entity_id"]])
            now, was = ok and alias_shown(after), ok and before is not None and alias_shown(before)
            if now and not was:
                opened.append((after["id"], before is None))
            elif was and not now:
                closed.append(after["id"])
    else:
        ends = await _entities_by_id(
            w, {x for _, a in rows for x in (a["subject_id"], a["object_id"])}
        )
        for before, after in rows:
            ok = all(
                e in ends and entry_shown_end(ends[e])
                for e in (after["subject_id"], after["object_id"])
            )
            now = ok and relation_shown(after)
            was = ok and before is not None and relation_shown(before)
            if now and not was:
                opened.append((after["id"], before is None))
            elif was and not now:
                closed.append(after["id"])
    if opened:
        await _open(w, column, opened)
    if closed:
        await w.delete_where_in(KNOWERS, column, closed)


def _ends_shown(ends: dict[str, dict[str, Any]], relation: dict[str, Any]) -> bool:
    """Neither end of this connection is kept from the players."""
    return all(
        e in ends and entry_shown_end(ends[e])
        for e in (relation["subject_id"], relation["object_id"])
    )


async def _follow_dependents(
    w: Changes, shown_now: Sequence[str], hidden_now: Sequence[str]
) -> None:
    """Entries that were shown or hidden just now: their names, conditions and connections go with
    them (a name or a connection of an entry the players are shown again is known again only when
    its own flags and its other end allow it)."""
    for ids, opening in ((shown_now, True), (hidden_now, False)):
        if not ids:
            continue
        aliases = await w.select(ALIASES, " AND entity_id = ANY(%s)", [list(ids)])
        states = await w.select(STATES, " AND entity_id = ANY(%s)", [list(ids)])
        relations = await w.select(
            RELATIONS, " AND (subject_id = ANY(%s) OR object_id = ANY(%s))", [list(ids), list(ids)]
        )
        if opening:
            ends = await _entities_by_id(
                w, {x for r in relations for x in (r["subject_id"], r["object_id"])}
            )
            await _open(w, "alias_id", [(a["id"], False) for a in aliases if alias_shown(a)])
            await _open(w, "state_id", [(s["id"], False) for s in states])
            await _open(
                w,
                "relation_id",
                [(r["id"], False) for r in relations if relation_shown(r) and _ends_shown(ends, r)],
            )
        else:
            for column, found in (
                ("alias_id", aliases),
                ("state_id", states),
                ("relation_id", relations),
            ):
                if found:
                    await w.delete_where_in(KNOWERS, column, [r["id"] for r in found])


async def move_flags(conn: Any, guild_id: int, campaign_id: str) -> int:
    """Work out who knows what from the old flags and beliefs, for one campaign that has no lists
    (#2995, slice 2b): a backup made before the lists existed. The same mapping as migration
    0287 (a test holds the two together): what is shown to the players is known to every player
    character, secret, hidden, unnamed and limited things to nobody, a character knows its own
    entry, and today's knowers carry over, each at its certainty. Returns the rows written."""
    scope = Scope(conn, guild_id, campaign_id, 0)
    entities = {r["id"]: r for r in await scope.select(ENTITIES)}

    # A point of view for every confirmed character (a player's `pc`, any other `npc`).
    views = {
        r["entity_id"]: r for r in await scope.select(PERSPECTIVES, " AND entity_id IS NOT NULL")
    }
    made: list[dict[str, Any]] = []
    for e in entities.values():
        if e["type"] == "character" and e["status"] == CONFIRMED and e["id"] not in views:
            kind = PC_VIEW if e["role"] == PLAYER_CHARACTER else NPC_VIEW
            row = {
                "id": new_id(),
                "kind": kind,
                "entity_id": e["id"],
                "created_at": e["created_at"],
            }
            made.append(row)
            views[e["id"]] = row
    await _bulk(conn, guild_id, campaign_id, PERSPECTIVES, made)
    for e_id, v in views.items():
        if entities.get(e_id, {}).get("role") == PLAYER_CHARACTER and v["kind"] == NPC_VIEW:
            await conn.execute(
                "UPDATE memory_perspectives SET kind = 'pc'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, v["id"]),
            )
            v["kind"] = PC_VIEW
    pcs = [v["id"] for v in views.values() if v["kind"] == PC_VIEW]
    by_id = {v["id"]: v for v in views.values()}

    rows: dict[tuple[str, str, str], dict[str, Any]] = {}  # (column, statement, view) -> row

    def put(column: str, statement_id: str, view_id: str, created: int, sure: float = 1.0) -> None:
        key = (column, statement_id, view_id)
        if key not in rows:
            rows[key] = {**_row(column, statement_id, view_id, created), "confidence": sure}

    shown = {i: entry_shown(e) for i, e in entities.items()}
    for i, e in entities.items():
        if shown[i]:
            for v in pcs:
                put("about_entity_id", i, v, int(e["created_at"]))
        if e["type"] == "character" and e["status"] == CONFIRMED and i in views:
            put("about_entity_id", i, views[i]["id"], int(e["created_at"]))
    for s in await scope.select(STATES):
        if shown.get(s["entity_id"]):
            for v in pcs:
                put("state_id", s["id"], v, int(s["created_at"]))
    for a in await scope.select(ALIASES):
        if alias_shown(a) and shown.get(a["entity_id"]):
            for v in pcs:
                put("alias_id", a["id"], v, int(a["created_at"]))
    live_aliases = {a["id"]: a for a in await scope.select(ALIASES)}
    for k in await scope.select(ALIAS_KNOWERS):
        a = live_aliases.get(k["alias_id"])
        if a is not None and a["status"] != "rejected" and a["secret"]:
            put("alias_id", k["alias_id"], k["perspective_id"], int(k["created_at"]))
    relations = {r["id"]: r for r in await scope.select(RELATIONS)}
    for r in relations.values():  # a limited clue is known to those who `knows` it
        clue = entities.get(r["object_id"])
        if (
            r["predicate"] == "knows" and r["status"] == CONFIRMED and clue is not None
            and clue["status"] == CONFIRMED and clue["audience_limited"]
            and r["subject_id"] in views
        ):  # fmt: skip
            put("about_entity_id", clue["id"], views[r["subject_id"]]["id"], int(r["created_at"]))
    current: dict[tuple[str, str], dict[str, Any]] = {}
    for b in sorted(await scope.select(BELIEFS), key=lambda b: b["seq"]):
        current[(b["perspective_id"], b["relation_id"])] = b
    for b in current.values():  # a character's own belief first, so it wins over The party's
        v, r = by_id.get(b["perspective_id"]), relations.get(b["relation_id"])
        if (
            v and r and r["status"] == CONFIRMED and v["kind"] in (PC_VIEW, NPC_VIEW)
            and b["confidence"] > 0
        ):  # fmt: skip
            put("relation_id", r["id"], v["id"], int(b["recorded_at"]), float(b["confidence"]))
    parties = {p["id"] for p in await scope.select(PERSPECTIVES, " AND kind = 'party'")}
    for b in current.values():
        r = relations.get(b["relation_id"])
        if (
            b["perspective_id"] in parties
            and r
            and r["status"] == CONFIRMED
            and b["confidence"] > 0
        ):
            ends = (entities.get(r["subject_id"]), entities.get(r["object_id"]))
            if all(e is not None and entry_shown_end(e) for e in ends):
                for v in pcs:
                    put("relation_id", r["id"], v, int(r["created_at"]), float(b["confidence"]))
    await _bulk(conn, guild_id, campaign_id, KNOWERS, list(rows.values()))
    return len(rows)


def entry_shown_end(row: dict[str, Any]) -> bool:
    """An end of a connection that is not kept from the players (the move's rule: such a
    connection is not handed to every player character, since learning it names that end)."""
    return not (row["hidden_from_players"] or row["audience_limited"] or row["unnamed"])


async def _bulk(
    conn: Any, guild_id: int, campaign_id: str, table: Any, rows: list[dict[str, Any]]
) -> None:
    """Insert many rows of one table in one statement (a restore's own rows: no change log)."""
    if not rows:
        return
    from psycopg import sql
    from psycopg.types.json import Jsonb

    cols = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
    await conn.execute(
        sql.SQL(
            "INSERT INTO {} (guild_id, campaign_id, {}) SELECT %s, %s, {}"
            " FROM jsonb_populate_recordset(NULL::{}, %s) ON CONFLICT DO NOTHING"
        ).format(sql.Identifier(table.name), cols, cols, sql.Identifier(table.name)),
        (guild_id, campaign_id, Jsonb([{c: row.get(c) for c in table.columns} for row in rows])),
    )
