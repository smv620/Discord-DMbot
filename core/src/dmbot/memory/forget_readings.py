"""Stop recording me, for the readings the DM placed (#2412; docs/STORY_MEMORY.md, 9a).

The same line as for claims: what the DM kept stays, and what only a person's words produced goes.
A placed guess, accusation or dodge under a spell is not a fact, so when the person stops being
recorded (or their transcripts are deleted) these go: the claim, the belief it created (a belief
that also stands on other evidence, another speaker's claim or a line of the DM's, stays and loses
only the link) and the connection the placement made just to hold it, if nothing else is believed
of it. What the DM kept of a reading (a reveal or confession shown to the players, a false clue, a
plot) stays, its link to the person's words a placeholder.

Both ways of forgetting a person (`ClaimStore.forget_speaker` and the transcript store's
`delete_person`) call this, on their own connection and transaction. Each campaign is handled under
its row lock, the one every memory write takes (`MemoryStore._write`), so a placement in progress
cannot add a belief this then deletes behind its back, and every delete is pinned to its campaign.
The deleted rows also leave the undo log, which would otherwise keep their words and let an Undo
run against rows that are gone.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory import evidence

# the readings that are not the DM's word (a suspected lie is an accusation too, #3091)
GUESSES = ("theory", "accusation", "suspected_lie", "evasion_under_magic")


async def forget(conn: Any, guild_id: int, user_id: int) -> int:
    """Forget the placed readings this person's words produced. Returns how many claims went."""
    # The campaigns first, then each one's lock (the one every memory write takes, held to the end
    # of this transaction), and only then the claims: a placement that was committing when this
    # started has finished by the time the lock is ours, so it is seen as the placed claim it is.
    cur = await conn.execute(
        "SELECT DISTINCT campaign_id FROM memory_claims WHERE guild_id = %s"
        " AND %s = ANY(speaker_ids) AND reading IS NOT NULL",
        (guild_id, user_id),
    )
    campaigns = sorted(str(r["campaign_id"]) for r in await cur.fetchall())
    guesses: dict[str, list[str]] = {}
    kept: list[str] = []
    gone = 0
    for campaign_id in campaigns:
        scope = (guild_id, campaign_id)
        await conn.execute(
            "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR NO KEY UPDATE", scope
        )
        cur = await conn.execute(
            "SELECT id, reading->>'status' AS status FROM memory_claims"
            " WHERE guild_id = %s AND campaign_id = %s AND %s = ANY(speaker_ids)"
            " AND status = 'confirmed' AND reading IS NOT NULL",
            (*scope, user_id),
        )
        ids: list[str] = []
        for row in await cur.fetchall():
            if row["status"] in GUESSES:
                ids.append(str(row["id"]))
            else:
                kept.append(str(row["id"]))
        if not ids:
            continue
        guesses[campaign_id] = ids
        cur = await conn.execute(
            "DELETE FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s"
            " AND status = ANY(%s) AND cardinality(evidence_ids) > 0 AND evidence_ids <@ %s::text[]"
            " RETURNING id",
            (*scope, list(GUESSES), ids),
        )
        beliefs = [str(r["id"]) for r in await cur.fetchall()]
        cur = await conn.execute(
            # only a connection a placement made, whose claims are all gone or going: it has
            # claim links, none of them to a claim that stays, and nothing is believed of it
            "DELETE FROM memory_relations r WHERE r.guild_id = %s AND r.campaign_id = %s"
            " AND r.status = 'proposed' AND r.secret AND r.source = 'extractor'"
            " AND EXISTS (SELECT 1 FROM memory_evidence e WHERE e.guild_id = r.guild_id"
            "   AND e.campaign_id = r.campaign_id AND e.relation_id = r.id AND e.kind = 'claim')"
            " AND NOT EXISTS (SELECT 1 FROM memory_evidence e WHERE e.guild_id = r.guild_id"
            "   AND e.campaign_id = r.campaign_id AND e.relation_id = r.id AND e.kind = 'claim'"
            "   AND e.target_id IS NOT NULL AND NOT e.target_id = ANY(%s))"
            " AND NOT EXISTS (SELECT 1 FROM memory_beliefs b WHERE b.guild_id = r.guild_id"
            "   AND b.campaign_id = r.campaign_id AND b.relation_id = r.id)"
            " RETURNING r.id",
            (*scope, ids),
        )
        relations = [str(r["id"]) for r in await cur.fetchall()]
        await conn.execute(
            "DELETE FROM memory_claims WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)",
            (*scope, ids),
        )
        for table, rows in (
            ("memory_beliefs", beliefs),
            ("memory_relations", relations),
            ("memory_claims", ids),
        ):
            await conn.execute(
                "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                " AND table_name = %s AND row_id = ANY(%s)",
                (*scope, table, rows),
            )
        await conn.execute(  # readers keep a copy until the version changes
            "UPDATE campaigns SET memory_version = memory_version + 1"
            " WHERE guild_id = %s AND id = %s",
            scope,
        )
        gone += len(ids)
    every = [i for ids in guesses.values() for i in ids] + kept
    await evidence.placeholder_claims(conn, guild_id, every)  # their links keep no target
    return gone
