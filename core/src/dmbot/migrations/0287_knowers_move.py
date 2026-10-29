"""0287_knowers_move

Today's flags and knowers become `memory_knowers` rows (#2995; STORY_MEMORY §9b item 9).
"""

from __future__ import annotations

# Rows only: no column or table changes. The old flags stay where they are and keep being read until
# the readers go through the gate (slice 3); nothing here removes or rewrites a flag.
# A migration that changes rows opens row-level security for itself (CLAUDE.md, #704): every table
# read or written below has a `migrate_backfill` policy for the length of this migration.
# What each row means (every row is certainty 1.0, the way things were told before certainty):
#   - revealed (a confirmed entry not hidden from the players, not an unnamed person and not a
#     limited clue; a name not secret; a connection The party holds at 1.0; a condition of such an
#     entry): known to every player character that exists now;
#   - secret, hidden, unnamed, unrevealed: known to nobody (the DM fixes those with Who knows this);
#   - a character entry knows that it exists itself (§9b, self-knowledge);
#   - a limited clue is known to the characters its `knows` connections list;
#   - a connection's knowers: each player character's current belief of 1.0, and each NPC's
#     current belief above 0 (at that certainty);
#   - a secret other name: its old knowers (`memory_alias_knowers`).
# A point of view is made for every confirmed character that has none (a player's `pc`, any
# other `npc`), since a knower row needs one.
KNOWERS_MOVE = r"""
    CREATE POLICY migrate_backfill ON memory_knowers FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_perspectives FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_beliefs FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_aliases FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_alias_knowers FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_states FOR SELECT USING (true);

    -- A player's character that was given an NPC's point of view is a player's now.
    UPDATE memory_perspectives p SET kind = 'pc'
        FROM memory_entities e
        WHERE e.guild_id = p.guild_id AND e.campaign_id = p.campaign_id AND e.id = p.entity_id
          AND e.role = 'player_character' AND p.kind = 'npc';

    -- A point of view for every confirmed character that has none.
    INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id, created_at)
        SELECT e.guild_id, e.campaign_id,
               md5(random()::text || clock_timestamp()::text || e.id),
               CASE WHEN e.role = 'player_character' THEN 'pc' ELSE 'npc' END,
               e.id, e.created_at
        FROM memory_entities e
        WHERE e.type = 'character' AND e.status = 'confirmed'
          AND NOT EXISTS (
              SELECT 1 FROM memory_perspectives p
              WHERE p.guild_id = e.guild_id AND p.campaign_id = e.campaign_id
                AND p.entity_id = e.id);

    -- Entries that exist for the players: every player character knows they exist.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, about_entity_id, created_at, confidence)
        SELECT e.guild_id, e.campaign_id,
               md5(random()::text || clock_timestamp()::text || e.id || p.id),
               p.id, e.id, e.created_at, 1.0
        FROM memory_entities e
        JOIN memory_perspectives p ON p.guild_id = e.guild_id AND p.campaign_id = e.campaign_id
                                  AND p.kind = 'pc'
        WHERE e.status = 'confirmed'
          AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
        ON CONFLICT DO NOTHING;

    -- A character knows that it exists itself.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, about_entity_id, created_at, confidence)
        SELECT e.guild_id, e.campaign_id,
               md5(random()::text || clock_timestamp()::text || e.id || 'self'),
               p.id, e.id, e.created_at, 1.0
        FROM memory_entities e
        JOIN memory_perspectives p ON p.guild_id = e.guild_id AND p.campaign_id = e.campaign_id
                                  AND p.entity_id = e.id
        WHERE e.type = 'character' AND e.status = 'confirmed'
        ON CONFLICT DO NOTHING;

    -- A limited clue is known to the characters its confirmed `knows` connections list.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, about_entity_id, created_at, confidence)
        SELECT c.guild_id, c.campaign_id,
               md5(random()::text || clock_timestamp()::text || c.id || r.id),
               p.id, c.id, r.created_at, 1.0
        FROM memory_entities c
        JOIN memory_relations r ON r.guild_id = c.guild_id AND r.campaign_id = c.campaign_id
                               AND r.object_id = c.id AND r.predicate = 'knows'
                               AND r.status = 'confirmed'
        JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                  AND p.entity_id = r.subject_id
        WHERE c.status = 'confirmed' AND c.audience_limited
        ON CONFLICT DO NOTHING;

    -- A condition (alive, dead, ...) of an entry that exists for the players.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, state_id, created_at, confidence)
        SELECT s.guild_id, s.campaign_id,
               md5(random()::text || clock_timestamp()::text || s.id || p.id),
               p.id, s.id, s.created_at, 1.0
        FROM memory_states s
        JOIN memory_entities e ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id
                              AND e.id = s.entity_id
        JOIN memory_perspectives p ON p.guild_id = s.guild_id AND p.campaign_id = s.campaign_id
                                  AND p.kind = 'pc'
        WHERE e.status = 'confirmed'
          AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
        ON CONFLICT DO NOTHING;

    -- A name that is not secret, of an entry that exists for the players.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, alias_id, created_at, confidence)
        SELECT a.guild_id, a.campaign_id,
               md5(random()::text || clock_timestamp()::text || a.id || p.id),
               p.id, a.id, a.created_at, 1.0
        FROM memory_aliases a
        JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                              AND e.id = a.entity_id
        JOIN memory_perspectives p ON p.guild_id = a.guild_id AND p.campaign_id = a.campaign_id
                                  AND p.kind = 'pc'
        WHERE a.status <> 'rejected' AND NOT a.secret AND e.status = 'confirmed'
          AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
        ON CONFLICT DO NOTHING;

    -- A secret name: whoever knew it before still does.
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, alias_id, created_at, confidence)
        SELECT k.guild_id, k.campaign_id,
               md5(random()::text || clock_timestamp()::text || k.id),
               k.perspective_id, k.alias_id, k.created_at, 1.0
        FROM memory_alias_knowers k
        JOIN memory_aliases a ON a.guild_id = k.guild_id AND a.campaign_id = k.campaign_id
                             AND a.id = k.alias_id
        WHERE a.status <> 'rejected' AND a.secret
        ON CONFLICT DO NOTHING;

    -- A connection The party holds as fact: every player character knows it (unless one of its
    -- ends is hidden from the players, a limited clue or an unnamed person: the old readers kept
    -- such a connection from them, and learning it would make that entry known).
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, relation_id, created_at, confidence)
        SELECT r.guild_id, r.campaign_id,
               md5(random()::text || clock_timestamp()::text || r.id || p.id),
               p.id, r.id, r.created_at, 1.0
        FROM memory_relations r
        JOIN memory_entities a ON a.guild_id = r.guild_id AND a.campaign_id = r.campaign_id
                              AND a.id = r.subject_id
        JOIN memory_entities o ON o.guild_id = r.guild_id AND o.campaign_id = r.campaign_id
                              AND o.id = r.object_id
        JOIN memory_perspectives p ON p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                                  AND p.kind = 'pc'
        WHERE r.status = 'confirmed'
          AND NOT a.hidden_from_players AND NOT a.audience_limited AND NOT a.unnamed
          AND NOT o.hidden_from_players AND NOT o.audience_limited AND NOT o.unnamed
          AND EXISTS (
            SELECT 1 FROM memory_beliefs b
            JOIN memory_perspectives party ON party.guild_id = b.guild_id
                                          AND party.campaign_id = b.campaign_id
                                          AND party.id = b.perspective_id
            WHERE party.kind = 'party' AND b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id
              AND b.relation_id = r.id AND b.confidence >= 1.0
              AND b.seq = (SELECT max(x.seq) FROM memory_beliefs x
                           WHERE x.guild_id = b.guild_id AND x.campaign_id = b.campaign_id
                             AND x.perspective_id = b.perspective_id
                             AND x.relation_id = b.relation_id))
        ON CONFLICT DO NOTHING;

    -- A connection's knowers: a player character's current belief of 1.0, an NPC's current belief
    -- above 0 at that certainty (a belief of 0.0 is how "stopped knowing" was written).
    INSERT INTO memory_knowers
            (guild_id, campaign_id, id, perspective_id, relation_id, created_at, confidence)
        SELECT b.guild_id, b.campaign_id,
               md5(random()::text || clock_timestamp()::text || b.id),
               b.perspective_id, b.relation_id, b.recorded_at, b.confidence
        FROM memory_beliefs b
        JOIN memory_perspectives p ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id
                                  AND p.id = b.perspective_id
        JOIN memory_relations r ON r.guild_id = b.guild_id AND r.campaign_id = b.campaign_id
                               AND r.id = b.relation_id
        WHERE r.status = 'confirmed'
          AND ((p.kind = 'pc' AND b.confidence >= 1.0) OR (p.kind = 'npc' AND b.confidence > 0))
          AND b.seq = (SELECT max(x.seq) FROM memory_beliefs x
                       WHERE x.guild_id = b.guild_id AND x.campaign_id = b.campaign_id
                         AND x.perspective_id = b.perspective_id
                         AND x.relation_id = b.relation_id)
        ON CONFLICT DO NOTHING;

    DROP POLICY migrate_backfill ON memory_states;
    DROP POLICY migrate_backfill ON memory_alias_knowers;
    DROP POLICY migrate_backfill ON memory_aliases;
    DROP POLICY migrate_backfill ON memory_beliefs;
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_knowers;
"""

SQL = KNOWERS_MOVE
