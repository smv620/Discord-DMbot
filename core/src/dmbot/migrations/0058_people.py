"""0058_people."""

from __future__ import annotations

PEOPLE = """
    -- The table's people are entries of their own (#1188): a player or the DM is a
    -- `person`, and a `plays` link goes from the person to the character. Every character
    -- played by someone today gets its person (named `Player` and the last four digits of
    -- the Discord ID until a better name is known) and the link. `played_by` stays on the
    -- character as a mirror that the sheets and fights still read. Migrations run with no
    -- server set, so row-level security hides every row: the backfill opens every table
    -- it reads or writes for itself (CLAUDE.md).
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_aliases FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR ALL USING (true) WITH CHECK (true);
    INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description, status,
            merged_into, source, created_at, played_by, role, needs_look)
        SELECT guild_id, campaign_id, md5(random()::text || clock_timestamp()::text),
            'person', 'Player ' || right(played_by::text, 4), '', 'confirmed', NULL,
            'migration', extract(epoch FROM now())::bigint, played_by, NULL, false
        FROM (SELECT DISTINCT guild_id, campaign_id, played_by FROM memory_entities
              WHERE role = 'player_character' AND played_by IS NOT NULL
                AND status IN ('proposed', 'confirmed')) players;
    INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key, kind, used_by,
            secret, status, sound_codes, source, created_at)
        SELECT guild_id, campaign_id, md5(random()::text || clock_timestamp()::text), id, name,
            lower(name), 'full', NULL, false, 'confirmed', ARRAY[]::text[], 'migration',
            extract(epoch FROM now())::bigint
        FROM memory_entities WHERE type = 'person' AND source = 'migration';
    INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate, object_id,
            detail, confidence, status, source, mention_ids, from_session_at, to_session_at,
            from_game_time, to_game_time, secret, created_at)
        SELECT c.guild_id, c.campaign_id, md5(random()::text || clock_timestamp()::text),
            p.id, 'plays', c.id, '', 1.0, 'confirmed', 'migration', ARRAY[]::text[], NULL, NULL,
            NULL, NULL, false, extract(epoch FROM now())::bigint
        FROM memory_entities c
        JOIN memory_entities p ON p.guild_id = c.guild_id AND p.campaign_id = c.campaign_id
            AND p.type = 'person' AND p.played_by = c.played_by
            AND p.status IN ('proposed', 'confirmed')
        WHERE c.role = 'player_character' AND c.played_by IS NOT NULL
            AND c.status IN ('proposed', 'confirmed');
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_aliases;
    DROP POLICY migrate_backfill ON memory_entities;
    -- The table's links to its entries are checked at the end of a transaction, and an
    -- index can't be built while checks are waiting: run them now.
    SET CONSTRAINTS ALL IMMEDIATE;
    -- One person entry for each Discord user in a campaign.
    CREATE UNIQUE INDEX memory_entities_one_person
        ON memory_entities (guild_id, campaign_id, played_by)
        WHERE type = 'person' AND status IN ('proposed', 'confirmed');
    """

SQL = PEOPLE
