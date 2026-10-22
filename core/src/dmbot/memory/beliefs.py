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
from dmbot.memory._changes import BELIEFS, PERSPECTIVES, RELATIONS, Changes, Scope
from dmbot.memory.models import (
    BELIEF_STATUSES,
    CONFIRMED,
    DM,
    EVIDENCE_ID_MAX,
    EVIDENCE_MAX,
    KNOWN,
    PARTY,
    REVEALED_FACT,
    SECRET_PLOT,
    TRUTH,
    Belief,
    MemoryRuleError,
    new_id,
)

ONLY_THE_DM = "Only the DM can write what is true."
NOT_A_REVEAL = "That is a guess, not a reveal: show the connection to the players to reveal it."
NOT_THERE = "DMbot doesn't remember that any more."


def belief_of(row: dict[str, Any]) -> Belief:
    return Belief(
        row["id"], row["perspective_id"], row["relation_id"], float(row["confidence"]),
        row["status"], row["session_at"], tuple(row["evidence_ids"]), int(row["seq"]),
        row["recorded_at"],
    )  # fmt: skip


async def perspective_ids(w: Changes) -> dict[str, str]:
    """The ids of The truth and The party, made on first use (a campaign made after the
    migration has none until its first confirmed connection)."""
    if w.perspectives is None:
        found = {
            p["kind"]: p["id"]
            for p in await w.select(PERSPECTIVES, " AND kind IN ('truth', 'party')")
        }
        for kind in (TRUTH, PARTY):
            if kind not in found:
                row = await w.insert(
                    PERSPECTIVES,
                    {"id": new_id(), "kind": kind, "entity_id": None, "created_at": w.now},
                )
                found[kind] = row["id"]
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
) -> dict[str, Any]:
    """The checked insert behind `believe_in` and `follow`."""
    if not 0.0 <= confidence <= 1.0:
        raise MemoryRuleError("A confidence runs from 0 to 1.")
    if status not in BELIEF_STATUSES:
        raise MemoryRuleError("That is not a kind of belief.")
    if len(evidence) > EVIDENCE_MAX or any(not e or len(e) > EVIDENCE_ID_MAX for e in evidence):
        raise MemoryRuleError("That evidence is not in the right shape.")
    last = await w.select(
        BELIEFS,
        " AND perspective_id = %s AND relation_id = %s ORDER BY seq DESC LIMIT 1",
        [perspective_id, relation_id],
    )
    # the version its own log row takes, so newer rows sort later; one past the pair's last when
    # that is higher (a restored backup brings rows numbered by another campaign's history)
    seq = max(w.version + 1, int(last[0]["seq"]) + 1 if last else 0)
    return await w.insert(
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
        },
    )


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
    if (
        perspective["kind"] == PARTY
        and confidence >= KNOWN
        and (relation["secret"] or relation["status"] != CONFIRMED)
    ):
        # a secret (or unconfirmed) connection is never held by the players as fact; a theory about
        # one is fine (the players guessed), a reveal goes through the connection's own switch
        raise MemoryRuleError(NOT_A_REVEAL)
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
    secret = bool(relation["secret"])
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
    alone, so a file that has them loses nothing). A campaign with no confirmed connection
    gets nothing: its perspectives are made with its first one, as for any new campaign. Plain
    scoped statements: the restore's transaction already has the server set."""
    now = int(time.time())
    cur = await conn.execute(
        "SELECT 1 FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
        " AND status = 'confirmed' LIMIT 1",
        (guild_id, campaign_id),
    )
    if await cur.fetchone() is None:
        return
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
        "        CASE WHEN r.secret THEN 'secret_plot' ELSE 'revealed_fact' END,"
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
        "    AND NOT r.secret"
        "    AND NOT EXISTS (SELECT 1 FROM memory_beliefs b WHERE b.guild_id = r.guild_id"
        "     AND b.campaign_id = r.campaign_id AND b.relation_id = r.id"
        "     AND b.perspective_id = p.id)",
        (now, *scope),
    )
