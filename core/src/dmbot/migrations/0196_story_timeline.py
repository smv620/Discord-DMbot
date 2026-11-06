"""0196_story_timeline

Recaps and reveals for the timeline (#2462).
"""

from __future__ import annotations

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
STORY_TIMELINE = """
    -- What the Story timeline page needs besides the sessions themselves (#2462): the campaign's
    -- recaps and the names shown to the players at the table, each with the time it belongs to. The
    -- sessions and their numbers come from the transcripts page's own door, so there is one count.
    -- For one campaign of a server in the caller's own list, NULL otherwise (and for a campaign
    -- that is not there). Recaps: a game master sees every one that is approved or going out or
    -- posted; a player sees posted ones only, never a draft. Shown at the table: the names DMbot
    -- showed the players after a session (the 'scan' writer's changes, #2404) that the players can
    -- still see: an Undo, or the game master hiding the name again, puts the entry back to hidden,
    -- and a redo shows it again, so the entry's own state says it (no need to follow the undo
    -- chain). The same list for everyone: they are names the players already see. A name whose own
    -- spelling is a secret one is left out for a player.
    CREATE FUNCTION dmbot_web_story_timeline(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        is_dm BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me);
        RETURN jsonb_build_object(
            'dm', is_dm,
            'recaps', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'sessionAt', r.session_at, 'title', r.title, 'status', r.status)
                    ORDER BY r.session_at)
                FROM recap_jobs r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.title <> ''
                  AND r.status <> 'dropped'
                  AND (r.status = 'posted'
                       OR (is_dm AND (r.status = 'posting' OR r.approved_at > 0)))
            ), '[]'::jsonb),
            'reveals', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('at', x.at, 'names', x.names) ORDER BY x.at)
                FROM (
                    SELECT min(ch.made_at) AS at,
                           jsonb_agg(e.name ORDER BY lower(e.name), e.id) AS names
                    FROM memory_changes ch
                    JOIN memory_entities e
                      ON e.guild_id = ch.guild_id AND e.campaign_id = ch.campaign_id
                     AND e.id = ch.row_id
                    WHERE ch.guild_id = g AND ch.campaign_id = c
                      AND ch.table_name = 'memory_entities' AND ch.op = 'update'
                      AND ch.source = 'scan'
                      AND ch.before ->> 'hidden_from_players' = 'true'
                      AND ch.after ->> 'hidden_from_players' = 'false'
                      AND e.status = 'confirmed' AND e.type <> 'person'
                      AND NOT e.hidden_from_players AND NOT e.audience_limited
                      -- the DM's plot notes are not names, and a player never gets them
                      AND (is_dm OR e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                   'promise', 'deed'))
                      AND (is_dm OR NOT EXISTS (
                            SELECT 1 FROM memory_aliases a
                            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                              AND a.status <> 'rejected' AND a.secret
                              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                                = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')))
                    GROUP BY ch.batch
                ) x
            ), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_story_timeline(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_story_timeline(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = STORY_TIMELINE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The Story timeline page (#2462).
    ("dmbot_web_story_timeline", "dmbot_web_story_timeline(BIGINT, TEXT)"),
)
