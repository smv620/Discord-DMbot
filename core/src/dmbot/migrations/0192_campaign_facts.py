"""0192_campaign_facts

Each campaign card's facts (#2460).
"""

from __future__ import annotations

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
CAMPAIGN_FACTS = """
    -- The facts on each campaign's card on My Account (docs/PLAN.md, "Where everything is on the
    -- website"; #2460): for one server, every campaign the signed-in person runs (a DM of it) or
    -- plays in (claimed a player character), in one read, not one per campaign. NULL when the
    -- caller is not in the server, like the other web doors. A non-DM's NPC count leaves out what
    -- the players cannot see (a hidden entry, or one whose own name is secret), exactly as the
    -- Names list does. `live` is DMbot's own record of the session it is in right now
    -- (active_sessions), not an open transcript row: a crash leaves one of those open. The seconds
    -- count the sessions that ended, those whose transcripts were deleted by retention included.
    CREATE FUNCTION dmbot_web_campaign_facts(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', x.id, 'name', x.name, 'dm', x.is_dm,
                'startedAt', x.created_at, 'lastPlayedAt', x.last_played_at,
                'live', x.live, 'sessions', x.sessions, 'secondsPlayed', x.seconds,
                'players', x.players, 'npcs', x.npcs) ORDER BY lower(x.name), x.id)
            FROM (
                SELECT k.id, k.name, k.created_at, k.last_played_at, d.user_id IS NOT NULL AS is_dm,
                    EXISTS (SELECT 1 FROM active_sessions a
                            WHERE a.guild_id = g AND a.campaign_id = k.id) AS live,
                    (SELECT count(*) FROM transcript_sessions t
                      WHERE t.guild_id = g AND t.campaign_id = k.id
                        AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) AS sessions,
                    COALESCE((SELECT sum(GREATEST(t.ended_at - t.started_at, 0))
                              FROM transcript_sessions t
                              WHERE t.guild_id = g AND t.campaign_id = k.id
                                AND t.ended_at IS NOT NULL
                                AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)),
                             0) AS seconds,
                    (SELECT count(*) FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = k.id AND e.status = 'confirmed'
                        AND e.role = 'player_character' AND e.played_by IS NOT NULL) AS players,
                    (SELECT count(*) FROM memory_entities e
                      WHERE e.guild_id = g AND e.campaign_id = k.id AND e.status = 'confirmed'
                        AND e.type = 'character' AND e.role = 'npc'
                        AND (d.user_id IS NOT NULL
                             OR (NOT e.audience_limited AND NOT e.hidden_from_players
                                 AND NOT EXISTS (
                                     SELECT 1 FROM memory_aliases a
                                     WHERE a.guild_id = g AND a.campaign_id = k.id
                                       AND a.entity_id = e.id AND a.status <> 'rejected'
                                       AND a.secret
                                       AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                         = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                                 )))) AS npcs
                FROM campaigns k
                LEFT JOIN campaign_dms d
                  ON d.guild_id = k.guild_id AND d.campaign_id = k.id AND d.user_id = me
                WHERE k.guild_id = g
                  AND (d.user_id IS NOT NULL
                       OR EXISTS (SELECT 1 FROM memory_entities e
                                  WHERE e.guild_id = g AND e.campaign_id = k.id
                                    AND e.status = 'confirmed' AND e.role = 'player_character'
                                    AND e.played_by = me))
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_facts(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_facts(BIGINT) FROM PUBLIC;
"""

SQL = CAMPAIGN_FACTS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The facts on each campaign's card on My Account (#2460).
    ("dmbot_web_campaign_facts", "dmbot_web_campaign_facts(BIGINT)"),
)
