"""0154_import_progress

Each import session's stage, scan given up.
"""

from __future__ import annotations

IMPORT_PROGRESS = """
    -- A scan let go after its tries (#2106 step 3) is remembered, so the progress call can tell the
    -- DM "couldn't check names for this session" instead of showing a clean finish. The column is
    -- added with a default, so no row changes and no backfill policy is needed.
    ALTER TABLE session_imports ADD COLUMN scan_gave_up BOOLEAN NOT NULL DEFAULT FALSE;

    -- dmbot_import_progress: each session of one import with where it has got to (`stage`), the
    -- name questions its recap waits on, and whether its name check was given up. NULL unless a DM.
    -- Replaces the 0079 function; the stage reads the hold and the recap job of the same session.
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

SQL = IMPORT_PROGRESS
