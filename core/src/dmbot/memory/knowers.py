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
from typing import Any, LiteralString

from dmbot.memory import beliefs, gate, unnamed
from dmbot.memory._changes import (
    ALIAS_KNOWERS,
    ALIASES,
    BELIEFS,
    ENTITIES,
    KNOWERS,
    PERSPECTIVES,
    RELATIONS,
    STATES,
    TOLD,
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
    THREAD_TYPES,
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
PARTY_NOT_A_KNOWER = "The party can't know something as a group. Pick its characters instead."
NO_SUCH_STATEMENT = "That is gone or was never here."
BAD_CERTAINTY = "How sure someone is goes from 0 to 1."
_COLUMNS = {
    "entry": ("about_entity_id", ENTITIES),
    "property": ("state_id", STATES),
    "connection": ("relation_id", RELATIONS),
    "alias": ("alias_id", ALIASES),
}


_GUARDED_TABLE = {  # the statements the owner's rule covers (#4524): names and connections
    "entry": "memory_entities",
    "connection": "memory_relations",
    "alias": "memory_aliases",
}


async def _players_kept(
    w: Changes, kind: str, item_id: str, chosen: Collection[str]
) -> tuple[set[str], set[str]]:
    """The player characters a setter would take off this item who knew it before this write
    (#4524: public never becomes secret by itself), as (their entry ids, their points of view),
    each kept and asked as one 🤫 question; none when the DM chose it (`dm_chose_secret`), for an
    Undo, or for a kind the rule doesn't cover. A row this same write made was not known before
    it (an upload setting its own new name's knowers), so it is never kept."""
    if w.dm_chose_secret or w.undoes is not None or w.joining or kind not in _GUARDED_TABLE:
        return set(), set()
    column, _ = _COLUMNS[kind]
    pcs = {
        str(r["id"]): str(r["entity_id"])
        for r in await w.select(PERSPECTIVES, " AND kind = %s", [PC_VIEW])
        if r["entity_id"] is not None
    }
    if not pcs:
        return set(), set()
    rows = await w.select(KNOWERS, f" AND {column} = %s AND created_at < %s", [item_id, w.now])
    if kind == "alias":
        rows += await w.select(
            ALIAS_KNOWERS, " AND alias_id = %s AND created_at < %s", [item_id, w.now]
        )
    before = {pcs[str(r["perspective_id"])] for r in rows if str(r["perspective_id"]) in pcs}
    if kind == "connection":  # (the old lists: a player character's belief in it)
        before |= set((await connection_knowers(w, [item_id])).get(item_id, [])) & set(pcs.values())
    dropping = before - set(chosen)
    if not dropping:
        return set(), set()
    from dmbot.memory import public_guard

    await public_guard.ask_knowers(w, _GUARDED_TABLE[kind], item_id, dropping)
    return dropping, {view for view, entity in pcs.items() if entity in dropping}


async def take_off(w: Changes, kind: str, item_id: str, entity_ids: Collection[str]) -> None:
    """Make it secret on a 🤫 knowers question (#4524): exactly these player characters stop knowing
    this name or connection, as the DM's own choice. Only their rows go (who-knows rows, a name's
    older list, and for a connection a 0.0 belief in place of theirs, as `set_connection_knowers`
    does); everyone else keeps what they know and how sure they are."""
    if not w.dm_chose_secret:
        raise ValueError("Only the DM's own choice takes someone off.")
    off = set(entity_ids)
    if not off or kind not in _GUARDED_TABLE:
        return
    column, _ = _COLUMNS[kind]
    views = {
        str(r["id"]): str(r["entity_id"])
        for r in await w.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [sorted(off)])
    }
    if not views:
        return
    for row in await w.select(
        KNOWERS, f" AND {column} = %s AND perspective_id = ANY(%s)", [item_id, sorted(views)],
        dormant=True,
    ):  # fmt: skip
        await w.delete(KNOWERS, row["id"])
    if kind == "alias":
        old = await w.select(
            ALIAS_KNOWERS,
            " AND alias_id = %s AND perspective_id = ANY(%s)",
            [item_id, sorted(views)],
        )
        for row in old:
            await w.delete(ALIAS_KNOWERS, row["id"])
    elif kind == "connection":
        believed = set((await connection_knowers(w, [item_id])).get(item_id, []))
        for view_id, entity_id in views.items():
            if entity_id in believed:
                await beliefs.add_belief(w, view_id, item_id, 0.0, SECRET_PLOT)


async def add_common(
    w: Changes, kind: str, statement_ids: Sequence[str], certainty: float = KNOWN
) -> int:
    """Common knowledge (#4537, docs/design/party_knowledge.md §4): these statements are known to
    every character, now or later, as one row each for the campaign's "everyone" point of view. For
    a World read's statements (a smart guess's "world" ones at its guess certainty) and, in slice
    1b, the DM's "Everyone knows this". Only adds: a row already there keeps its certainty unless
    this one is surer. The gate counts it while the statement's own flags show it. Returns how many
    rows were added or raised."""
    from dmbot.memory import beliefs as views_of

    if kind not in _COLUMNS:
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    if not 0.0 < certainty <= KNOWN:
        raise MemoryRuleError(BAD_CERTAINTY)
    ids = sorted(set(statement_ids))
    if not ids:
        return 0
    column, table = _COLUMNS[kind]
    live = {
        str(r["id"])
        for r in await w.select(table, " AND id = ANY(%s)", [ids])
        if r.get("status") not in ("rejected", "merged")
    }
    view = await views_of.everyone_id(w)
    have = {
        str(r[column]): r
        for r in await w.select(
            KNOWERS, f" AND {column} = ANY(%s) AND perspective_id = %s", [ids, view], dormant=True
        )
    }
    changed = 0
    for statement_id in ids:
        if statement_id not in live:
            continue
        row = have.get(statement_id)
        if row is None:
            await w.insert(
                KNOWERS, {**_row(column, statement_id, view, w.now), "confidence": certainty}
            )
            changed += 1
        elif float(row["confidence"]) < certainty:
            await w.update(KNOWERS, row["id"], {"confidence": certainty})
            changed += 1
    return changed


async def remove_common(w: Changes, kind: str, statement_ids: Sequence[str]) -> int:
    """Common knowledge taken back (#4537 slice 1b): the DM's own choice (`dm_chose_secret`), as
    another answer on "Who knows this?". The "everyone" rows of these statements go, logged, so
    Undo brings them back. Returns how many went."""
    if not w.dm_chose_secret:
        raise ValueError("Only the DM's own choice takes common knowledge back.")
    if kind not in _COLUMNS or not statement_ids:
        return 0
    column, _ = _COLUMNS[kind]
    views = [str(r["id"]) for r in await w.select(PERSPECTIVES, " AND kind = 'everyone'")]
    if not views:
        return 0
    rows = await w.select(
        KNOWERS, f" AND {column} = ANY(%s) AND perspective_id = ANY(%s)",
        [list(statement_ids), views], dormant=True,
    )  # fmt: skip
    for row in rows:
        await w.delete(KNOWERS, row["id"])
    return len(rows)


async def common_of(scope: Scope, kind: str, statement_ids: Sequence[str]) -> set[str]:
    """Which of these statements hold a common-knowledge row (#4537), whatever their flags say.
    Awake rows only, unlike `remove_common` (which also takes sleeping ones): nothing puts an
    "everyone" row to sleep today (`_sleep` touches player views only). If that changes, decide
    here whether a sleeping row still makes the page say 🌍, or the two will disagree."""
    if kind not in _COLUMNS or not statement_ids:
        return set()
    column, _ = _COLUMNS[kind]
    views = [str(r["id"]) for r in await scope.select(PERSPECTIVES, " AND kind = 'everyone'")]
    if not views:
        return set()
    rows = await scope.select(
        KNOWERS, f" AND {column} = ANY(%s) AND perspective_id = ANY(%s)",
        [list(statement_ids), views],
    )  # fmt: skip
    return {str(r[column]) for r in rows}


async def _known_characters(w: Scope, character_ids: Sequence[str]) -> dict[str, bool]:
    """The confirmed characters among these ids, player characters and NPCs, each with whether it
    is a player's (§9b: a point of view for each character). Anything else (another campaign's
    entry, a place, a name not yet confirmed) is refused."""
    chosen = list(dict.fromkeys(character_ids))
    if len(chosen) > MAX_KNOWERS_ANY:
        raise MemoryRuleError(NOT_A_CHARACTER)
    if not chosen:
        return {}
    rows = await w.select(
        ENTITIES,
        " AND id = ANY(%s) AND type IN ('character', 'faction') AND status = %s",
        [chosen, CONFIRMED],
    )
    if any(r["party"] for r in rows):
        # Never a knower (STORY_MEMORY 9b, #3954): what the party's characters know stays each
        # one's own, so a row on the party could never be told apart from all of them knowing.
        raise MemoryRuleError(PARTY_NOT_A_KNOWER)
    found = {r["id"]: r["role"] == PLAYER_CHARACTER for r in rows}
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
    marks = await _drop_asleep(w, column, statement_id)
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
                 column: statement_id, "created_at": w.now, "confidence": sure[entity_id],
                 "backstory_of": marks.get(view_id)},
            )  # fmt: skip
        elif have[view_id]["confidence"] != sure[entity_id]:
            await w.update(KNOWERS, have[view_id]["id"], {"confidence": sure[entity_id]})
    _kept, kept_views = await _players_kept(w, kind, statement_id, list(chosen))
    # Common knowledge (#4537): the DM's own list (`dm_chose_secret`: Who knows this) says exactly
    # who knows it, so it replaces "everyone"; any other write keeps it (public stays public, #4524)
    common = (
        set()
        if w.dm_chose_secret
        else {str(r["id"]) for r in await w.select(PERSPECTIVES, " AND kind = 'everyone'")}
    )
    for view_id, row in have.items():
        if view_id not in views.values() and view_id not in kept_views and view_id not in common:
            await w.delete(KNOWERS, row["id"])
    return list(chosen)


GUESS_SURE, GUESS_LIKELY = 0.9, 0.6  # DMbot's guess of an NPC's knowledge: never 1.0 (#2959)


async def add_guessed(
    w: Changes, kind: str, statement_id: str, guesses: Mapping[str, float]
) -> int:
    """DMbot's guesses of which NPCs and groups know a statement the DM just confirmed or kept
    (#2959, STORY_MEMORY 9b item 4): each confirmed NPC or group of these gets a knower row at its
    certainty, below 1.0, so it reads as DMbot's guess, never the DM's word (the DM's own edit
    writes 1.0). Never a player character (what they know is the upload's choice), never the
    statement's own entry, and never over a row that is there already: the DM's word, or an
    earlier guess, stays. A knower row of an NPC or a group never shows a hidden statement to a
    player: the gate reads players' points of view and the party's, never an NPC's. Returns how
    many rows were added."""
    # The mark of a guess is its certainty below 1.0 (Supervisor, #3941): today only DMbot writes a
    # row below 1.0 for an NPC. Before any control lets a DM set a certainty below 1.0, add a
    # `guessed` column to `memory_knowers` first, so a DM's own 0.6 never reads as DMbot's guess.
    if kind not in ("entry", "connection") or not guesses:
        return 0
    column, table = _COLUMNS[kind]
    statement = await w.get(table, statement_id)
    if statement is None or statement.get("status") != CONFIRMED:
        return 0
    wanted = {i: c for i, c in guesses.items() if 0.0 < c < 1.0 and i != statement_id}
    if not wanted:
        return 0
    rows = await w.select(
        ENTITIES,
        " AND id = ANY(%s) AND type IN ('character', 'faction') AND status = %s"
        " AND role IS DISTINCT FROM %s AND NOT party",  # (never the party: #3954)
        [sorted(wanted), CONFIRMED, PLAYER_CHARACTER],
    )
    have = {
        str(r["perspective_id"])
        for r in await w.select(KNOWERS, f" AND {column} = %s", [statement_id])
    }
    added = 0
    for r in rows:
        view = await _view_for(w, str(r["id"]), False)
        if view in have:
            continue
        await w.insert(
            KNOWERS,
            {"id": new_id(), "perspective_id": view, "about_entity_id": None, "state_id": None,
             "relation_id": None, "alias_id": None, column: statement_id, "created_at": w.now,
             "confidence": wanted[str(r["id"])]},
        )  # fmt: skip
        have.add(view)
        added += 1
    return added


async def apply_waiting_guesses(w: Changes, entity_id: str) -> int:
    """The guesses a lore read left waiting for a name (`memory_npc_guesses`, #2959), written once
    the DM confirms it, in the same change (its Undo takes them back). The waiting rows stay: a
    knower row that is there already is never added twice."""
    cur = await w.conn.execute(
        "SELECT knower_id, confidence FROM memory_npc_guesses"
        " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s",
        (*w.ids, entity_id),
    )
    guesses = {str(r["knower_id"]): float(r["confidence"]) for r in await cur.fetchall()}
    return await add_guessed(w, "entry", entity_id, guesses)


NOT_ONE_CHARACTER = "Only a character can learn something this way."


async def learn(w: Changes, kind: str, statement_id: str, character_id: str) -> bool:
    """One character learns one statement (#2996, part 2 slice 1: the DM keeps a "who learned
    what" proposal). Unlike `set_statement_knowers`, which sets the whole list, this adds ONE row,
    at the character's own point of view and sure (1.0), and leaves everyone else as they were:
    nothing spreads past the one who learned it. It never lowers a certainty. Only the DM, and only
    a character (never a group, never the party: the party is never a knower). A row a hide put to
    sleep for this character is replaced, as the DM's word is final (`_drop_asleep`).

    The old tables the DM's "Who knows this?" menus still read are kept level in the same write: a
    player character's belief in a held-back connection (`connection_knowers`), and a secret other
    name's own list (`alias_knowers`). Otherwise the menu would show the character as not knowing
    it, and saving the menu would erase what was learned. Returns whether anything changed for
    this character's own rows (False: they already knew it surely, in every table)."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    if kind not in _COLUMNS:
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    column, table = _COLUMNS[kind]
    statement = await w.get(table, statement_id)
    if statement is None or statement.get("status") in ("rejected", "merged"):
        raise MemoryRuleError(NO_SUCH_STATEMENT)
    found = await w.select(ENTITIES, " AND id = %s AND status = %s", [character_id, CONFIRMED])
    if not found or found[0]["type"] not in ("character", "faction"):
        raise MemoryRuleError(NOT_A_CHARACTER)
    if found[0]["type"] != "character":
        raise MemoryRuleError(NOT_ONE_CHARACTER)
    player = found[0]["role"] == PLAYER_CHARACTER
    view = await _view_for(w, character_id, player)
    marks = await _drop_asleep(w, column, statement_id, [view])
    changed = False
    have = await w.select(
        KNOWERS, f" AND {column} = %s AND perspective_id = %s", [statement_id, view]
    )
    if not have:
        await w.insert(
            KNOWERS, _row(column, statement_id, view, w.now) | {"backstory_of": marks.get(view)}
        )
        changed = True
    elif have[0]["confidence"] < KNOWN:
        await w.update(KNOWERS, have[0]["id"], {"confidence": 1.0})  # (never down: up to sure)
        changed = True
    held_back = statement.get("status") == CONFIRMED and bool(
        statement.get("secret") or statement.get("unrevealed")
    )
    if kind == "connection" and player and held_back:
        listed = (await connection_knowers(w, [statement_id])).get(statement_id, [])
        if character_id not in listed:
            await beliefs.add_belief(w, view, statement_id, KNOWN, REVEALED_FACT)
            changed = True
    if (
        kind == "alias"
        and statement.get("secret")
        and not await w.select(
            ALIAS_KNOWERS, " AND alias_id = %s AND perspective_id = %s", [statement_id, view]
        )
    ):
        await w.insert(
                ALIAS_KNOWERS,
                {"id": new_id(), "alias_id": statement_id, "perspective_id": view,
                 "created_at": w.now},
            )  # fmt: skip
        changed = True
    return changed


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


async def mark_guess(
    w: Changes, kind: str, statement_ids: Sequence[str], certainty: float, made_since: int
) -> int:
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
    # Only rows the caller's keep made (`made_since`, its own start: the keep ran in an earlier
    # write, so this write's clock may have moved on): a player character already sure of it
    # stays sure (#4524). Making someone less sure is never asked about, it is simply not done.
    rows = [r for r in rows if r["created_at"] >= made_since]
    for row in rows:
        await w.update(KNOWERS, row["id"], {"confidence": certainty})
    return len(rows)


async def statement_knowers(
    scope: Scope, kind: str, statement_ids: Sequence[str]
) -> dict[str, list[str]]:
    """For each of these statements, the characters who know it (their entry ids). Common
    knowledge lists every player character (#4537), so the DM's list never reads as "only the
    DM" for something every player knows."""
    out: dict[str, list[str]] = {}
    if not statement_ids:
        return out
    column, _ = _COLUMNS[kind]
    views = {
        r["id"]: r["entity_id"]
        for r in await scope.select(
            PERSPECTIVES, " AND (entity_id IS NOT NULL OR kind = 'everyone')"
        )
    }
    pcs: list[str] | None = None
    rows = await scope.select(KNOWERS, f" AND {column} = ANY(%s)", [list(statement_ids)])
    # (common knowledge counts as the gate counts it: only while the statement is shown, #4537)
    common: dict[tuple[str, str], dict[str, float]] = {
        (kind, str(r[column])): {gate.COMMON: 1.0}
        for r in rows
        if r["perspective_id"] in views and views[r["perspective_id"]] is None
    }
    await gate.common_while_shown(scope, common, {}, {})
    for row in rows:
        if row["perspective_id"] not in views:
            continue
        who = views[row["perspective_id"]]
        if who is None and (kind, str(row[column])) not in common:
            continue
        if who is None:  # (common knowledge, #4537: every member of the party knows it, rule 13.1)
            if pcs is None:
                from dmbot.memory import party_record

                pcs = await party_record.current_members(scope)
            have = out.setdefault(row[column], [])
            have.extend(p for p in pcs if p not in have)
        elif who not in out.get(row[column], []):
            out.setdefault(row[column], []).append(who)
    return out


async def set_connection_knowers(
    w: Changes,
    relation_id: str,
    character_ids: Sequence[str],
    *,
    players_only: bool = False,
    listed: Collection[str] | None = None,
) -> list[str]:
    """Exactly these characters know this hidden connection (none: only the DM). A character not
    listed who knew it stops knowing (a 0.0 belief, so the history stays). The connection must be
    confirmed and still held back (secret, or not revealed yet): one everyone knows has nothing
    to choose. Returns the characters now knowing.

    `players_only`: the caller's list holds player characters only (the Discord menu), so only the
    player characters' rows are replaced and an NPC who knows it keeps knowing (#3197).

    `listed`: the characters the caller's list showed (the website's, #4968). A character it didn't
    show (DMbot guessed them after the page was read) is left as they are, and DMbot's guess of an
    NPC it showed and the DM kept becomes the DM's word (see `_follow_player_list`)."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    relation = await w.get(RELATIONS, relation_id)
    if relation is None or relation["status"] != CONFIRMED:
        raise MemoryRuleError(NOT_HIDDEN)
    if not (relation["secret"] or relation["unrevealed"]):
        raise MemoryRuleError(NOT_HIDDEN)
    known = await _known_characters(w, character_ids)
    for entity_id in (await _players_kept(w, "connection", relation_id, list(known)))[0]:
        known[entity_id] = True  # (a player character who knew it keeps knowing: #4524)
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
        if listed is not None and entity_id not in listed:
            continue  # (the list never showed them: nothing the DM said is about them, #4968)
        await beliefs.add_belief(w, await _view_of(w, entity_id), relation_id, 0.0, SECRET_PLOT)
    # The same list in the one table the gate reads (#2995, slice 2b); the old beliefs stay what
    # the old readers read until slice 3.
    await _follow_player_list(w, "connection", relation_id, known, players_only, listed)
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
    for entity_id in (await _players_kept(w, "alias", alias_id, list(known)))[0]:
        known[entity_id] = True  # (a player character who knew it keeps knowing: #4524)
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


_ROW_OF: dict[str, LiteralString] = {
    "about_entity_id": " AND about_entity_id = %s AND perspective_id = %s",
    "state_id": " AND state_id = %s AND perspective_id = %s",
    "relation_id": " AND relation_id = %s AND perspective_id = %s",
    "alias_id": " AND alias_id = %s AND perspective_id = %s",
}


async def holds(w: Scope, kind: str, statement_id: str, entity_id: str) -> bool:
    """Whether the character holds a row for this statement now (asleep or awake): a backstory's
    write marks only a row it made, never one learned before (#4795)."""
    column, _ = _COLUMNS[kind]
    views = await w.select(PERSPECTIVES, " AND entity_id = %s", [entity_id])
    if not views:
        return False
    return bool(
        await w.select(KNOWERS, _ROW_OF[column], [statement_id, views[0]["id"]], dormant=True)
    )


async def mark_backstory(w: Changes, kind: str, statement_id: str, entity_id: str) -> None:
    """The character's row for this statement came from the character's own backstory (#4795,
    migration 0556): marked, in the caller's change (one Undo), so the party's copies never pass
    it to another character (§5). Nothing when the character holds no row for it."""
    column, _ = _COLUMNS[kind]
    views = await w.select(PERSPECTIVES, " AND entity_id = %s", [entity_id])
    if not views:
        return
    for row in await w.select(KNOWERS, _ROW_OF[column], [statement_id, views[0]["id"]]):
        if row["backstory_of"] != entity_id:
            await w.update(KNOWERS, row["id"], {"backstory_of": entity_id})


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
    listed: Collection[str] | None = None,
) -> None:
    """The old "Who knows this?" writers (`chosen`: each character, with whether it is a player's):
    make the characters in the new list exactly those (players and NPCs, #3197: one not picked
    stops knowing), keep every other view's rows and the certainty of a character who stays
    (#2995, slice 2b).

    `listed`: the characters the DM's list showed (the website's, which shows NPCs and marks
    DMbot's guesses; #4968, Supervisor's ruling). When it is given:
    - DMbot's guess of an NPC the DM keeps (a row below 1.0, `add_guessed`) becomes the DM's word,
      1.0: the DM picked them on a list that showed the guess;
    - a character the list didn't show (a guess added after the page was read) is left as it is.
    Without it (Discord, every other caller), everything is as before. A row a hide put to sleep
    still goes whatever the list showed (`_drop_asleep`: the DM's list replaces those)."""
    column, _ = _COLUMNS[kind]
    views = {i: await _view_for(w, i, player) for i, player in chosen.items()}
    marks = await _drop_asleep(w, column, statement_id)
    pcs = await _character_views(w, players_only)
    have = {
        r["perspective_id"]: r
        for r in await w.select(KNOWERS, f" AND {column} = %s", [statement_id])
    }
    npcs: set[str] = set()
    owner: dict[str, str] = {}
    if listed is not None:
        npcs = {str(r["id"]) for r in await w.select(PERSPECTIVES, " AND kind = %s", [NPC_VIEW])}
        owner = {
            str(r["id"]): str(r["entity_id"])
            for r in await w.select(PERSPECTIVES, " AND id = ANY(%s)", [sorted(have)])
        }
    for view_id in views.values():
        if view_id not in have:
            await w.insert(
                KNOWERS,
                _row(column, statement_id, view_id, w.now) | {"backstory_of": marks.get(view_id)},
            )
        elif (
            listed is not None
            and not players_only
            and view_id in npcs
            and float(have[view_id]["confidence"]) < KNOWN
        ):
            await w.update(KNOWERS, have[view_id]["id"], {"confidence": KNOWN})
    for view_id, row in have.items():
        if view_id in pcs and view_id not in views.values():
            if listed is not None and owner.get(view_id) not in listed:
                continue  # (the list never showed them: nothing the DM said is about them)
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
    # the names one of these characters was told are an entry (#3089): no person of their own
    told_names = {
        str(t["alias_id"])
        for t in await scope.select(TOLD, " AND character_id = ANY(%s)", [list(mine)])
    }
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
        through = (
            r.get("via_alias_id")
            if r.get("via_alias_id") in owner_of and r.get("via_alias_id") not in told_names
            else None
        )
        alias_end = owner_of.get(through) if through else None

        def end(
            entity_id: str, through: str | None = through, alias_end: str | None = alias_end
        ) -> str | None:
            if through is not None and entity_id == alias_end:
                return safe_texts.get(through, unnamed.SOMEONE)
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
        if str(a["id"]) in joined_names and str(a["id"]) not in told_names:
            continue  # a person of their own for them (#3037): "a name of Ken" would say who
        owner = name(a["entity_id"])
        if owner is not None:
            text = safe_texts.get(str(a["id"]), unnamed.SOMEONE)
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

    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    changing: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        moved = after(row)
        if moved["perspective_id"] == row["perspective_id"] and (
            moved["about_entity_id"] == row["about_entity_id"]
        ):
            seen[key(row, moved)] = row
        else:
            changing.append((row, moved))
    for row, moved in changing:
        k = key(row, moved)
        held = seen.get(k)
        if held is not None and not _stronger(row, held):
            await w.delete(KNOWERS, row["id"])
            continue
        if held is not None:  # (the moving row is the stronger: it stays, the weaker goes, #4524)
            await w.delete(KNOWERS, held["id"])
        seen[k] = row
        await w.update(KNOWERS, row["id"], moved)
    await rehome_hidden_by(w, keep_id, gone_id, rows)


def _stronger(row: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Of two who-knows rows for one character and statement (a fold or merge meets both), whether
    `row` is the one to keep: awake beats asleep, then the surer (#4524: knowledge is never taken
    away or lowered by a merge). A tie keeps `other`."""
    return (not row["hidden_by"], float(row["confidence"])) > (
        not other["hidden_by"],
        float(other["confidence"]),
    )


async def move_rows(w: Changes, column: str, old_id: str, new_id: str, *, asleep: str = "") -> None:
    """A statement is folded into its twin (the same name or connection Ken already had, #3037):
    whoever knew the folded one knows the twin, and nothing is lost with the copy that goes. A
    character on both keeps the stronger of its two rows (`_stronger`), so a fold never takes
    knowledge away or makes anyone less sure (#4524). `column`: which statement column of the
    list (`relation_id` or `alias_id`). `asleep`: a 🤫 question's id: a row that arrives for
    someone the twin didn't list sleeps under it until the DM answers (`wake_under`)."""
    mine = await w.select(KNOWERS, f" AND {column} = %s", [new_id], dormant=True)
    have = {r["perspective_id"]: r for r in mine}
    folded = await w.select(KNOWERS, f" AND {column} = %s", [old_id], dormant=True)
    for row in folded:
        held = have.get(row["perspective_id"])
        if held is not None and not _stronger(row, held):
            await w.delete(KNOWERS, row["id"])
            continue
        if held is not None:  # (the folded row is the stronger: it stays, the weaker goes, #4524)
            await w.delete(KNOWERS, held["id"])
        have[row["perspective_id"]] = row
        moved: dict[str, Any] = {column: new_id}
        if asleep and held is None:  # (asleep until the DM answers `asleep`, a 🤫 question)
            moved["hidden_by"] = _joined(row["hidden_by"], [asleep])
        await w.update(KNOWERS, row["id"], moved)


async def move_alias_list(w: Changes, old_id: str, new_id: str) -> None:
    """`move_rows` for a name's older per-name list (#2935): whoever was on the folded copy's list
    is on the twin's; one already there keeps its own row (#4524: nobody stops knowing). The list
    has no certainty and no sleep mark, so two rows for one character say the same thing and
    either one keeps it (`_stronger` has nothing to compare)."""
    have = {
        r["perspective_id"] for r in await w.select(ALIAS_KNOWERS, " AND alias_id = %s", [new_id])
    }
    for row in await w.select(ALIAS_KNOWERS, " AND alias_id = %s", [old_id]):
        if row["perspective_id"] in have:
            await w.delete(ALIAS_KNOWERS, row["id"])
        else:
            have.add(row["perspective_id"])
            await w.update(ALIAS_KNOWERS, row["id"], {"alias_id": new_id})


async def wake_under(w: Changes, column: str, statement_id: str, mark: str) -> None:
    """The rows of this statement that slept under `mark` (a 🤫 question's id, `move_rows`) wake:
    the DM answered Let them know (#4524). Logged, so the answer's Undo puts them back to sleep."""
    rows = await w.select(KNOWERS, f" AND {column} = %s", [statement_id], dormant=True)
    await w.update_rows(
        KNOWERS,
        {
            r["id"]: {"hidden_by": [h for h in r["hidden_by"] if h != mark]}
            for r in rows
            if mark in r["hidden_by"]
        },
    )


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
    """The current party's points of view (`party_record.current_members`, #4537), made for a
    player's character that has none. Cached for the write and dropped when a character entry is
    written (`Changes._knowers_follow`). A player character added later knows nothing that was
    shown before it (Supervisor's answer on #2995: it learns from then on; the DM adds older
    knowledge with Who knows this)."""
    cached = w.player_views
    if cached is None:
        from dmbot.memory import party_record  # ("the party" is one function, #4537 rule 13.1)

        pcs = await party_record.current_members(w)
        cached = [await _view_of(w, e) for e in pcs]
        w.player_views = cached
    return cached


async def _all_player_views(w: Changes) -> list[str]:
    """Every player character's point of view, one who left the party included (#4537): what
    takes knowledge away or gives it back (a hide, a sleep, a wake) is about the players, not the
    party. A character who left keeps what they knew (§6), so a statement hidden after they left
    is put out of their reach too, and one woken again comes back to them as to everyone."""
    from dmbot.memory import party_record

    return [await _view_of(w, e) for e in await party_record.player_characters(w)]


def _row(column: str, statement_id: str, view_id: str, now: int) -> dict[str, Any]:
    return {
        "id": new_id(), "perspective_id": view_id, "about_entity_id": None, "state_id": None,
        "relation_id": None, "alias_id": None, column: statement_id, "created_at": now,
        "confidence": 1.0,
    }  # fmt: skip


async def _drop_asleep(
    w: Changes, column: str, statement_id: str, views: Collection[str] | None = None
) -> dict[str, str]:
    """The DM names who knows a statement: the rows a hide put to sleep for it are final now, the
    DM's list replaces them (they go, Undo brings them back), and a hide's 0.0 belief in a
    connection becomes the DM's own, so nothing the hide marked comes back over what the DM said
    (#3082). A (statement, point of view) is one thing: the row and the belief change together.
    `views`: only these points of view (one character learning it, `learn`), else all. Returns the
    marks of the rows it dropped (point of view → whose backstory, #4795), so a row written again
    for the same point of view keeps it: a list saved over a hidden backstory row never unmarks
    it."""
    asleep = await w.select(
        KNOWERS,
        f" AND {column} = %s AND cardinality(hidden_by) > 0"
        + ("" if views is None else " AND perspective_id = ANY(%s)"),
        [statement_id] if views is None else [statement_id, list(views)],
        dormant=True,
    )
    if not asleep:
        return {}
    kept_marks = {
        str(r["perspective_id"]): str(r["backstory_of"]) for r in asleep if r["backstory_of"]
    }
    await w.delete_where_in(KNOWERS, "id", [r["id"] for r in asleep])
    if column != "relation_id":
        return kept_marks
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
    return kept_marks


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
    if not items:
        return
    have: set[tuple[str, str]] = set()
    old = [i for i, new in items if not new]
    if old:  # (a row `_close` put to sleep for a character who had left wakes, #4537 §6)
        rows = await w.select(KNOWERS, f" AND {column} = ANY(%s)", [old], dormant=True)
        await w.update_rows(
            KNOWERS,
            {
                r["id"]: {"hidden_by": [h for h in r["hidden_by"] if h != r[column]]}
                for r in rows
                if r[column] in r["hidden_by"]
            },
        )
    if not views:
        return
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
    Logged in the joining change, so one Undo takes it back. With `DMBOT_PARTY_JOIN` on it runs
    only on the DM's word (#4537 rule 13.4: "What the party knows"). Returns how many statements
    were given (to one character or several), the number `party_known_count` showed."""
    if not joined:
        return 0
    from dmbot.memory import party_record  # ("the party" is one function, #4537 rule 13.1)

    ids = set(await party_record.current_members(w))
    new = [i for i in dict.fromkeys(joined) if i in ids]
    if not new:
        return 0
    new_views = {i: await _view_of(w, i) for i in new}
    w.player_views = None  # (made views may have changed the list)
    others = {await _view_of(w, i) for i in await _caught_up(w, ids - set(new))}
    made = await _inherit_plan(w, list(new_views.values()), others)
    await w.insert_many(
        KNOWERS, [_row(c, st, v, w.now) | {"confidence": sure} for c, st, v, sure in made]
    )
    return len({(c, st) for c, st, _, _ in made})


async def party_known_count(r: Scope, joined: Sequence[str]) -> int:
    """How many statements `inherit_table_rows` would give these characters now, read without
    writing (the join question's "212 things", which the answer then copies: Supervisor's ruling
    on #4537, "the count uses the gate's own count"). A character with no point of view yet holds
    nothing."""
    from dmbot.memory import party_record

    ids = set(await party_record.current_members(r))
    new = [i for i in dict.fromkeys(joined) if i in ids]
    if not new:
        return 0
    views = {
        str(p["entity_id"]): str(p["id"])
        for p in await r.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [sorted(ids)])
    }
    caught_up = await _caught_up(r, ids - set(new))
    if any(i not in views for i in caught_up):
        return 0  # (another member with no point of view holds nothing: nothing is shared by all)
    others = {views[i] for i in caught_up}
    new_views = [views.get(i, f"(none:{i})") for i in new]
    made = await _inherit_plan(r, new_views, others)
    return len({(c, st) for c, st, _, _ in made})


async def _caught_up(r: Scope, members: set[str]) -> set[str]:
    """The other members a copy is taken from (Supervisor's ruling on #4780): not one whose own
    join question is still waiting (not yet caught up, so its gaps aren't the party's). One told
    "Only what everyone knows" stays in (§5). With none left, the copy falls back to what the
    flags show, as for a campaign's first characters (`_inherit_plan` with no others)."""
    from dmbot.memory import join_questions

    waiting = await join_questions.waiting_entities(r.conn, *r.ids)
    return members - waiting


async def _inherit_plan(
    r: Scope,
    new_views: Sequence[str],
    others: set[str],
    only: Mapping[str, Collection[str]] | None = None,
) -> list[tuple[str, str, str, float]]:
    """(column, statement, view, sureness) for each row `inherit_table_rows` would make, or with
    `only` (column → statement ids: one card's, "Tell Mira") just those statements, read in SQL
    so a card's draw never reads the whole campaign; with `only` and no others, nothing (there is
    no party to tell from)."""
    if only is not None and not others:
        return []
    where, params = _only_where(only)
    held: dict[tuple[str, str], dict[str, float]] = {}
    asleep: set[tuple[str, str, str]] = set()  # (column, statement, view) put to sleep by a hide
    for row in await r.select(
        KNOWERS, " AND cardinality(hidden_by) > 0" + where, params, dormant=True
    ):
        for column in ("about_entity_id", "state_id", "relation_id", "alias_id"):
            if row[column] is not None:
                asleep.add((column, row[column], row["perspective_id"]))
    plain: dict[tuple[str, str], set[str]] = {}  # (who holds it NOT from their own backstory)
    for row in await r.select(KNOWERS, where, params):
        for column in ("about_entity_id", "state_id", "relation_id", "alias_id"):
            if row[column] is not None:
                held.setdefault((column, row[column]), {})[row["perspective_id"]] = float(
                    row["confidence"]
                )
                if row["backstory_of"] is None:
                    plain.setdefault((column, row[column]), set()).add(row["perspective_id"])
    wanted: dict[tuple[str, str], float] = {}
    if others:
        for key, who in held.items():
            # (all the others know it, and one of them NOT only from their own backstory: §5,
            # another character's backstory is never copied, #4795)
            if others <= set(who) and others & plain.get(key, set()):
                wanted[key] = min(who[o] for o in others)
    else:
        wanted = await _shown_by_flags(r)
    return [
        (column, statement, view, sure)
        for (column, statement), sure in wanted.items()
        for view in new_views
        if view not in held.get((column, statement), {})
        and (column, statement, view) not in asleep  # (a sleeping row wakes by itself)
    ]


_ONLY_COLUMNS = ("about_entity_id", "state_id", "relation_id", "alias_id")
# "Catch up" copies what every other member knows, so one other is enough: a row from that
# character's own backstory is marked (`backstory_of`, #4795) and never counts (`_inherit_plan`).
TELL_FROM = 1


def _only_where(only: Mapping[str, Collection[str]] | None) -> tuple[LiteralString, list[Any]]:
    """The SQL that keeps `_inherit_plan` to one card's statements (none: everything)."""
    if only is None:
        return "", []
    return (
        " AND (about_entity_id = ANY(%s) OR state_id = ANY(%s) OR relation_id = ANY(%s)"
        " OR alias_id = ANY(%s))",
        [sorted(only.get(c, ())) for c in _ONLY_COLUMNS],
    )


async def card_statements(r: Scope, entity_id: str) -> dict[str, set[str]]:
    """What one name card shows, for "Tell Mira" (#4537 slice 4b, §5): the entry, its other names,
    its conditions and its connections, and for a story thread (a quest, a mystery…) the entries
    its connections name too (who is involved, its clues and parts). Column → statement ids."""
    out: dict[str, set[str]] = {c: set() for c in _ONLY_COLUMNS}
    found = await r.select(ENTITIES, " AND id = %s", [entity_id])
    if not found:
        return out
    out["about_entity_id"].add(entity_id)
    out["alias_id"] |= {
        str(a["id"]) for a in await r.select(ALIASES, " AND entity_id = %s", [entity_id])
    }
    out["state_id"] |= {
        str(a["id"]) for a in await r.select(STATES, " AND entity_id = %s", [entity_id])
    }
    thread = found[0]["type"] in THREAD_TYPES
    for rel in await r.select(
        RELATIONS, " AND (subject_id = %s OR object_id = %s)", [entity_id, entity_id]
    ):
        out["relation_id"].add(str(rel["id"]))
        if thread:  # (a quest's card names its people and places: they come with it)
            other = rel["object_id"] if rel["subject_id"] == entity_id else rel["subject_id"]
            out["about_entity_id"].add(str(other))
    return out


async def tell_plan(
    r: Scope,
    member_id: str,
    only: Mapping[str, Collection[str]],
    view: str | None = None,
    *,
    choosing: Collection[str] = (),
) -> list[tuple[str, str, str, float]]:
    """The rows "Tell Mira" would write (#4537 slice 4b): every statement in `only` that all the
    OTHER current members know (not one whose own join question still waits, as the join copy
    does) and Mira doesn't, at the least sure of theirs; never what only some know, a backstory,
    or a row a hide put to sleep (`_inherit_plan`). Nothing for one not in the party now, or with
    nobody else in it. `view`: Mira's point of view (None: not made yet, so Mira holds nothing)."""
    from dmbot.memory import join_questions, party_record

    ids = set(await party_record.current_members(r))
    if member_id not in ids:
        return []
    if member_id not in choosing and member_id in await join_questions.waiting_entities(
        r.conn, *r.ids
    ):
        return []  # (one whose own join question waits is caught up by answering it)
    # (`choosing`: the characters of one join question, caught up together by "Let me choose":
    # none of them is one of "the others")
    caught_up = await _caught_up(r, ids - {member_id} - set(choosing))
    if len(caught_up) < TELL_FROM:
        return []
    views = {
        str(p["entity_id"]): str(p["id"])
        for p in await r.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [sorted(caught_up)])
    }
    if any(i not in views for i in caught_up):
        return []  # (a member with no point of view holds nothing: nothing is known by all)
    if view is None:
        mine = await r.select(PERSPECTIVES, " AND entity_id = %s", [member_id])
        view = str(mine[0]["id"]) if mine else f"(none:{member_id})"
    return await _inherit_plan(r, [view], set(views.values()), only)


async def tell_counts(r: Scope, only: Mapping[str, Collection[str]]) -> dict[str, int]:
    """For a card's draw: how many things `tell_plan` would give each current member (only those
    with any), with every row read once, never once per member. The same rule as `tell_plan`."""
    from dmbot.memory import join_questions, party_record

    members = await party_record.current_members(r)
    waiting = await join_questions.waiting_entities(r.conn, *r.ids)
    caught = [m for m in members if m not in waiting]
    if len(caught) < TELL_FROM:
        return {}
    views = {
        str(p["entity_id"]): str(p["id"])
        for p in await r.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [sorted(members)])
    }
    where, params = _only_where(only)
    held: dict[tuple[str, str], set[str]] = {}
    plain: dict[tuple[str, str], set[str]] = {}  # (not from the holder's own backstory, #4795)
    asleep: set[tuple[str, str, str]] = set()
    for row in await r.select(
        KNOWERS, " AND cardinality(hidden_by) > 0" + where, params, dormant=True
    ):
        for column in _ONLY_COLUMNS:
            if row[column] is not None:
                asleep.add((column, row[column], row["perspective_id"]))
    for row in await r.select(KNOWERS, where, params):
        for column in _ONLY_COLUMNS:
            if row[column] is not None:
                held.setdefault((column, row[column]), set()).add(row["perspective_id"])
                if row["backstory_of"] is None:
                    plain.setdefault((column, row[column]), set()).add(row["perspective_id"])
    out: dict[str, int] = {}
    for member in caught:
        others = [o for o in caught if o != member]
        if len(others) < TELL_FROM or any(o not in views for o in others):
            continue
        mine, theirs = views.get(member), {views[o] for o in others}
        n = sum(
            1
            for (column, statement), who in held.items()
            if theirs <= who
            and theirs & plain.get((column, statement), set())
            and mine not in who
            and (column, statement, mine or "") not in asleep
        )
        if n:
            out[member] = n
    return out


async def session_statements(r: Scope, started_at: int) -> dict[str, set[str]]:
    """What came up in one session ("Let me choose", by session; Supervisor's ruling G on #4537):
    the entries mentioned in it, the names first heard in it, and the connections and conditions
    dated from it. Lore and what the DM typed have no session, so they are never in one."""
    out: dict[str, set[str]] = {c: set() for c in _ONLY_COLUMNS}
    cur = await r.conn.execute(
        "SELECT DISTINCT entity_id FROM memory_mentions WHERE guild_id = %s AND campaign_id = %s"
        " AND session_started_at = %s",
        (*r.ids, started_at),
    )
    out["about_entity_id"] |= {str(x["entity_id"]) for x in await cur.fetchall()}
    cur = await r.conn.execute(
        "SELECT a.id FROM memory_aliases a JOIN memory_name_sessions n ON n.guild_id = a.guild_id"
        " AND n.campaign_id = a.campaign_id AND n.entity_id = a.entity_id"
        " WHERE a.guild_id = %s AND a.campaign_id = %s AND n.session_started_at = %s",
        (*r.ids, started_at),
    )
    out["alias_id"] |= {str(x["id"]) for x in await cur.fetchall()}
    out["relation_id"] |= {
        str(x["id"]) for x in await r.select(RELATIONS, " AND from_session_at = %s", [started_at])
    }
    out["state_id"] |= {
        str(x["id"]) for x in await r.select(STATES, " AND from_session_at = %s", [started_at])
    }
    return out


async def sessions_statements(r: Scope, starts: Collection[int]) -> dict[int, dict[str, set[str]]]:
    """`session_statements` for many sessions at once (the "Let me choose" page): four reads in
    all, never four per session. Names: only public ones, of an entry first named in that session
    (a name's own session isn't kept, and a secret one is never credited to a session)."""
    out: dict[int, dict[str, set[str]]] = {s: {c: set() for c in _ONLY_COLUMNS} for s in starts}
    if not out:
        return out
    wanted = sorted(out)
    cur = await r.conn.execute(
        "SELECT DISTINCT session_started_at AS at, entity_id FROM memory_mentions"
        " WHERE guild_id = %s AND campaign_id = %s AND session_started_at = ANY(%s)",
        (*r.ids, wanted),
    )
    for x in await cur.fetchall():
        out[int(x["at"])]["about_entity_id"].add(str(x["entity_id"]))
    cur = await r.conn.execute(
        "SELECT n.session_started_at AS at, a.id FROM memory_aliases a"
        " JOIN memory_name_sessions n ON n.guild_id = a.guild_id"
        " AND n.campaign_id = a.campaign_id AND n.entity_id = a.entity_id"
        " WHERE a.guild_id = %s AND a.campaign_id = %s AND n.session_started_at = ANY(%s)"
        " AND NOT a.secret",
        (*r.ids, wanted),
    )
    for x in await cur.fetchall():
        out[int(x["at"])]["alias_id"].add(str(x["id"]))
    for table, column in ((RELATIONS, "relation_id"), (STATES, "state_id")):
        for x in await r.select(table, " AND from_session_at = ANY(%s)", [wanted]):
            out[int(x["from_session_at"])][column].add(str(x["id"]))
    return out


async def cards_statements(r: Scope, entity_ids: Collection[str]) -> dict[str, dict[str, set[str]]]:
    """`card_statements` for many entries at once (the "Let me choose" page's story threads): four
    reads in all."""
    out: dict[str, dict[str, set[str]]] = {e: {c: set() for c in _ONLY_COLUMNS} for e in entity_ids}
    if not out:
        return out
    wanted = sorted(out)
    kinds = {
        str(e["id"]): e["type"] for e in await r.select(ENTITIES, " AND id = ANY(%s)", [wanted])
    }
    for e in list(out):
        if e not in kinds:
            del out[e]
        else:
            out[e]["about_entity_id"].add(e)
    for a in await r.select(ALIASES, " AND entity_id = ANY(%s)", [wanted]):
        if str(a["entity_id"]) in out:
            out[str(a["entity_id"])]["alias_id"].add(str(a["id"]))
    for st in await r.select(STATES, " AND entity_id = ANY(%s)", [wanted]):
        if str(st["entity_id"]) in out:
            out[str(st["entity_id"])]["state_id"].add(str(st["id"]))
    for rel in await r.select(
        RELATIONS, " AND (subject_id = ANY(%s) OR object_id = ANY(%s))", [wanted, wanted]
    ):
        for end, other in (
            (rel["subject_id"], rel["object_id"]),
            (rel["object_id"], rel["subject_id"]),
        ):
            if str(end) in out:
                out[str(end)]["relation_id"].add(str(rel["id"]))
                if kinds[str(end)] in THREAD_TYPES:
                    out[str(end)]["about_entity_id"].add(str(other))
    return out


async def caught_up_others(r: Scope, members: Collection[str]) -> list[str]:
    """The current members a "Let me choose" pick copies from: not the question's own characters,
    and not one whose own question still waits."""
    from dmbot.memory import join_questions, party_record

    waiting = await join_questions.waiting_entities(r.conn, *r.ids)
    return [
        m for m in await party_record.current_members(r) if m not in waiting and m not in members
    ]


async def choose_counts(
    r: Scope, members: Collection[str], groups: Mapping[str, Mapping[str, Collection[str]]]
) -> dict[str, dict[str, int]]:
    """ "Let me choose": for each group of statements (a session's, a story thread's), how many
    things it would give the question's characters (`members`), read once for all groups: what
    every other caught-up member knows, one of them not only from their own backstory (#4795), and
    that character doesn't: group → character → how many, only groups and characters with any."""
    from dmbot.memory import join_questions, party_record

    ids = await party_record.current_members(r)
    waiting = await join_questions.waiting_entities(r.conn, *r.ids)
    mine = [m for m in ids if m in members]
    caught = [m for m in ids if m not in waiting and m not in members]
    if not mine or len(caught) < TELL_FROM:
        return {}
    views = {
        str(p["entity_id"]): str(p["id"])
        for p in await r.select(PERSPECTIVES, " AND entity_id = ANY(%s)", [sorted(ids)])
    }
    if any(o not in views for o in caught):
        return {}
    union: dict[str, set[str]] = {c: set() for c in _ONLY_COLUMNS}
    for only in groups.values():
        for column in _ONLY_COLUMNS:
            union[column] |= set(only.get(column, ()))
    where, params = _only_where(union)
    held: dict[tuple[str, str], set[str]] = {}
    plain: dict[tuple[str, str], set[str]] = {}
    asleep: set[tuple[str, str, str]] = set()
    for row in await r.select(
        KNOWERS, " AND cardinality(hidden_by) > 0" + where, params, dormant=True
    ):
        for column in _ONLY_COLUMNS:
            if row[column] is not None:
                asleep.add((column, row[column], row["perspective_id"]))
    for row in await r.select(KNOWERS, where, params):
        for column in _ONLY_COLUMNS:
            if row[column] is not None:
                held.setdefault((column, row[column]), set()).add(row["perspective_id"])
                if row["backstory_of"] is None:
                    plain.setdefault((column, row[column]), set()).add(row["perspective_id"])
    theirs = {views[o] for o in caught}
    new_views = {m: views.get(m, f"(none:{m})") for m in mine}
    out: dict[str, dict[str, int]] = {}
    for key, only in groups.items():
        each: dict[str, int] = {}
        for column in _ONLY_COLUMNS:
            for statement in only.get(column, ()):
                who = held.get((column, statement), set())
                if not (theirs <= who and theirs & plain.get((column, statement), set())):
                    continue
                for member, v in new_views.items():
                    if v not in who and (column, statement, v) not in asleep:
                        each[member] = each.get(member, 0) + 1
        if each:
            out[key] = each
    return out


async def choose_write(
    w: Changes, members: Sequence[str], only: Mapping[str, Collection[str]]
) -> dict[str, int]:
    """One "Let me choose" pick: each of the question's characters is given what the rest of the
    party knows of `only`, in the caller's change (one Undo). How many things each was given."""
    told: dict[str, int] = {}
    for member in members:
        if not await tell_plan(w, member, only, choosing=members):
            continue
        view = await _view_of(w, member)
        w.player_views = None
        made = await tell_plan(w, member, only, view, choosing=members)
        await w.insert_many(
            KNOWERS, [_row(c, st, v, w.now) | {"confidence": sure} for c, st, v, sure in made]
        )
        if made:
            told[member] = len({(c, st) for c, st, _, _ in made})
    return told


async def tell_member(w: Changes, member_id: str, entity_id: str) -> int:
    """ "Tell Mira" on a card: the rows `tell_plan` names, written in the caller's change (one
    Undo). Returns how many statements Mira was told."""
    only = await card_statements(w, entity_id)
    if not await tell_plan(w, member_id, only):
        return 0
    view = await _view_of(w, member_id)
    w.player_views = None  # (a made view may have changed the list)
    made = await tell_plan(w, member_id, only, view)
    if not made:  # (never a change with nothing in it: it would have no Undo to show)
        return 0
    await w.insert_many(
        KNOWERS, [_row(c, st, v, w.now) | {"confidence": sure} for c, st, v, sure in made]
    )
    return len({(c, st) for c, st, _, _ in made})


async def _shown_by_flags(w: Scope) -> dict[tuple[str, str], float]:
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
        if joined:  # (the party record: each one's stretch opens in the same change, #4537 2a)
            from dmbot.memory import party_record

            for entity_id in joined:
                await party_record.open_span(w, entity_id)
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
        if w.party_join:
            await _ask_about(w, joined)
        else:
            await inherit_table_rows(w, joined)


async def _ask_about(w: Changes, joined: Sequence[str]) -> None:
    """`DMBOT_PARTY_JOIN` on (#4537 slice 2b, rule 13.4): characters who joined a party that
    already had members are asked about; nothing is copied until the DM answers. A campaign's
    first characters have no "before" and are asked nothing (§5): those made before its first
    session, when there is nothing yet to have missed. They are given what the flags show, as
    always (the party's table to copy is only being set up)."""
    from dmbot.memory import join_questions, party_record

    ids = await party_record.current_members(w)
    new = [i for i in dict.fromkeys(joined) if i in ids]
    if not new:
        return
    if set(ids) - set(new) and await party_record.first_session_start(w) is not None:
        await join_questions.ask(w, new)
    else:
        await inherit_table_rows(w, new)


async def catch_up_returner(w: Changes, entity_id: str) -> None:
    """A character came back to the party in this change (#4537 slice 4a, §6). With
    `DMBOT_PARTY_JOIN` on and someone else in the party, the DM is asked what the character knows
    of what happened while away (a BACK question, answered as a join's); nothing is written until
    then (rule 13.4). Alone in the party there is nobody to catch up with, so nothing is asked.
    With the switch off it is given what the rest of the party knows now, as a join is."""
    from dmbot.memory import join_questions, party_record

    if not w.party_join:
        await inherit_table_rows(w, [entity_id])
        return
    if set(await party_record.current_members(w)) - {entity_id}:
        await join_questions.ask(w, [entity_id], join_questions.BACK)


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
    players, not unknown to the world (Supervisor, #3018). Logged, so Undo brings the rows back.
    A character who has left the party keeps what they knew (#4537 §6) and is not at the table to
    be shown it again, so their row is put to sleep instead, marked with the statement itself
    (`hidden_by`, which every reader honours), and `_open` wakes it when it is shown again."""
    views = await _player_views(w)
    gone = sorted(set(await _all_player_views(w)) - set(views))
    if views:
        await w.delete_where_in(KNOWERS, column, ids, views)
    if gone:
        rows = await w.select(
            KNOWERS,
            f" AND {column} = ANY(%s) AND perspective_id = ANY(%s)",
            [list(ids), gone],
            dormant=True,
        )
        await w.update_rows(
            KNOWERS,
            {
                r["id"]: {"hidden_by": _joined(r["hidden_by"], [r[column]])}
                for r in rows
                if r[column] not in r["hidden_by"]
            },
        )


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
    views = await _all_player_views(w)
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
    views = await _all_player_views(w)
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
