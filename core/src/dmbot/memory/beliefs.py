"""Who believes a connection, and how strongly (#2409; docs/STORY_MEMORY.md, section 9).

A connection (`memory_relations`) is already the claim itself. A belief is one perspective's
confidence in it: The truth (the DM's meta-truth), The party (what the players have learned), or
one character. A belief is never edited: a new row is the new belief, and the highest `seq`
for a pair is the current one, so the history stays and an Undo takes the last one away.

Today's words map on: SECRET = The truth believes it and The party has no belief; REVEALED = The
party believes it at 1.0. The `secret` flag stays the writers' switch until every reader moves
over, so `follow` keeps the two beliefs in step with it inside the very write that changed the
connection (one change, one Undo). Only the DM writes The truth: `believe_in` refuses any other
source, and `follow` runs only for a connection that is confirmed, which only the DM can do.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from dmbot.db import Conn
from dmbot.memory import evidence as evidence_links
from dmbot.memory._changes import (
    ALIAS_KNOWERS,
    BELIEFS,
    ENTITIES,
    PERSPECTIVES,
    RELATIONS,
    Changes,
    Scope,
)
from dmbot.memory.evidence import CLAIM, LINE
from dmbot.memory.models import (
    ACCUSATION,
    BELIEF_STATUSES,
    CONFIRMED,
    DM,
    EVASION_UNDER_MAGIC,
    EVIDENCE_ID_MAX,
    EVIDENCE_MAX,
    FALSE_EVIDENCE,
    KNOWN,
    MAGICALLY_VERIFIED,
    PARTY,
    PC_VIEW,
    REVEALED_FACT,
    SECRET_PLOT,
    THEORY,
    TRUTH,
    Belief,
    MemoryRuleError,
    new_id,
)

ONLY_THE_DM = "Only the DM can write what is true."
NOT_A_REVEAL = "Show or hide the connection itself to change what the players know."
NOT_THERE = "DMbot doesn't remember that any more."


def belief_of(row: dict[str, Any]) -> Belief:
    return Belief(
        row["id"], row["perspective_id"], row["relation_id"], float(row["confidence"]),
        row["status"], row["session_at"], tuple(row["evidence_ids"]), int(row["seq"]),
        row["recorded_at"], bool(row.get("pinned", False)),
    )  # fmt: skip


async def perspective_ids(w: Changes) -> dict[str, str]:
    """The ids of The truth and The party. Every campaign has them from the day it is made (and
    the migration gave them to the ones that existed), so they are never made inside a change a
    DM could undo. A campaign that somehow lacks them (made by hand in a test) gets them here with
    plain statements, which the change log does not see."""
    if w.perspectives is None:
        found = {
            p["kind"]: p["id"]
            for p in await w.select(PERSPECTIVES, " AND kind IN ('truth', 'party')")
        }
        for kind in (TRUTH, PARTY):
            if kind not in found:
                found[kind] = new_id()
                await w.conn.execute(
                    "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (*w.ids, found[kind], kind, w.now),
                )
        w.perspectives = found
    return w.perspectives


async def _newest(
    scope: Scope, relation_ids: Sequence[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """The current belief of each (relation, perspective) pair among these connections."""
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    if not relation_ids:
        return newest
    for row in await scope.select(
        BELIEFS, " AND relation_id = ANY(%s) ORDER BY seq", [list(relation_ids)]
    ):
        newest[(row["relation_id"], row["perspective_id"])] = row  # (later rows replace earlier)
    return newest


async def _add(
    w: Changes,
    perspective_id: str,
    relation_id: str,
    confidence: float,
    status: str,
    evidence: Sequence[str] = (),
    session_at: int | None = None,
    pinned: bool | None = None,
    copy_from: str | None = None,
) -> dict[str, Any]:
    """The checked insert behind `believe_in` and `follow`. `pinned` (the DM's 📌, #2447): None
    keeps what the pair's last belief said, so a new belief never silently unpins.
    `copy_from`: the id of an earlier belief of this campaign whose evidence links this one
    carries on (a placeholder stays a placeholder); `evidence` is then that belief's old list.
    Without it `evidence` is new and each item must be a claim or line of this campaign (#2662)."""
    if not 0.0 <= confidence <= 1.0:
        raise MemoryRuleError("A confidence runs from 0 to 1.")
    if status not in BELIEF_STATUSES:
        raise MemoryRuleError("That is not a kind of belief.")
    if len(evidence) > EVIDENCE_MAX or any(not e or len(e) > EVIDENCE_ID_MAX for e in evidence):
        raise MemoryRuleError("That evidence is not in the right shape.")
    kinds: dict[str, str] = {}
    if copy_from is None:  # new evidence: each item a claim or a line of THIS campaign, or refused
        for item in dict.fromkeys(evidence):
            for kind in (CLAIM, LINE):
                if await evidence_links.resolves(w.conn, w.guild_id, w.campaign_id, kind, item):
                    kinds[item] = kind
                    break
            else:
                raise MemoryRuleError(evidence_links.NOT_THIS_CAMPAIGN)
    last = await w.select(
        BELIEFS,
        " AND perspective_id = %s AND relation_id = %s ORDER BY seq DESC LIMIT 1",
        [perspective_id, relation_id],
    )
    # the version its own log row takes, so newer rows sort later; one past the pair's last when
    # that is higher (a restored backup brings rows numbered by another campaign's history)
    seq = max(w.version + 1, int(last[0]["seq"]) + 1 if last else 0)
    row = await w.insert(
        BELIEFS,
        {
            "id": new_id(),
            "perspective_id": perspective_id,
            "relation_id": relation_id,
            "confidence": float(confidence),
            "status": status,
            "session_at": session_at,
            "evidence_ids": list(evidence),
            "seq": seq,
            "recorded_at": w.now,
            "pinned": bool(last[0]["pinned"]) if pinned is None and last else bool(pinned),
        },
    )
    if copy_from is not None:
        await w.conn.execute(
            "INSERT INTO memory_evidence (guild_id, campaign_id, belief_id, kind, target_id,"
            " session_started_at, added_at)"
            " SELECT guild_id, campaign_id, %s, kind, target_id, session_started_at, added_at"
            " FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s AND belief_id = %s"
            " ON CONFLICT DO NOTHING",
            (row["id"], *w.ids, copy_from),
        )
    for item, kind in kinds.items():
        await evidence_links.add_in(
            w, kind=kind, target_id=item, belief_id=row["id"],
            session_started_at=session_at, trusted=True,
        )  # fmt: skip
    return row


async def add_belief(
    w: Changes, perspective_id: str, relation_id: str, confidence: float, status: str
) -> None:
    """A belief of a character's point of view, written by the DM's "Who knows this?" (#2935)
    through `memory.knowers`: the one place that writes a player character's certain belief. A
    new row, so the history stays and Undo works; The truth and The party are not written here."""
    await _add(w, perspective_id, relation_id, confidence, status)


async def set_pin(w: Changes, relation_id: str, pinned: bool) -> Belief:
    """The DM's 📌 on a connection (#2447): The truth believes it at 1.0, held as the DM's word.
    A new belief row, so the change log has it and Undo takes it back. Only the DM, and only a
    confirmed connection. Unpinning clears the flag and leaves the belief as it is."""
    if w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    relation = await w.get(RELATIONS, relation_id)
    if relation is None or relation["status"] != CONFIRMED:
        raise MemoryRuleError(NOT_THERE)
    ids = await perspective_ids(w)
    last = await _newest(w, [relation_id])
    truth = last.get((relation_id, ids[TRUTH]))
    held = bool(relation["secret"] or relation["unrevealed"])
    if truth is None:
        status, session_at, evidence = (SECRET_PLOT if held else REVEALED_FACT), None, []
    else:
        status, session_at, evidence = truth["status"], truth["session_at"], truth["evidence_ids"]
    if pinned:
        if truth is not None and bool(truth["pinned"]):
            return belief_of(truth)  # already pinned: nothing to write
        if status not in (SECRET_PLOT, REVEALED_FACT, MAGICALLY_VERIFIED):
            status = SECRET_PLOT if held else REVEALED_FACT  # a doubt or a lie is not 100% truth
        row = await _add(
            w,
            ids[TRUTH],
            relation_id,
            KNOWN,
            status,
            evidence,
            session_at,
            pinned=True,
            copy_from=truth["id"] if truth is not None else None,
        )
    else:
        if truth is None or not bool(truth["pinned"]):
            raise MemoryRuleError("That connection isn't pinned.")
        row = await _add(
            w, ids[TRUTH], relation_id, float(truth["confidence"]), status, evidence,
            session_at, pinned=False, copy_from=truth["id"],
        )  # fmt: skip
    return belief_of(row)


async def believe_in(
    w: Changes,
    perspective_id: str,
    relation_id: str,
    confidence: float,
    status: str,
    *,
    evidence: Sequence[str] = (),
    session_at: int | None = None,
) -> Belief:
    """A perspective comes to believe a connection (or stops, at 0.0), inside an open write. Both
    ends must be in this campaign; only the DM writes The truth."""
    perspective = await w.get(PERSPECTIVES, perspective_id)
    relation = await w.get(RELATIONS, relation_id)
    if perspective is None or relation is None:
        raise MemoryRuleError(NOT_THERE)
    if perspective["kind"] == TRUTH and w.source != DM:
        raise MemoryRuleError(ONLY_THE_DM)
    if perspective["kind"] == PARTY and (
        confidence < KNOWN
        or relation["secret"]
        or relation["unrevealed"]
        or relation["status"] != CONFIRMED
    ):
        # Until guesses arrive (#2412, with their own rules for readers), what the players hold is
        # exactly the connections shown to them: a reveal or a hide goes through the connection's
        # own switch, and a guess is not recorded here.
        raise MemoryRuleError(NOT_A_REVEAL)
    return belief_of(
        await _add(w, perspective_id, relation_id, confidence, status, evidence, session_at)
    )


BAD_READING = "That is not a belief DMbot can place."
ALREADY_KNOWN = "The players already know that, so a guess about it adds nothing."
SECRET_FIRST = "That connection is secret. Show it to the players first."


async def place_belief(
    w: Changes,
    perspective_id: str,
    relation_id: str,
    confidence: float,
    status: str,
    *,
    evidence: Sequence[str] = (),
    session_at: int | None = None,
) -> Belief:
    """A belief the DM placed from a reading (#2412), inside an open write. `believe_in` is for
    the DM's own beliefs and refuses a guess on the party's view; this is the one door a reading
    comes through, so it holds each view to the kind of belief it may have:
    - **The truth** (the DM's alone): a false public statement, 0.0 False Evidence.
    - **The party**: a guess or an accusation or a dodge under a spell (above 0 and below 1) on
      any connection; or, at 1.0, a Revealed Fact or a Magically Verified word on a connection that
      is confirmed and shown (a secret one is revealed first, which is the DM's own step).
    - **A character** (a player's or an NPC): a guess or an accusation, above 0 and below 1.
    The relation it hangs on is whatever the caller found; this never changes it."""
    perspective = await w.get(PERSPECTIVES, perspective_id)
    relation = await w.get(RELATIONS, relation_id)
    if perspective is None or relation is None:
        raise MemoryRuleError(NOT_THERE)
    kind = perspective["kind"]
    guess = (THEORY, ACCUSATION)
    if kind == TRUTH:
        if w.source != DM:
            raise MemoryRuleError(ONLY_THE_DM)
        ok = status == FALSE_EVIDENCE and confidence == 0.0
    elif kind == PARTY:
        if status in (REVEALED_FACT, MAGICALLY_VERIFIED):
            if confidence != KNOWN:
                raise MemoryRuleError(BAD_READING)
            if relation["status"] != CONFIRMED:
                raise MemoryRuleError(NOT_A_REVEAL)
            if relation["secret"] or relation["unrevealed"]:
                raise MemoryRuleError(SECRET_FIRST)
            ok = True
        else:
            ok = status in (*guess, EVASION_UNDER_MAGIC) and 0.0 < confidence < KNOWN
            held = (await _newest(w, [relation_id])).get((relation_id, perspective_id))
            if ok and held is not None and float(held["confidence"]) >= KNOWN:
                # (the players' readers take the party's newest belief, so a guess would hide a
                # connection they already know)
                raise MemoryRuleError(ALREADY_KNOWN)
    else:
        ok = status in guess and 0.0 < confidence < KNOWN
    if not ok:
        raise MemoryRuleError(BAD_READING)
    return belief_of(
        await _add(w, perspective_id, relation_id, confidence, status, evidence, session_at)
    )


async def follow(w: Changes, relation: dict[str, Any]) -> None:
    """A connection was written: bring The truth's and The party's belief in line with its status
    and secret flag. Only a confirmed connection has beliefs. A secret one is believed by The
    truth alone (a party belief that was there is ended with 0.0); a known one by both at 1.0.
    Nothing is written when the beliefs already say so, so this is cheap to call."""
    if relation["status"] != CONFIRMED:
        return
    ids = await perspective_ids(w)
    newest = await _newest(w, [relation["id"]])
    truth = newest.get((relation["id"], ids[TRUTH]))
    party = newest.get((relation["id"], ids[PARTY]))
    # A connection the story is holding back (#2404) is kept from the party exactly like a secret
    # one: the players' readers see only what The party believes, so no reader had to change.
    secret = bool(relation["secret"]) or bool(relation.get("unrevealed", False))
    truth_status = SECRET_PLOT if secret else REVEALED_FACT
    # The truth is written from the secret flag only while it still says what the flag said (a
    # Secret Plot or a Revealed Fact): a belief the DM set another way (False Evidence, a
    # Magically Verified word, a confidence short of 1.0) is left as the DM made it.
    if truth is None or (
        truth["status"] in (SECRET_PLOT, REVEALED_FACT) and truth["status"] != truth_status
    ):
        await _add(w, ids[TRUTH], relation["id"], KNOWN, truth_status)
    party_knows = party is not None and party["confidence"] >= KNOWN
    if secret and party_knows:
        await _add(w, ids[PARTY], relation["id"], 0.0, SECRET_PLOT)
    elif not secret and not party_knows:
        await _add(w, ids[PARTY], relation["id"], KNOWN, REVEALED_FACT)


async def backfill(conn: Conn, guild_id: int, campaign_id: str) -> None:
    """Give a campaign the beliefs it lacks: The truth and The party, and for every confirmed
    connection with none, The truth at 1.0 and, when it is not secret, The party at 1.0. The
    migration does the same for every campaign; this is for one restored from a backup made before
    beliefs existed, or with a connection its file gave no beliefs (a belief already there is left
    alone, so a file that has them loses nothing). Plain
    scoped statements: the restore's transaction already has the server set."""
    now = int(time.time())
    await conn.execute(
        "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)"
        " SELECT %s, %s, md5(%s::text || k.kind || clock_timestamp()::text), k.kind, %s::bigint"
        "   FROM (VALUES ('truth'), ('party')) AS k(kind)"
        " ON CONFLICT DO NOTHING",
        (guild_id, campaign_id, campaign_id, now),
    )
    scope = (guild_id, campaign_id)
    await conn.execute(  # The truth believes every confirmed connection; a secret one as a plot
        "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,"
        " confidence, status, session_at, evidence_ids, seq, recorded_at)"
        " SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'truth'), p.id, r.id, 1.0,"
        "        CASE WHEN r.secret OR r.unrevealed THEN 'secret_plot'"
        "             ELSE 'revealed_fact' END,"
        "        NULL, '{}', 0, %s::bigint"
        "   FROM memory_relations r"
        "   JOIN memory_perspectives p ON p.guild_id = r.guild_id"
        "    AND p.campaign_id = r.campaign_id AND p.kind = 'truth'"
        "  WHERE r.guild_id = %s AND r.campaign_id = %s AND r.status = 'confirmed'"
        "    AND NOT EXISTS (SELECT 1 FROM memory_beliefs b WHERE b.guild_id = r.guild_id"
        "     AND b.campaign_id = r.campaign_id AND b.relation_id = r.id"
        "     AND b.perspective_id = p.id)",
        (now, *scope),
    )
    await conn.execute(  # The party believes the ones that are not secret
        "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,"
        " confidence, status, session_at, evidence_ids, seq, recorded_at)"
        " SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'party'), p.id, r.id, 1.0,"
        "        'revealed_fact', NULL, '{}', 0, %s::bigint"
        "   FROM memory_relations r"
        "   JOIN memory_perspectives p ON p.guild_id = r.guild_id"
        "    AND p.campaign_id = r.campaign_id AND p.kind = 'party'"
        "  WHERE r.guild_id = %s AND r.campaign_id = %s AND r.status = 'confirmed'"
        "    AND NOT r.secret AND NOT r.unrevealed"
        "    AND NOT EXISTS (SELECT 1 FROM memory_beliefs b WHERE b.guild_id = r.guild_id"
        "     AND b.campaign_id = r.campaign_id AND b.relation_id = r.id"
        "     AND b.perspective_id = p.id)",
        (now, *scope),
    )


async def carry(
    w: Changes,
    old: dict[str, Any],
    new_id: str,
    *,
    copy_beliefs: bool = True,
    skip_players: bool = False,
) -> None:
    """A reworded connection (#2408) is the same claim in other words, so the new one takes over
    what the old one held: each perspective's current belief (what it thought, how sure, the
    evidence and the session), the mentions it points to and its session span. Beliefs are copied
    as new rows (history is kept on the old connection, which is ended). The players are never
    handed a belief in a secret connection this way. `skip_players`: copy every point of view's
    belief except the player characters' (a merge folding a fact the players were shown into a twin
    they were not shown)."""
    new = await w.get(RELATIONS, new_id)
    if new is None:
        return
    changes: dict[str, Any] = {}
    if old["mention_ids"] and not new["mention_ids"]:
        changes["mention_ids"] = list(old["mention_ids"])
    for column in ("from_session_at", "to_session_at"):
        if old[column] is not None and new[column] is None:
            changes[column] = old[column]
    if changes:
        new = await w.update(RELATIONS, new_id, changes)
    await w.conn.execute(  # and what the old one stood on (#2662), unless the new one has its own
        "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, kind, target_id,"
        " session_started_at, added_at)"
        " SELECT guild_id, campaign_id, %s, kind, target_id, session_started_at, added_at"
        " FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s AND relation_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM memory_evidence n WHERE n.guild_id = %s"
        "   AND n.campaign_id = %s AND n.relation_id = %s)"
        " ON CONFLICT DO NOTHING",
        (new_id, *w.ids, old["id"], *w.ids, new_id),
    )
    if not copy_beliefs:  # a join: the twin keeps its own flag and what is believed of it
        return
    ids = await perspective_ids(w)
    party = ids[PARTY]
    pc_views = (
        {str(p["id"]) for p in await w.select(PERSPECTIVES, " AND kind = %s", [PC_VIEW])}
        if skip_players
        else set()
    )
    held = await _newest(w, [old["id"], new_id])
    for (relation_id, perspective_id), belief in sorted(held.items(), key=lambda kv: kv[1]["seq"]):
        if relation_id != old["id"] or perspective_id in pc_views:
            continue
        mine = held.get((new_id, perspective_id))

        def shape(row: dict[str, Any]) -> tuple[Any, ...]:
            return (
                float(row["confidence"]),
                row["status"],
                list(row["evidence_ids"]),
                row["session_at"],
                bool(row["pinned"]),
            )

        same = mine is not None and shape(mine) == shape(belief)
        if same:
            continue
        if perspective_id == party and (new["secret"] or new["unrevealed"]):
            continue  # (the flag decides what the players know; see `follow`)
        if (
            perspective_id == party
            and mine is not None
            and float(mine["confidence"]) >= KNOWN > float(belief["confidence"])
        ):
            continue  # (a guess never hides what the players already know)
        await _add(
            w, perspective_id, new_id, float(belief["confidence"]), belief["status"],
            list(belief["evidence_ids"]), belief["session_at"], pinned=bool(belief["pinned"]),
            copy_from=belief["id"],
        )  # fmt: skip


def party_knows_by(table: str, party_id: str | None) -> str:
    """`party_knows` for a hot read: with The party's id in hand (`party_id`, one cheap read for
    the whole query) the current belief is one probe of the `memory_beliefs_current` index for
    each connection, instead of the join and the max() subquery. The id is ours (32 hex digits,
    checked here) so it is written into the SQL; None, no party yet, knows nothing."""
    if party_id is None:
        return "false"
    if len(party_id) != 32 or any(c not in "0123456789abcdef" for c in party_id):
        raise ValueError("Bad perspective ID")
    return (
        "EXISTS (SELECT 1 FROM (SELECT pb.confidence FROM memory_beliefs pb"
        f" WHERE pb.guild_id = {table}.guild_id AND pb.campaign_id = {table}.campaign_id"
        f" AND pb.relation_id = {table}.id AND pb.perspective_id = '{party_id}'"
        " ORDER BY pb.seq DESC LIMIT 1) now WHERE now.confidence >= 1.0)"
    )


async def party_id(scope: Scope) -> str | None:
    """The id of this campaign's party perspective, or None if it has none."""
    rows = await scope.select(PERSPECTIVES, " AND kind = 'party' LIMIT 1")
    return str(rows[0]["id"]) if rows else None


def party_knows(table: str = "memory_relations") -> str:
    """SQL for "The party holds this connection as fact": its current party belief is 1.0. The
    readers that show the players the story use it where they used to test the `secret` flag, so
    they read what The party believes (#2409); until guesses arrive the two always agree (`follow`
    keeps them so, and a test holds the readers to it). `table` is the connections' table or alias
    in the query; the SQL reads that table's own guild, campaign and id. Whoever inserts confirmed
    connections by raw SQL (a migration, a test) must write their beliefs too (`backfill`), or the
    players do not see them."""
    return (
        "EXISTS (SELECT 1 FROM memory_beliefs pb JOIN memory_perspectives pp"
        " ON pp.guild_id = pb.guild_id AND pp.campaign_id = pb.campaign_id"
        " AND pp.id = pb.perspective_id"
        f" WHERE pp.kind = 'party' AND pb.guild_id = {table}.guild_id"
        f" AND pb.campaign_id = {table}.campaign_id AND pb.relation_id = {table}.id"
        " AND pb.confidence >= 1.0 AND pb.seq = (SELECT max(x.seq) FROM memory_beliefs x"
        "  WHERE x.guild_id = pb.guild_id AND x.campaign_id = pb.campaign_id"
        "  AND x.perspective_id = pb.perspective_id AND x.relation_id = pb.relation_id))"
    )


async def fold_perspective(w: Changes, keep_id: str, gone_id: str) -> tuple[str, str] | None:
    """A merged-away character's beliefs become the kept entry's (#2438, #2433). The kept entry
    gets a perspective if it had none, and every belief of the other entry's moves onto it. History
    is kept, so a pair both believed has both rows and the newest (highest `seq`) is the one read.
    The beliefs move through the change log, so Undo puts them back; the emptied perspective stays
    on the merged-away entry (perspectives are not logged: an Undo never takes one from beliefs
    resting on it)."""
    gone_p = await w.select(PERSPECTIVES, " AND entity_id = %s", [gone_id])
    if not gone_p:
        return None
    keep_p = await w.select(PERSPECTIVES, " AND entity_id = %s", [keep_id])
    if keep_p:
        target = keep_p[0]["id"]
    else:
        target = new_id()
        kept = await w.get(ENTITIES, keep_id)
        # (the kept entry's own kind: a player's character holds beliefs as one, anything else
        # as an NPC; the merged one's kind is not copied)
        kind = "pc" if kept is not None and kept["played_by"] is not None else "npc"
        await w.insert(
            PERSPECTIVES, {"id": target, "kind": kind, "entity_id": keep_id, "created_at": w.now}
        )
    await w.update_where(
        BELIEFS, {"perspective_id": target}, " AND perspective_id = %s", [gone_p[0]["id"]]
    )
    # Who knows an other name moves too (#2935); a name both knew keeps one row.
    have = {
        r["alias_id"] for r in await w.select(ALIAS_KNOWERS, " AND perspective_id = %s", [target])
    }
    for row in await w.select(ALIAS_KNOWERS, " AND perspective_id = %s", [gone_p[0]["id"]]):
        if row["alias_id"] in have:
            await w.delete(ALIAS_KNOWERS, row["id"])
    await w.update_where(
        ALIAS_KNOWERS, {"perspective_id": target}, " AND perspective_id = %s", [gone_p[0]["id"]]
    )
    return str(gone_p[0]["id"]), str(target)  # (who knows what folds with it: #2995)
