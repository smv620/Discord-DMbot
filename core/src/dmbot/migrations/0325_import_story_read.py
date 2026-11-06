"""0325_import_story_read

Where the story read of an imported session is (#3307, #3301): so the website can show it.
"""

from __future__ import annotations

IMPORT_STORY_READ = """
    -- The story read (the extractor, which finds connections) of an imported session, for the
    -- import's progress card (CLAUDE.md, "every wait shows that DMbot is still working"):
    -- '' not started, 'reading' (batch `story_part` of `story_parts`), 'waiting' (stopped by the
    -- daily limit or a failed batch; it carries on by itself) or 'read'. Counts and times only,
    -- so a backup leaves them out and a restored session starts blank. The columns have
    -- defaults, so no row changes and no backfill policy is needed.
    ALTER TABLE session_imports
        ADD COLUMN story_state      TEXT NOT NULL DEFAULT ''
            CHECK (story_state IN ('', 'reading', 'waiting', 'read')),
        ADD COLUMN story_part       INT NOT NULL DEFAULT 0 CHECK (story_part >= 0),
        ADD COLUMN story_parts      INT NOT NULL DEFAULT 0 CHECK (story_parts >= 0),
        ADD COLUMN story_started_at BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN story_moved_at   BIGINT NOT NULL DEFAULT 0;

    -- dmbot_import_progress, again (replaces 0217's): the same per-session rows plus `story`.
    -- NULL unless a DM.
    CREATE OR REPLACE FUNCTION dmbot_import_progress(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', i.file_name, 'state', i.state, 'reason', i.reason,
                       'date', t.started_at,
                       'namesUnchecked', i.scan_gave_up,
                       'namesToAnswer', CASE WHEN h.asked THEN h.names_open ELSE 0 END,
                       'imported', i.imported_at,
                       'moved', i.updated_at,
                       'recapMoved', COALESCE(r.updated_at, 0),
                       'stepDone', i.step_done, 'stepTotal', i.step_total,
                       'story', jsonb_build_object(
                           'state', i.story_state, 'part', i.story_part,
                           'parts', i.story_parts, 'started', i.story_started_at,
                           'moved', i.story_moved_at),
                       'scanned', i.scanned, 'recapState', r.state,
                       'stage', CASE
                           WHEN i.state = 'failed' THEN 'failed'
                           WHEN i.state <> 'done' THEN 'cleaning'
                           WHEN NOT i.scan_finished THEN 'checking_names'
                           WHEN NOT i.write_recap THEN 'done'
                           WHEN h.session_at IS NOT NULL AND NOT h.asked THEN 'finding_names'
                           WHEN h.session_at IS NOT NULL THEN 'waiting_for_names'
                           WHEN r.state = 'ready' THEN 'recap_ready'
                           WHEN r.state = 'failed' THEN 'recap_failed'
                           WHEN r.session_at IS NOT NULL THEN 'writing_recap'
                           WHEN i.recap_started THEN 'recap_not_written'
                           ELSE 'starting_recap' END)
                   ORDER BY t.started_at, i.session_id)
            FROM session_imports i
            JOIN transcript_sessions t ON t.guild_id = i.guild_id AND t.id = i.session_id
            LEFT JOIN recap_holds h ON h.guild_id = i.guild_id AND h.campaign_id = i.campaign_id
                AND h.session_at = t.started_at
            LEFT JOIN recap_jobs r ON r.guild_id = i.guild_id AND r.campaign_id = i.campaign_id
                AND r.session_at = t.started_at
            WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_import_progress(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""

SQL = IMPORT_STORY_READ
