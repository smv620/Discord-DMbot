"""0103_web_transcript_deleted

The site shows deleted ones (#1548).
"""

from __future__ import annotations

WEB_TRANSCRIPT_DELETED = """
    -- The website says why a session has no lines (#1548). A session whose transcript was deleted
    -- (a player's Delete my transcripts, or Keep transcripts for; 0101) keeps its row and number
    -- with `transcripts_deleted_at` set and line_count 0. 0101's door numbered those sessions but
    -- then dropped them from its answer; now they stay, each with `transcriptsDeletedAt`, so the
    -- list can say "Transcript deleted" instead of showing nothing. The campaigns door counts
    -- them too, so a campaign whose transcripts have all expired still shows its sessions.
    -- Replacing a door changes no row, so no row-level security needs opening; the door's checks
    -- and numbering are 0101's, and CREATE OR REPLACE drops the function's own settings, so they
    -- are set again below.
    CREATE OR REPLACE FUNCTION dmbot_web_transcript_sessions(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'name', camp.name,
            'owner', camp.owner_user_id::TEXT,
            'sessions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id, 'number', s.number, 'startedAt', s.started_at,
                           'endedAt', s.ended_at, 'lines', s.line_count,
                           'engines', to_jsonb(s.engines),
                           'transcriptsDeletedAt', s.transcripts_deleted_at)
                       ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                    FROM transcript_sessions t
                    WHERE t.guild_id = g AND t.campaign_id = c
                      AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                ) s), '[]'::jsonb));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_transcript_campaigns(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', x.id, 'name', x.name, 'owner', x.owner, 'sessions', x.sessions)
                       ORDER BY lower(x.name), x.id)
            FROM (
                SELECT c.id, c.name, c.owner_user_id::TEXT AS owner, count(*) AS sessions
                FROM campaigns c
                JOIN transcript_sessions t
                  ON t.guild_id = c.guild_id AND t.campaign_id = c.id
                 AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                WHERE c.guild_id = g
                GROUP BY c.id, c.name, c.owner_user_id
                ORDER BY lower(c.name), c.id
                LIMIT 100
            ) x), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_campaigns(BIGINT) FROM PUBLIC;
"""

SQL = WEB_TRANSCRIPT_DELETED
