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

from dmbot.memory import beliefs, gate
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
    MERGED,
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


async def mark_guess(w: Changes, kind: str, statement_ids: Sequence[str], certainty: float) -> int:
    """The player characters who know these statements only because the AI guessed so (an upload's
    Smart guess, #2959) are recorded as sure to `certainty`, not 1.0: a guess is not the DM's word.
    Only the player characters' rows are touched, only down from sure, and no row is made or
    removed (the old flags' hook makes them; an NPC's or a group's row is never read here).
    Returns how many rows changed."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    if kind not in _COLUMNS:
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    if not 0.0 <= certainty < KNOWN:
        raise MemoryRuleError(BAD_CERTAINTY)
    column, _ = _COLUMNS[kind]
    views = await _player_views(w)
    if not views or not statement_ids:
        return 0
    rows = await w.select(
        KNOWERS,
        f" AND {column} = ANY(%s) AND perspective_id = ANY(%s) AND confidence > %s",
        [list(statement_ids), views, certainty],
    )
    for row in rows:
        await w.update(KNOWERS, row["id"], {"confidence": certainty})
    return len(rows)


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
        knows |= {  # (and the who-knows lists: a character's row on a connection)
            str(r["relation_id"])
            for r in await scope.select(
                KNOWERS,
                " AND perspective_id = ANY(%s) AND relation_id IS NOT NULL AND confidence > 0",
                [list(views)],
            )
        }
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
            | {  # (the who-knows lists a join writes: a character's row on a name)
                r["alias_id"]
                for r in await scope.select(
                    KNOWERS,
                    " AND perspective_id = ANY(%s) AND alias_id IS NOT NULL AND confidence > 0",
                    [list(views)],
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
    # A name belongs to the fact (#3037): `via_alias_id` says WHERE a known connection is drawn
    # (on the name it came through), never WHETHER it is theirs: only the connections these
    # characters know are listed. One that came through a name that is secret and held by them is
    # drawn on that name; through a name that is not secret, on the entry; through a name that was
    # rejected or is gone, or a secret one they do not hold, it is not shown.
    owner_of = {str(a["id"]): str(a["entity_id"]) for a in aliases}
    via_ids = sorted({str(r["via_alias_id"]) for r in hidden if r.get("via_alias_id")})
    via_rows = (
        {str(a["id"]): a for a in await scope.select(ALIASES, " AND id = ANY(%s)", [via_ids])}
        if via_ids
        else {}
    )

    def drawn(r: dict[str, Any]) -> bool:
        via = r.get("via_alias_id")
        if not via:
            return True
        a = via_rows.get(str(via))
        if a is None or a["status"] != CONFIRMED:
            return False
        return not a["secret"] or str(via) in owner_of

    hidden = [r for r in hidden if drawn(r)]
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

    # Each end is named the way THIS player's characters know it (#3037): the main name only when
    # one of them knows the entry under its own name, else the first other name they learned. An
    # end none of them may name is not shown, so a joined person never reads as the named one.
    safe_texts: dict[str, str] = {}
    shown_names = await gate.names_for(
        scope,
        gate.Viewer.player(mine),
        sorted(
            {r["subject_id"] for r in hidden}
            | {r["object_id"] for r in hidden}
            | {a["entity_id"] for a in aliases}
        ),
        texts_out=safe_texts,
    )

    def name(entity_id: str) -> str | None:
        if entity_id not in entries:
            return None
        return shown_names.get(entity_id)

    connections = []
    for r in hidden:
        through = r.get("via_alias_id") if r.get("via_alias_id") in owner_of else None
        alias_end = owner_of.get(through) if through else None

        def end(
            entity_id: str, through: str | None = through, alias_end: str | None = alias_end
        ) -> str | None:
            if through is not None and entity_id == alias_end:
                return safe_texts.get(through, "someone (name unknown)")
            return name(entity_id)

        subject, target = end(r["subject_id"]), end(r["object_id"])
        if subject is None or target is None:
            continue
        # (the DM's own words on a connection may name a main name: sent only when both ends are
        # shown under their main names, as the website's doors do)
        plain = (
            subject == entries[r["subject_id"]]["name"]
            and target == entries[r["object_id"]]["name"]
        )
        connections.append(
            MineConnection(
                r["id"], subject, r["predicate"], (r["detail"] or "") if plain else "", target,
                bool(r.get("directional")), others(who.get(r["id"], [])),
            )
        )  # fmt: skip
    # (every name a join moved connections through, whether they know those connections or not)
    joined_names = (
        {
            str(r["via_alias_id"])
            for r in await scope.select(
                RELATIONS,
                " AND via_alias_id = ANY(%s) AND status = %s",
                [[str(a["id"]) for a in aliases], CONFIRMED],
            )
        }
        if aliases
        else set()
    )
    names = []
    for a in aliases:
        if str(a["id"]) in joined_names:
            continue  # a person of their own for them (#3037): "a name of Ken" would say who
        owner = name(a["entity_id"])
        if owner is not None:
            text = safe_texts.get(str(a["id"]), "someone (name unknown)")
            names.append(MineName(a["id"], text, owner, others(a_who.get(a["id"], []))))
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
    w: Changes,
    keep_id: str,
    gone_id: str,
    views: tuple[str, str] | None,
    *,
    as_alias: str | None = None,
) -> None:
    """Two entries merge (#2995): whoever knew the merged-away entry exists now knows the kept
    one does, and the merged-away character's knowledge becomes the kept character's (`views` is
    its point of view and the kept one's, from `beliefs.fold_perspective`). A statement both knew
    keeps one row. One read, and nothing more when no list is touched.

    `as_alias`: the merged-away entry was an unnamed person the DM now says is the kept one (#3037).
    Whoever knew only that the unnamed person exists must not learn the kept entry's name, so their
    "knows this entry exists" row becomes a row on the secret other name made from the description
    (`as_alias`), open to them only; every other row keeps its own scope, statement by statement."""
    gone_view, keep_view = views if views is not None else ("", "")
    rows = await w.select(
        KNOWERS,
        " AND (about_entity_id = ANY(%s) OR perspective_id = ANY(%s))",
        [[keep_id, gone_id], [gone_view, keep_view]],
    )
    columns = ("about_entity_id", "state_id", "relation_id", "alias_id")

    def after(row: dict[str, Any]) -> dict[str, Any]:
        if as_alias is not None and row["about_entity_id"] == gone_id:
            return {
                "perspective_id": keep_view
                if gone_view and row["perspective_id"] == gone_view
                else row["perspective_id"],
                "about_entity_id": None,
                "alias_id": as_alias,
            }
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


async def move_rows(w: Changes, column: str, old_id: str, new_id: str) -> None:
    """A statement is folded into its twin (the same name or connection Ken already had, #3037):
    whoever knew the folded one knows the twin, and nothing is lost with the copy that goes. A
    character the twin already lists keeps its own row. `column`: which statement column of the
    list (`relation_id` or `alias_id`)."""
    mine = await w.select(KNOWERS, f" AND {column} = %s", [new_id])
    have = {r["perspective_id"] for r in mine}
    for row in await w.select(KNOWERS, f" AND {column} = %s", [old_id]):
        if row["perspective_id"] in have:
            await w.delete(KNOWERS, row["id"])
        else:
            have.add(row["perspective_id"])
            await w.update(KNOWERS, row["id"], {column: new_id})


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
            if after["status"] == MERGED:
                # (a merge moves everything that hung off the entry, who knows each included, in
                # its own bulk statements: closing it here would be a second, row-by-row pass)
                continue
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
        # (a name whose own flags did not change is left alone before anything is read: a merge
        # moves hundreds of them, and who knows each moves with it in the merge's own statements)
        rows = [(b, a) for b, a in rows if b is None or alias_shown(b) != alias_shown(a)]
        owners = await _entities_by_id(
            w, {r["entity_id"] for b, a in rows for r in (a, b) if r is not None}
        )

        def shown_owner(row: dict[str, Any]) -> bool:
            return row["entity_id"] in owners and entry_shown(owners[row["entity_id"]])

        for before, after in rows:
            ok = shown_owner(after)
            # (it was shown only if ITS OWN owner was shown: a join moves the guard's other name
            # to Ken and makes it secret in one update, which must not read as "stopped being
            # shown" and take the name away from the characters who knew it)
            now = ok and alias_shown(after)
            was = before is not None and shown_owner(before) and alias_shown(before)
            if now and not was:
                opened.append((after["id"], before is None))
            elif was and not now:
                closed.append(after["id"])
    else:
        rows = [(b, a) for b, a in rows if b is None or relation_shown(b) != relation_shown(a)]
        ends = await _entities_by_id(
            w,
            {
                x
                for b, a in rows
                for r in (a, b)
                if r is not None
                for x in (r["subject_id"], r["object_id"])
            },
        )

        def shown_ends(row: dict[str, Any]) -> bool:
            return all(
                e in ends and entry_shown_end(ends[e])
                for e in (row["subject_id"], row["object_id"])
            )

        for before, after in rows:
            ok = shown_ends(after)
            now = ok and relation_shown(after)
            # (it was shown only if ITS OWN ends were: a join moves a connection off an unnamed
            # person and makes it secret in one update, which must not read as "stopped being
            # shown" and take the connection away from the characters who knew it)
            was = before is not None and shown_ends(before) and relation_shown(before)
            if now and not was:
                opened.append((after["id"], before is None))
            elif was and not now:
                closed.append(after["id"])
    if opened:
        await _open(w, column, opened)
    if closed:
        await _close(w, column, closed)


async def forget_moved(
    w: Changes,
    moved: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    entities: Mapping[str, dict[str, Any]],
) -> None:
    """Connections whose ends changed in a plain merge (`moved`: before, after; `entities`: the
    ends as they stand now, the merge's flags already applied). The player characters knew each
    one about its OLD ends, so one that stopped being shown (its ends were shown and one is kept
    from the players now) is forgotten by them: their knower rows go and so does what their point
    of view believed. A DM's own list on a connection whose ends stay as hidden as they were is
    left alone, and nothing is ever opened here. A join does not call this: it keeps whoever knew.
    Logged, so Undo brings it all back (#3073)."""
    lost = [a["id"] for b, a in moved if ends_shown(entities, b) and not ends_shown(entities, a)]
    if not lost:
        return
    await _close(w, "relation_id", lost)
    views = await _player_views(w)
    if not views:
        return
    newest: dict[tuple[str, str], float] = {}
    for row in await w.select(
        BELIEFS,
        " AND relation_id = ANY(%s) AND perspective_id = ANY(%s) ORDER BY seq",
        [lost, views],
    ):
        newest[(row["relation_id"], row["perspective_id"])] = row["confidence"]
    for (relation_id, view), confidence in newest.items():
        if confidence >= KNOWN:
            await beliefs.add_belief(w, view, relation_id, 0.0, SECRET_PLOT)


async def _close(w: Changes, column: str, ids: Sequence[str]) -> None:
    """These statements stopped being shown to the players: the player characters stop knowing
    them. An NPC's or a group's row stays: secret means kept from the players, not unknown to the
    world (Supervisor, #3018). Logged, so Undo brings the rows back."""
    views = await _player_views(w)
    if views:
        await w.delete_where_in(KNOWERS, column, ids, views)


def ends_shown(ends: Mapping[str, Mapping[str, Any]], relation: Mapping[str, Any]) -> bool:
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
                [(r["id"], False) for r in relations if relation_shown(r) and ends_shown(ends, r)],
            )
        else:
            for column, found in (
                ("alias_id", aliases),
                ("state_id", states),
                ("relation_id", relations),
            ):
                if found:
                    await _close(w, column, [r["id"] for r in found])


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
        held = live_aliases.get(k["alias_id"])
        if held is not None and held["status"] != "rejected" and held["secret"]:
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
        view, rel = by_id.get(b["perspective_id"]), relations.get(b["relation_id"])
        if (
            view and rel and rel["status"] == CONFIRMED and view["kind"] in (PC_VIEW, NPC_VIEW)
            and b["confidence"] > 0
        ):  # fmt: skip
            put("relation_id", rel["id"], view["id"], int(b["recorded_at"]), float(b["confidence"]))
    parties = {p["id"] for p in await scope.select(PERSPECTIVES, " AND kind = 'party'")}
    for b in current.values():
        held_rel = relations.get(b["relation_id"])
        if (
            b["perspective_id"] in parties
            and held_rel
            and held_rel["status"] == CONFIRMED
            and b["confidence"] > 0
        ):
            ends = (entities.get(held_rel["subject_id"]), entities.get(held_rel["object_id"]))
            if all(e is not None and entry_shown_end(e) for e in ends):
                for v in pcs:
                    put(
                        "relation_id", held_rel["id"], v, int(held_rel["created_at"]),
                        float(b["confidence"]),
                    )  # fmt: skip
    await _bulk(conn, guild_id, campaign_id, KNOWERS, list(rows.values()))
    return len(rows)


def entry_shown_end(row: Mapping[str, Any]) -> bool:
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
