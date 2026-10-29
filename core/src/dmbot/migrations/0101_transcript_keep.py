"""0101_transcript_keep

How long a campaign keeps transcripts (#1533).
"""

from __future__ import annotations

TRANSCRIPT_KEEP = """
    -- Keep transcripts for (#1533; docs/PLAN.md, "Retention"): a DM chooses how long a campaign
    -- keeps its transcripts. `transcript_keep_days` is NULL (forever, today's behaviour) or 30,
    -- 90 or 365; the daily retention job deletes the lines of every session that ended longer
    -- ago than that. The session's own row stays, so session numbers never shift:
    -- `transcripts_deleted_at` marks it (the lines are gone, line_count 0).
    -- `transcript_deleted_noted` is whether the DM screen has said so once; changing the choice
    -- resets it. Nullable or defaulted columns: no existing row changes, so no row-level
    -- security needs opening.
    ALTER TABLE campaigns
        ADD COLUMN transcript_keep_days INTEGER
            CHECK (transcript_keep_days IS NULL OR transcript_keep_days IN (30, 90, 365)),
        ADD COLUMN transcript_deleted_noted BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE transcript_sessions ADD COLUMN transcripts_deleted_at BIGINT;

    -- The website lists the same sessions with the same numbers as the bot: a session whose
    -- transcript was deleted still takes its number, so the ones after it do not shift.
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
                           'engines', to_jsonb(s.engines)) ORDER BY s.started_at DESC, s.id DESC)
                FROM (
                    SELECT * FROM (
                        SELECT t.*, row_number() OVER (ORDER BY t.started_at, t.id) AS number
                        FROM transcript_sessions t
                        WHERE t.guild_id = g AND t.campaign_id = c
                          AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                    ) numbered
                    WHERE numbered.line_count > 0
                ) s), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_sessions(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = TRANSCRIPT_KEEP
