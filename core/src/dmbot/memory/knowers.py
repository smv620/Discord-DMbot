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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import beliefs
from dmbot.memory._changes import (
    ALIAS_KNOWERS,
    ALIASES,
    BELIEFS,
    ENTITIES,
    PERSPECTIVES,
    RELATIONS,
    Changes,
    Scope,
)
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    KNOWN,
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
    return chosen


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
