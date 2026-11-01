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
NOT_SECRET = "Only a secret name can be known by some characters."
MAX_KNOWERS = 12  # a table's characters; more than this is a mistake, not a party
MAX_KNOWERS_ANY = 60  # the characters of a campaign, NPCs included (a whole faction told at once)


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
    await _drop_asleep(w, column, statement_id)
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


async def know_own_disguise(w: Changes, relation_id: str, entity_id: str) -> None:
    """The one in disguise knows it (#3091): a new `disguised_as` connection is listed to its
    subject at 1.0 in the same write, on every path, so that if the subject is a player character
    its player sees their own disguise and nobody else's players do. Nothing for a subject that is
    not a confirmed character."""
    try:
        chosen = await _known_characters(w, [entity_id])
    except MemoryRuleError:
        return
    if entity_id not in chosen:
        return
    view = await _view_for(w, entity_id, chosen[entity_id])
    if not await w.select(
        KNOWERS, " AND relation_id = %s AND perspective_id = %s", [relation_id, view]
    ):
        await w.insert(KNOWERS, _row("relation_id", relation_id, view, w.now))


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
    w: Changes, relation_id: str, character_ids: Sequence[str], *, players_only: bool = False
) -> list[str]:
    """Exactly these characters know this hidden connection (none: only the DM). A character not
    listed who knew it stops knowing (a 0.0 belief, so the history stays). The connection must be
    confirmed and still held back (secret, or not revealed yet): one everyone knows has nothing
    to choose. Returns the characters now knowing.

    `players_only`: the caller's list holds player characters only (the Discord menu), so only the
    player characters' rows are replaced and an NPC who knows it keeps knowing (#3197)."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    relation = await w.get(RELATIONS, relation_id)
    if relation is None or relation["status"] != CONFIRMED:
        raise MemoryRuleError(NOT_HIDDEN)
    if not (relation["secret"] or relation["unrevealed"]):
        raise MemoryRuleError(NOT_HIDDEN)
    known = await _known_characters(w, character_ids)
    chosen = list(known)
    now = await connection_knowers(w, [relation_id])
    current = set(now.get(relation_id, []))
    # The old beliefs are a player character's point of view (what `connection_knowers` reads);
    # an NPC knower lives only in the table the gate reads, written by `_follow_player_list`.
    for entity_id in chosen:
        if known[entity_id] and entity_id not in current:
            await beliefs.add_belief(
                w, await _view_of(w, entity_id), relation_id, KNOWN, REVEALED_FACT
            )
    for entity_id in current - set(chosen):
        await beliefs.add_belief(w, await _view_of(w, entity_id), relation_id, 0.0, SECRET_PLOT)
    # The same list in the one table the gate reads (#2995, slice 2b); the old beliefs stay what
    # the old readers read until slice 3.
    await _follow_player_list(w, "connection", relation_id, known, players_only)
    return chosen


async def set_alias_knowers(
    w: Changes, alias_id: str, character_ids: Sequence[str], *, players_only: bool = False
) -> list[str]:
    """Exactly these characters know this secret other name (none: only the DM). Returns the
    characters now knowing. `players_only`: as for `set_connection_knowers`."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    alias = await w.get(ALIASES, alias_id)
    if alias is None or alias["status"] == "rejected" or not alias["secret"]:
        raise MemoryRuleError(NOT_SECRET)
    known = await _known_characters(w, character_ids)
    chosen = list(known)
    views = {
        entity_id: await _view_for(w, entity_id, player) for entity_id, player in known.items()
    }
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
    kept = await _character_views(w, players_only)
    for view_id, row_id in have.items():
        if view_id not in views.values() and view_id in kept:
            await w.delete(ALIAS_KNOWERS, row_id)
    await _follow_player_list(w, "alias", alias_id, known, players_only)
    return chosen


async def _character_views(w: Scope, players_only: bool) -> set[str]:
    """The views a "who knows" save may replace: every character's, or only the player
    characters' (a list that never offered an NPC must not drop one, #3197). The table's own and
    the party's views are never among them."""
    kinds = [PC_VIEW] if players_only else [PC_VIEW, NPC_VIEW]
    return {str(r["id"]) for r in await w.select(PERSPECTIVES, " AND kind = ANY(%s)", [kinds])}


async def _follow_player_list(
    w: Changes,
    kind: str,
    statement_id: str,
    chosen: Mapping[str, bool],
    players_only: bool = False,
) -> None:
    """The old "Who knows this?" writers (`chosen`: each character, with whether it is a player's):
    make the characters in the new list exactly those (players and NPCs, #3197: one not picked
    stops knowing), keep every other view's rows and the certainty of a character who stays
    (#2995, slice 2b)."""
    column, _ = _COLUMNS[kind]
    views = {i: await _view_for(w, i, player) for i, player in chosen.items()}
    await _drop_asleep(w, column, statement_id)
    pcs = await _character_views(w, players_only)
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
    many, or anything but this campaign's confirmed characters, NPCs included (one rule with
    `set_knowers`: `_known_characters`). A multi-step edit calls it first so a bad pick cannot
    leave it half done."""
    await _known_characters(scope, character_ids)


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
        " AND (about_entity_id = ANY(%s) OR perspective_id = ANY(%s) OR %s = ANY(hidden_by))",
        [[keep_id, gone_id], [gone_view, keep_view], gone_id],
        dormant=True,
    )
    rows.sort(
        key=lambda r: bool(r["hidden_by"])
    )  # (a row in use keeps its place over a sleeping one)
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
    await rehome_hidden_by(w, keep_id, gone_id, rows)


async def move_rows(w: Changes, column: str, old_id: str, new_id: str) -> None:
    """A statement is folded into its twin (the same name or connection Ken already had, #3037):
    whoever knew the folded one knows the twin, and nothing is lost with the copy that goes. A
    character the twin already lists keeps its own row. `column`: which statement column of the
    list (`relation_id` or `alias_id`)."""
    mine = await w.select(KNOWERS, f" AND {column} = %s", [new_id], dormant=True)
    have = {r["perspective_id"] for r in mine}
    folded = await w.select(KNOWERS, f" AND {column} = %s", [old_id], dormant=True)
    for row in sorted(folded, key=lambda r: bool(r["hidden_by"])):
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


async def _drop_asleep(w: Changes, column: str, statement_id: str) -> None:
    """The DM names who knows a statement: the rows a hide put to sleep for it are final now, the
    DM's list replaces them (they go, Undo brings them back), and a hide's 0.0 belief in a
    connection becomes the DM's own, so nothing the hide marked comes back over what the DM said
    (#3082). A (statement, point of view) is one thing: the row and the belief change together."""
    asleep = await w.select(
        KNOWERS,
        f" AND {column} = %s AND cardinality(hidden_by) > 0",
        [statement_id],
        dormant=True,
    )
    if not asleep:
        return
    await w.delete_where_in(KNOWERS, "id", [r["id"] for r in asleep])
    if column != "relation_id":
        return
    marked: dict[str, dict[str, Any]] = {}
    for row in await w.select(
        BELIEFS,
        " AND relation_id = %s AND perspective_id = ANY(%s) ORDER BY seq",
        [statement_id, [r["perspective_id"] for r in asleep]],
    ):
        if row["hidden_by"]:
            marked[row["perspective_id"]] = row
        else:
            marked.pop(row["perspective_id"], None)  # (a newer belief is the DM's already)
    await w.update_rows(BELIEFS, {r["id"]: {"hidden_by": []} for r in marked.values()})


async def rehome_hidden_by(
    w: Changes, keep_id: str, gone_id: str, knowers: Sequence[dict[str, Any]]
) -> None:
    """Two entries merged: whatever slept because the merged-away entry was hidden now waits for
    the kept one (#3082). `knowers`: the rows the merge already read (those marked with the
    merged-away entry among them; nothing is read again, so a merge with nothing asleep costs no
    statement). A hide's belief marks are looked for only when a row was marked: both are written
    by the same hide."""

    def moved(held: Sequence[str]) -> list[str]:
        return sorted({keep_id if h == gone_id else h for h in held})

    marked = [r for r in knowers if gone_id in r["hidden_by"]]
    if not marked:
        return
    await w.update_rows(KNOWERS, {r["id"]: {"hidden_by": moved(r["hidden_by"])} for r in marked})
    believed = await w.select(BELIEFS, " AND %s = ANY(hidden_by)", [gone_id])
    await w.update_rows(BELIEFS, {r["id"]: {"hidden_by": moved(r["hidden_by"])} for r in believed})


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
        for r in await w.select(KNOWERS, f" AND {column} = ANY(%s)", [old], dormant=True):
            have.add(
                (r[column], r["perspective_id"])
            )  # (a sleeping row counts: it wakes by itself)
    made = [_row(column, i, v, w.now) for i, _ in items for v in views if (i, v) not in have]
    if all(new for _, new in items):
        await w.insert_unlogged(KNOWERS, made)  # new statements: gone with them on Undo
    else:
        await w.insert_many(KNOWERS, made)


def _is_player_character(row: Mapping[str, Any]) -> bool:
    return bool(
        row["type"] == "character"
        and row["role"] == PLAYER_CHARACTER
        and row["status"] == CONFIRMED
    )


async def inherit_table_rows(w: Changes, joined: Sequence[str]) -> int:
    """Player characters that have just joined (`joined`: entry ids) are given what the whole table
    was shown (#2995, Supervisor 2026-11-01): a statement every OTHER current player character
    knows, at the least sure of theirs. Never a "some characters" list (not every other player
    character holds it), nor a DM-only, NPC-only or sleeping row (those are held by no player
    character, or are left out of `select`). With no other player character there is no table
    to copy, so what the flags show the players is given instead (the rule of `follow_flags`).
    Logged in the joining change, so one Undo takes it back. Returns the rows made."""
    if not joined:
        return 0
    chars = await w.select(
        ENTITIES,
        " AND type = 'character' AND role = %s AND status = %s",
        [PLAYER_CHARACTER, CONFIRMED],
    )
    ids = {str(e["id"]) for e in chars}
    new = [i for i in dict.fromkeys(joined) if i in ids]
    if not new:
        return 0
    new_views = {i: await _view_of(w, i) for i in new}
    w.player_views = None  # (made views may have changed the list)
    others = {await _view_of(w, i) for i in ids - set(new)}
    held: dict[tuple[str, str], dict[str, float]] = {}
    asleep: set[tuple[str, str, str]] = set()  # (column, statement, view) put to sleep by a hide
    for r in await w.select(KNOWERS, " AND cardinality(hidden_by) > 0", dormant=True):
        for column in ("about_entity_id", "state_id", "relation_id", "alias_id"):
            if r[column] is not None:
                asleep.add((column, r[column], r["perspective_id"]))
    for r in await w.select(KNOWERS):
        for column in ("about_entity_id", "state_id", "relation_id", "alias_id"):
            if r[column] is not None:
                held.setdefault((column, r[column]), {})[r["perspective_id"]] = float(
                    r["confidence"]
                )
    wanted: dict[tuple[str, str], float] = {}
    if others:
        for key, who in held.items():
            if others <= set(who):
                wanted[key] = min(who[o] for o in others)
    else:
        wanted = await _shown_by_flags(w)
    made = [
        _row(column, statement, view, w.now) | {"confidence": sure}
        for (column, statement), sure in wanted.items()
        for view in new_views.values()
        if view not in held.get((column, statement), {})
        and (column, statement, view) not in asleep  # (a sleeping row wakes by itself)
    ]
    await w.insert_many(KNOWERS, made)
    return len(made)


async def _shown_by_flags(w: Changes) -> dict[tuple[str, str], float]:
    """Every statement the flags show the players (entries, conditions, names, connections), for
    `inherit_table_rows` when the first player character joins: the same rule as `follow_flags`."""
    entities = {str(r["id"]): r for r in await w.select(ENTITIES)}
    out: dict[tuple[str, str], float] = {}
    for i, e in entities.items():
        if entry_shown(e):
            out[("about_entity_id", i)] = 1.0
    for st in await w.select(STATES):
        owner = entities.get(str(st["entity_id"]))
        if owner is not None and entry_shown(owner):
            out[("state_id", str(st["id"]))] = 1.0
    for a in await w.select(ALIASES):
        owner = entities.get(str(a["entity_id"]))
        if alias_shown(a) and owner is not None and entry_shown(owner):
            out[("alias_id", str(a["id"]))] = 1.0
    for rel in await w.select(RELATIONS):
        ends = (entities.get(str(rel["subject_id"])), entities.get(str(rel["object_id"])))
        if relation_shown(rel) and all(x is not None and entry_shown_end(x) for x in ends):
            out[("relation_id", str(rel["id"]))] = 1.0
    return out


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
    joined: list[str] = []  # player characters that became one in this write
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
        joined = [
            str(after["id"])
            for before, after in rows
            if _is_player_character(after)
            and not (before is not None and _is_player_character(before))
        ]
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
    if table_name == "memory_entities" and joined:
        await inherit_table_rows(w, joined)


async def forget_moved(
    w: Changes,
    moved: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    entities: Mapping[str, dict[str, Any]],
) -> None:
    """Connections whose ends changed in a plain merge (`moved`: before, after; `entities`: the
    ends as they stand now, the merge's flags already applied). The player characters knew each
    one about its OLD ends, so one that stopped being shown (its ends were shown and one is kept
    from the players now) is put to sleep for them: their who-knows rows and what their point of
    view believed wait, marked with the end that is hidden (`hidden_by`), and come back when it
    is shown again. A DM's own list on a connection whose ends stay as hidden as they were is
    left alone, and nothing is ever opened here. A join does not call this: it keeps whoever knew.
    Logged, so Undo brings it all back (#3073)."""
    lost = {
        a["id"]: {
            e
            for e in (a["subject_id"], a["object_id"])
            if e not in entities or not entry_shown_end(entities[e])
        }
        for b, a in moved
        if ends_shown(entities, b) and not ends_shown(entities, a)
    }
    if lost:
        await _sleep(w, "relation_id", lost)


async def _close(w: Changes, column: str, ids: Sequence[str]) -> None:
    """These statements stopped being shown to the players on their own flags: the player
    characters stop knowing them. An NPC's or a group's row stays: secret means kept from the
    players, not unknown to the world (Supervisor, #3018). Logged, so Undo brings the rows back."""
    views = await _player_views(w)
    if not views:
        return
    await w.delete_where_in(KNOWERS, column, ids, views)


def _joined(held: Collection[str], more: Collection[str]) -> list[str]:
    return sorted({*held, *more})


async def _sleep(w: Changes, column: str, hiders: Mapping[str, Collection[str]]) -> None:
    """Hiding an entry puts to sleep what its player characters knew of what hangs off it
    (#3082): `hiders` is, for each name, condition or connection, the entries whose hiding closed
    it. A who-knows row of a player character is kept, marked with them (`hidden_by`), and every
    reader ignores it while any is set; the point of view's belief in a connection it held as fact
    is followed by a 0.0 belief carrying the same mark. Several entries can close the same row
    (a connection with two hidden ends), and the mark is what lets each be shown again in any
    order. Everything is logged, and one statement per table however many."""
    views = await _player_views(w)
    if not views or not hiders:
        return
    rows = await w.select(
        KNOWERS,
        f" AND {column} = ANY(%s) AND perspective_id = ANY(%s)",
        [list(hiders), views],
        dormant=True,
    )
    await w.update_rows(
        KNOWERS,
        {
            r["id"]: {"hidden_by": _joined(r["hidden_by"], hiders[r[column]])}
            for r in rows
            if not set(hiders[r[column]]) <= set(r["hidden_by"])
        },
    )
    if column != "relation_id":
        return
    last: dict[tuple[str, str], dict[str, Any]] = {}
    for row in await w.select(
        BELIEFS,
        " AND relation_id = ANY(%s) AND perspective_id = ANY(%s) ORDER BY seq",
        [list(hiders), views],
    ):
        last[(row["relation_id"], row["perspective_id"])] = row
    await w.insert_many(
        BELIEFS,
        [
            {
                "id": new_id(),
                "perspective_id": view,
                "relation_id": relation_id,
                "confidence": 0.0,
                "status": SECRET_PLOT,
                "session_at": None,
                "evidence_ids": [],
                "seq": max(w.version + 1, int(held["seq"]) + 1),
                "recorded_at": w.now,
                "pinned": bool(held["pinned"]),
                "hidden_by": _joined((), hiders[relation_id]),
            }
            for (relation_id, view), held in last.items()
            if held["confidence"] >= KNOWN
        ],
    )
    await w.update_rows(  # (a 0.0 an earlier hide wrote also waits for this one)
        BELIEFS,
        {
            held["id"]: {"hidden_by": _joined(held["hidden_by"], hiders[relation_id])}
            for (relation_id, _), held in last.items()
            if held["hidden_by"] and not set(hiders[relation_id]) <= set(held["hidden_by"])
        },
    )


async def _wake(
    w: Changes, shown: Sequence[str], statements: Mapping[str, Collection[str]]
) -> None:
    """Entries shown to the players again (`shown`; `statements`: the names, conditions and
    connections hanging off them, by column). Each is taken off the mark of every row and belief
    it put to sleep. A row wakes when no entry that closed it is still hidden: it is exactly the
    row the hide closed, with its own certainty. A belief wakes the point of view's earlier
    belief (its confidence, status, evidence and pin) when its 0.0 is still the newest belief of
    the pair and the pair has no row the DM made since: a belief or row the DM wrote while it
    slept stays as the DM left it, both of them, since a (statement, point of view) is one
    thing (#3082)."""
    views = await _player_views(w)
    gone = set(shown)
    if not views or not gone:
        return
    woken: set[tuple[str, str]] = set()  # (relation, view) whose row woke now
    awake: set[tuple[str, str]] = set()  # (relation, view) with a row the DM made meanwhile
    for column, ids in statements.items():
        if not ids:
            continue
        rows = await w.select(
            KNOWERS,
            f" AND {column} = ANY(%s) AND perspective_id = ANY(%s)",
            [sorted(ids), views],
            dormant=True,
        )
        changes: dict[str, dict[str, Any]] = {}
        for r in rows:
            if not r["hidden_by"]:
                awake.add((r[column], r["perspective_id"]))
            elif gone & set(r["hidden_by"]):
                left = sorted(set(r["hidden_by"]) - gone)
                changes[r["id"]] = {"hidden_by": left}
                if not left:
                    woken.add((r[column], r["perspective_id"]))
        await w.update_rows(KNOWERS, changes)
    relations = sorted(statements.get("relation_id", ()))
    if not relations:
        return
    held: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in await w.select(
        BELIEFS,
        " AND relation_id = ANY(%s) AND perspective_id = ANY(%s) ORDER BY seq",
        [relations, views],
    ):
        held.setdefault((row["relation_id"], row["perspective_id"]), []).append(row)
    marks: dict[str, dict[str, Any]] = {}
    back: list[dict[str, Any]] = []
    copies: list[tuple[str, str]] = []  # (new belief, the belief it is copied from)
    for pair, rows in held.items():
        newest = rows[-1]
        if not newest["hidden_by"] or not gone & set(newest["hidden_by"]):
            continue
        left = sorted(set(newest["hidden_by"]) - gone)
        marks[newest["id"]] = {"hidden_by": left}
        if left or (pair in awake and pair not in woken) or len(rows) < 2:
            continue
        was = rows[-2]
        fresh = new_id()
        back.append(
            {**was, "id": fresh, "seq": max(w.version + 1, int(newest["seq"]) + 1),
             "recorded_at": w.now}
        )  # fmt: skip
        copies.append((fresh, was["id"]))
    await w.update_rows(BELIEFS, marks)
    await w.insert_many(BELIEFS, back)
    if copies:
        await w.conn.execute(
            "INSERT INTO memory_evidence (guild_id, campaign_id, belief_id, kind, target_id,"
            " session_started_at, added_at) SELECT e.guild_id, e.campaign_id, m.fresh, e.kind,"
            " e.target_id, e.session_started_at, e.added_at FROM memory_evidence e"
            " JOIN unnest(%s::text[], %s::text[]) AS m(fresh, was) ON e.belief_id = m.was"
            " WHERE e.guild_id = %s AND e.campaign_id = %s ON CONFLICT DO NOTHING",
            ([c[0] for c in copies], [c[1] for c in copies], *w.ids),
        )


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
    them. Hiding an entry puts what the player characters knew of them to sleep (`_sleep`);
    showing it wakes exactly that (`_wake`), and then a name or connection that is shown to the
    players is known again when its own flags and its other end allow it. A name belongs to the
    fact (#3037): a connection a join moved (it has a confirmed `via_alias_id`) and the secret name
    it was moved through are known through that name, not through the entry it now hangs on, so
    hiding that entry does not close them (hiding the connection's other end still does)."""
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
            await _wake(
                w,
                ids,
                {
                    "alias_id": {a["id"] for a in aliases},
                    "state_id": {s["id"] for s in states},
                    "relation_id": {r["id"] for r in relations},
                },
            )
            await _open(w, "alias_id", [(a["id"], False) for a in aliases if alias_shown(a)])
            await _open(w, "state_id", [(s["id"], False) for s in states])
            await _open(
                w,
                "relation_id",
                [(r["id"], False) for r in relations if relation_shown(r) and ends_shown(ends, r)],
            )
        else:
            hidden = set(ids)
            held = await _joined_through(w, aliases, relations, hidden)
            for column, hiders in (
                (
                    "alias_id",
                    {a["id"]: {a["entity_id"]} for a in aliases if a["id"] not in held[0]},
                ),
                ("state_id", {s["id"]: {s["entity_id"]} for s in states}),
                (
                    "relation_id",
                    {
                        r["id"]: {r["subject_id"], r["object_id"]} & hidden
                        for r in relations
                        if r["id"] not in held[1]
                    },
                ),
            ):
                if hiders:
                    await _sleep(w, column, hiders)


async def _joined_through(
    w: Changes,
    aliases: Sequence[dict[str, Any]],
    relations: Sequence[dict[str, Any]],
    hidden: set[str],
) -> tuple[set[str], set[str]]:
    """What hiding these entries leaves alone because a join made it belong to the fact: the
    secret names some confirmed connection was moved through, and the connections moved through a
    confirmed name of a hidden entry whose other end is not hidden as well."""
    names = {a["id"] for a in aliases}
    via = [r for r in relations if r.get("via_alias_id")]
    wanted = sorted({str(r["via_alias_id"]) for r in via} | names)
    found = (
        {str(a["id"]): a for a in await w.select(ALIASES, " AND id = ANY(%s)", [wanted])}
        if wanted
        else {}
    )
    moved_names: set[str] = set()
    if names:
        for r in await w.select(
            RELATIONS, " AND via_alias_id = ANY(%s) AND status = %s", [sorted(names), CONFIRMED]
        ):
            moved_names.add(str(r["via_alias_id"]))
    kept_relations: set[str] = set()
    for r in via:
        alias = found.get(str(r["via_alias_id"]))
        if alias is None or alias["status"] != CONFIRMED or alias["entity_id"] not in hidden:
            continue
        if r["subject_id"] in hidden and r["object_id"] in hidden:
            continue  # (both ends hidden at once: nothing shown is left to know it)
        kept_relations.add(str(r["id"]))
    return {n for n in moved_names if n in names}, kept_relations


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
        (
            guild_id,
            campaign_id,
            Jsonb(
                [
                    {c: row[c] if c in row else table.defaults.get(c) for c in table.columns}
                    for row in rows
                ]
            ),
        ),
    )
