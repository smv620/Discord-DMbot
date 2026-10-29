"""0179_beliefs

Perspectives, beliefs and the backfill (#2409).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _entity_link,
    _isolate,
    _memory_scope,
)

BELIEFS = (
    f"""
    -- Assertions, perspectives and evidence (#2409; docs/STORY_MEMORY.md, section 9). A connection
    -- row is already the claim itself; this adds WHO believes it and how strongly.
    --   memory_perspectives  who holds beliefs: `truth` (the DM's meta-truth) and `party` (what
    --                        the players have learned) for every campaign, and optionally one per
    --                        player character (`pc`) or NPC (`npc`) whose beliefs matter.
    --   memory_beliefs       a perspective's belief in one connection: a confidence from 0.0
    --                        (certainly false) to 1.0, a status, the session, and the evidence it
    --                        points at BY ID (claim, line or event pointers; no words). History is
    --                        kept: a new belief adds a row, and the highest `seq` for a pair is
    --                        the current one (a write's memory version, or one more than the
    --                        pair's last, whichever is higher, so a restored backup keeps order).
    --   memory_entities.manipulated_by  an event, clue or item entry that points at the evidence
    --                        it faked ("the ledger Fabio planted").
    -- Like every memory table both are one campaign's, with forced row-level security and
    -- composite keys, so a row can never point into another campaign.
    CREATE TABLE memory_perspectives (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind       TEXT NOT NULL CHECK (kind IN ('truth', 'party', 'pc', 'npc')),
        entity_id  TEXT,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((kind IN ('truth', 'party') AND entity_id IS NULL)
            OR (kind IN ('pc', 'npc') AND entity_id IS NOT NULL)),
        {_entity_link("entity_id")}
    );
    CREATE UNIQUE INDEX memory_perspectives_one_each
        ON memory_perspectives (guild_id, campaign_id, kind) WHERE kind IN ('truth', 'party');
    CREATE UNIQUE INDEX memory_perspectives_one_per_entry
        ON memory_perspectives (guild_id, campaign_id, entity_id) WHERE entity_id IS NOT NULL;
    CREATE TABLE memory_beliefs (
        {_memory_scope()}
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        perspective_id TEXT NOT NULL,
        relation_id    TEXT NOT NULL,
        confidence     DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        status         TEXT NOT NULL CHECK (status IN ('theory', 'accusation', 'revealed_fact',
            'false_evidence', 'secret_plot', 'magically_verified', 'evasion_under_magic')),
        session_at     BIGINT,
        evidence_ids   TEXT[] NOT NULL DEFAULT '{{}}'
            CHECK (cardinality(evidence_ids) <= 20
               AND char_length(array_to_string(evidence_ids, ',')) <= 2000),
        seq            BIGINT NOT NULL,
        recorded_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, perspective_id)
            REFERENCES memory_perspectives (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX memory_beliefs_current
        ON memory_beliefs (guild_id, campaign_id, relation_id, perspective_id, seq DESC);
    CREATE INDEX memory_beliefs_by_perspective
        ON memory_beliefs (guild_id, campaign_id, perspective_id);
    ALTER TABLE memory_entities ADD COLUMN manipulated_by TEXT;
    CREATE INDEX memory_entities_by_manipulated_by
        ON memory_entities (guild_id, campaign_id, manipulated_by) WHERE manipulated_by IS NOT NULL;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_manipulated_by_other
        CHECK (manipulated_by IS NULL OR manipulated_by <> id);
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_manipulated_by_link
        FOREIGN KEY (guild_id, campaign_id, manipulated_by)
        REFERENCES memory_entities (guild_id, campaign_id, id) DEFERRABLE INITIALLY DEFERRED;
    """
    + _isolate("memory_perspectives")
    + _isolate("memory_beliefs")
    + """
    -- The backfill. Migrations run with no server set, so row-level security hides every row: open
    -- EVERY table the statements read or write for them alone, dropped again inside the
    -- migration's transaction (CLAUDE.md: a plain INSERT ... SELECT would match nothing).
    CREATE POLICY migrate_backfill ON memory_perspectives FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_beliefs FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    -- Every campaign gets The truth and The party.
    INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)
        SELECT c.guild_id, c.id, md5(c.guild_id::text || c.id || k.kind || clock_timestamp()::text),
               k.kind, extract(epoch FROM now())::BIGINT
          FROM campaigns c CROSS JOIN (VALUES ('truth'), ('party')) AS k(kind);
    -- Every confirmed connection: The truth at 1.0 (a secret one as a Secret Plot) ...
    INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,
                                confidence, status, session_at, evidence_ids, seq, recorded_at)
        SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'truth'), p.id, r.id, 1.0,
               CASE WHEN r.secret THEN 'secret_plot' ELSE 'revealed_fact' END,
               NULL, '{}', 0, extract(epoch FROM now())::BIGINT
          FROM memory_relations r
          JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                    AND p.kind = 'truth'
         WHERE r.status = 'confirmed';
    -- ... and, when it is not secret, The party at 1.0 (a Revealed Fact). A secret one gets no
    -- party belief at all.
    INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,
                                confidence, status, session_at, evidence_ids, seq, recorded_at)
        SELECT r.guild_id, r.campaign_id, md5(p.id || r.id || 'party'), p.id, r.id, 1.0,
               'revealed_fact', NULL, '{}', 0, extract(epoch FROM now())::BIGINT
          FROM memory_relations r
          JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                    AND p.kind = 'party'
         WHERE r.status = 'confirmed' AND NOT r.secret;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_beliefs;
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON campaigns;
    -- Every campaign made from now on has them from the start, part of the campaign and never part
    -- of a change a DM could undo (the trigger runs in the creating transaction, so the server is
    -- already set and the table's policy lets the rows in).
    CREATE FUNCTION dmbot_campaign_perspectives() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, created_at)
            SELECT NEW.guild_id, NEW.id, md5(NEW.guild_id::text || NEW.id || k.kind), k.kind,
                   NEW.created_at
              FROM (VALUES ('truth'), ('party')) AS k(kind);
        RETURN NEW;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_campaign_perspectives()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    CREATE TRIGGER campaign_perspectives AFTER INSERT ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_campaign_perspectives();
    """
)

SQL = BELIEFS

ISOLATED_TABLES: tuple[str, ...] = (
    "memory_perspectives",
    "memory_beliefs",
)
