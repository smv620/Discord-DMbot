"""0235_memory_evidence

Typed evidence links with placeholders (#2662).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

MEMORY_EVIDENCE = (
    """
    -- Typed evidence links (#2662, docs/STORY_MEMORY.md 9a, "Evidence links"): what a connection
    -- or a belief stands on, one row for each thing it points at, in place of the untyped lists
    -- `memory_relations.mention_ids` and `memory_beliefs.evidence_ids`. A link is a claim, a
    -- transcript line, a clue or an event (`kind`), the thing's id (`target_id`) and the session
    -- it belongs to. The campaign's composite keys keep a link inside its campaign, to a row of
    -- that campaign: a link to a connection or belief of another campaign cannot be made.
    -- When a person's words are removed (Stop recording me, Delete my past transcripts, the
    -- retention setting, account deletion) the link of a line or claim becomes a PLACEHOLDER:
    -- `target_id` is cleared and the kind and session stay, so "a line from session 12, no longer
    -- available" can still be said, with no words and no speaker. A line's target is its
    -- reference ("session:started_ms:user"), which names the speaker, so clearing it is what
    -- keeps the speaker out. A link never gives access to what it points at: it is read only
    -- through the door that already shows the connection or belief. Unlogged (an Undo does not
    -- bring a link back; a connection that Undo restores has its links in the old list until
    -- that is dropped), and deleted with its connection or belief. Its foreign keys follow a
    -- campaign that moves to another server (ON UPDATE CASCADE).
    CREATE TABLE memory_evidence (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        id                 TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$'),
        relation_id        TEXT,
        belief_id          TEXT,
        kind               TEXT NOT NULL CHECK (kind IN ('claim', 'line', 'clue', 'event')),
        target_id          TEXT CHECK (char_length(target_id) BETWEEN 1 AND 100),
        session_started_at BIGINT,
        added_at           BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((relation_id IS NOT NULL) <> (belief_id IS NOT NULL)),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, belief_id)
            REFERENCES memory_beliefs (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_evidence_by_relation
        ON memory_evidence (guild_id, campaign_id, relation_id) WHERE relation_id IS NOT NULL;
    CREATE INDEX memory_evidence_by_belief
        ON memory_evidence (guild_id, campaign_id, belief_id) WHERE belief_id IS NOT NULL;
    CREATE INDEX memory_evidence_by_target
        ON memory_evidence (guild_id, kind, target_id) WHERE target_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_evidence_once_for_relation
        ON memory_evidence (guild_id, campaign_id, relation_id, kind, target_id)
        WHERE relation_id IS NOT NULL AND target_id IS NOT NULL;
    CREATE UNIQUE INDEX memory_evidence_once_for_belief
        ON memory_evidence (guild_id, campaign_id, belief_id, kind, target_id)
        WHERE belief_id IS NOT NULL AND target_id IS NOT NULL;
    """
    + _isolate("memory_evidence")
    + """
    -- The old lists, copied in. Migrations run with no server set, so row-level security hides
    -- every row: open EVERY table the statements read or write, for this migration alone
    -- (CLAUDE.md: a plain INSERT ... SELECT would match nothing).
    CREATE POLICY migrate_backfill ON memory_evidence FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_beliefs FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_mentions FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_claims FOR SELECT USING (true);
    -- A connection's mentions are lines: the link points at the line the mention names (a
    -- placeholder when the mention is already gone, which is what it would have become).
    INSERT INTO memory_evidence
            (guild_id, campaign_id, relation_id, kind, target_id, session_started_at, added_at)
        SELECT DISTINCT ON (r.guild_id, r.campaign_id, r.id, t.line_ref)
               r.guild_id, r.campaign_id, r.id, 'line', t.line_ref, t.session_started_at,
               r.created_at
        FROM memory_relations r
        CROSS JOIN LATERAL unnest(r.mention_ids) AS ids(mention_id)
        LEFT JOIN memory_mentions m ON m.guild_id = r.guild_id AND m.campaign_id = r.campaign_id
                                   AND m.id = ids.mention_id
        CROSS JOIN LATERAL (SELECT CASE WHEN char_length(m.line_ref) <= 100
                                        THEN m.line_ref END AS line_ref,
                                   m.session_started_at AS session_started_at) t
        ORDER BY r.guild_id, r.campaign_id, r.id, t.line_ref, t.session_started_at;
    -- A belief's evidence ids were never typed: one that is a claim of the campaign is a claim,
    -- anything else is taken to be a line.
    INSERT INTO memory_evidence
            (guild_id, campaign_id, belief_id, kind, target_id, session_started_at, added_at)
        SELECT DISTINCT b.guild_id, b.campaign_id, b.id,
               CASE WHEN c.id IS NULL THEN 'line' ELSE 'claim' END, ids.evidence_id,
               b.session_at, b.recorded_at
        FROM memory_beliefs b
        CROSS JOIN LATERAL unnest(b.evidence_ids) AS ids(evidence_id)
        LEFT JOIN memory_claims c ON c.guild_id = b.guild_id AND c.campaign_id = b.campaign_id
                                 AND c.id = ids.evidence_id
        WHERE char_length(ids.evidence_id) BETWEEN 1 AND 100;
    DROP POLICY migrate_backfill ON memory_claims;
    DROP POLICY migrate_backfill ON memory_mentions;
    DROP POLICY migrate_backfill ON memory_beliefs;
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_evidence;
"""
)

SQL = MEMORY_EVIDENCE

ISOLATED_TABLES: tuple[str, ...] = ("memory_evidence",)
