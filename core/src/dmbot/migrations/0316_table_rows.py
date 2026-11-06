"""0316_table_rows

A player character who joined after things were shown gets the who-knows rows it lacks (#2995;
STORY_MEMORY §9b item 9, Supervisor 2026-11-01).
"""

from __future__ import annotations

# Rows only: no column or table changes, nothing removed or rewritten. Adds only missing rows, so a
# second run adds nothing. A migration that changes rows opens row-level security for itself
# (CLAUDE.md, #704): every table read or written below has a `migrate_backfill` policy for the
# length of this migration.
# The rule is the one the app now applies when a player character joins (`inherit_table_rows`):
#   - a statement every OTHER player character already knew (awake rows only) BEFORE the one that
#     lacks it joined (the newest of those rows is no newer than that character's entry: the data
#     cannot tell "joined late" from "left out on purpose", and a row made after the join may
#     be a partial reveal) is given to it, at the least sure of theirs: never a "some characters"
#     list, a DM-only or NPC-only row, or a row a hide put to sleep (a sleeping row of its own also
#     stops it, so nothing is woken here);
#   - a campaign with exactly one player character has no table to copy: what the flags show the
#     players (an entry, its conditions and names, a connection with both ends shown) is given.
# Statement ids are random 32-character hex strings, so one id never names two kinds of statement
# (the `NOT EXISTS` checks compare the id alone). Two player characters that both lack a row get
# nothing here ("every other holds it" fails for both): safe, and the app path handles each join.
# The count of rows added is raised as a notice, counts only.
TABLE_ROWS = r"""
    CREATE POLICY migrate_backfill ON memory_knowers FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_perspectives FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_states FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_aliases FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR SELECT USING (true);

    DO $$
    DECLARE
        copied BIGINT;
        flagged BIGINT;
    BEGIN
        -- The table: what every other player character knows, given to the one that lacks it.
        WITH pcv AS (
            SELECT p.guild_id, p.campaign_id, p.id AS view_id, e.created_at AS joined_at
            FROM memory_perspectives p
            JOIN memory_entities e ON e.guild_id = p.guild_id AND e.campaign_id = p.campaign_id
                                  AND e.id = p.entity_id
            WHERE p.kind = 'pc' AND e.type = 'character' AND e.role = 'player_character'
              AND e.status = 'confirmed'
        ), pcs AS (
            SELECT guild_id, campaign_id, count(*) AS n FROM pcv GROUP BY 1, 2
        ), held AS (
            SELECT k.guild_id, k.campaign_id, k.perspective_id, k.confidence, k.created_at,
                   CASE WHEN k.about_entity_id IS NOT NULL THEN 'e'
                        WHEN k.state_id IS NOT NULL THEN 's'
                        WHEN k.relation_id IS NOT NULL THEN 'r' ELSE 'a' END AS kind,
                   COALESCE(k.about_entity_id, k.state_id, k.relation_id, k.alias_id) AS sid
            FROM memory_knowers k
            JOIN pcv ON pcv.guild_id = k.guild_id AND pcv.campaign_id = k.campaign_id
                    AND pcv.view_id = k.perspective_id
            WHERE cardinality(k.hidden_by) = 0
        ), said AS (
            SELECT guild_id, campaign_id, kind, sid, count(*) AS holders,
                   min(confidence) AS sure, max(created_at) AS last_at
            FROM held GROUP BY 1, 2, 3, 4
        )
        INSERT INTO memory_knowers
                (guild_id, campaign_id, id, perspective_id, about_entity_id, state_id,
                 relation_id, alias_id, created_at, confidence)
            SELECT s.guild_id, s.campaign_id,
                   md5(random()::text || clock_timestamp()::text || s.sid || v.view_id),
                   v.view_id,
                   CASE WHEN s.kind = 'e' THEN s.sid END,
                   CASE WHEN s.kind = 's' THEN s.sid END,
                   CASE WHEN s.kind = 'r' THEN s.sid END,
                   CASE WHEN s.kind = 'a' THEN s.sid END,
                   extract(epoch FROM now())::bigint, s.sure
            FROM said s
            JOIN pcs ON pcs.guild_id = s.guild_id AND pcs.campaign_id = s.campaign_id
                    AND pcs.n >= 2 AND s.holders = pcs.n - 1
            JOIN pcv v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
            WHERE s.last_at <= v.joined_at
              AND NOT EXISTS (
                SELECT 1 FROM memory_knowers x
                WHERE x.guild_id = v.guild_id AND x.campaign_id = v.campaign_id
                  AND x.perspective_id = v.view_id
                  AND COALESCE(x.about_entity_id, x.state_id, x.relation_id, x.alias_id) = s.sid)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS copied = ROW_COUNT;

        -- A campaign with one player character: what the flags show the players.
        WITH pcv AS (
            SELECT p.guild_id, p.campaign_id, p.id AS view_id
            FROM memory_perspectives p
            JOIN memory_entities e ON e.guild_id = p.guild_id AND e.campaign_id = p.campaign_id
                                  AND e.id = p.entity_id
            WHERE p.kind = 'pc' AND e.type = 'character' AND e.role = 'player_character'
              AND e.status = 'confirmed'
        ), one AS (
            SELECT guild_id, campaign_id, min(view_id) AS view_id
            FROM pcv GROUP BY 1, 2 HAVING count(*) = 1
        ), shown AS (
            SELECT o.guild_id, o.campaign_id, o.view_id, 'e' AS kind, e.id AS sid
            FROM one o JOIN memory_entities e ON e.guild_id = o.guild_id
                                              AND e.campaign_id = o.campaign_id
            WHERE e.status = 'confirmed'
              AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
            UNION ALL
            SELECT o.guild_id, o.campaign_id, o.view_id, 's', st.id
            FROM one o
            JOIN memory_states st ON st.guild_id = o.guild_id AND st.campaign_id = o.campaign_id
            JOIN memory_entities e ON e.guild_id = st.guild_id AND e.campaign_id = st.campaign_id
                                  AND e.id = st.entity_id
            WHERE e.status = 'confirmed'
              AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
            UNION ALL
            SELECT o.guild_id, o.campaign_id, o.view_id, 'a', a.id
            FROM one o
            JOIN memory_aliases a ON a.guild_id = o.guild_id AND a.campaign_id = o.campaign_id
            JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                                  AND e.id = a.entity_id
            WHERE a.status <> 'rejected' AND NOT a.secret AND e.status = 'confirmed'
              AND NOT e.hidden_from_players AND NOT e.audience_limited AND NOT e.unnamed
            UNION ALL
            SELECT o.guild_id, o.campaign_id, o.view_id, 'r', r.id
            FROM one o
            JOIN memory_relations r ON r.guild_id = o.guild_id AND r.campaign_id = o.campaign_id
            JOIN memory_entities s ON s.guild_id = r.guild_id AND s.campaign_id = r.campaign_id
                                  AND s.id = r.subject_id
            JOIN memory_entities t ON t.guild_id = r.guild_id AND t.campaign_id = r.campaign_id
                                  AND t.id = r.object_id
            WHERE r.status = 'confirmed' AND NOT r.secret AND NOT r.unrevealed
              AND NOT s.hidden_from_players AND NOT s.audience_limited AND NOT s.unnamed
              AND NOT t.hidden_from_players AND NOT t.audience_limited AND NOT t.unnamed
        )
        INSERT INTO memory_knowers
                (guild_id, campaign_id, id, perspective_id, about_entity_id, state_id,
                 relation_id, alias_id, created_at, confidence)
            SELECT h.guild_id, h.campaign_id,
                   md5(random()::text || clock_timestamp()::text || h.sid || h.view_id),
                   h.view_id,
                   CASE WHEN h.kind = 'e' THEN h.sid END,
                   CASE WHEN h.kind = 's' THEN h.sid END,
                   CASE WHEN h.kind = 'r' THEN h.sid END,
                   CASE WHEN h.kind = 'a' THEN h.sid END,
                   extract(epoch FROM now())::bigint, 1.0
            FROM shown h
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_knowers x
                WHERE x.guild_id = h.guild_id AND x.campaign_id = h.campaign_id
                  AND x.perspective_id = h.view_id
                  AND COALESCE(x.about_entity_id, x.state_id, x.relation_id, x.alias_id) = h.sid)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS flagged = ROW_COUNT;

        RAISE NOTICE '0316_table_rows: % rows copied from the table, % from the flags',
            copied, flagged;
    END $$;

    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_aliases;
    DROP POLICY migrate_backfill ON memory_states;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON memory_perspectives;
    DROP POLICY migrate_backfill ON memory_knowers;
"""

SQL = TABLE_ROWS
