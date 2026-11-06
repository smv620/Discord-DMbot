"""0217_import_progress

Step counts and last-moved time per import (#2564).
"""

from __future__ import annotations

IMPORT_STEPS = """
    -- How far each imported session's cleaning has got and when anything about it last moved
    -- (#2564), so the website can show "420 of 900 lines" and say "stalled" when nothing has moved
    -- for longer than the stage's limit (kept in code, `progress.py`). Counts and times only. The
    -- columns have defaults, so no row changes and no backfill policy is needed. A trigger stamps
    -- `updated_at` whenever a step column changes, so no worker has to remember to.
    ALTER TABLE session_imports
        ADD COLUMN step_done  INT NOT NULL DEFAULT 0,
        ADD COLUMN step_total INT NOT NULL DEFAULT 0,
        ADD COLUMN updated_at BIGINT NOT NULL DEFAULT 0;

    CREATE FUNCTION session_imports_moved() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
    BEGIN
        IF NEW.updated_at = OLD.updated_at AND (
               NEW.state, NEW.scanned, NEW.scan_finished, NEW.scan_tries, NEW.recap_started,
               NEW.recap_tries, NEW.step_done, NEW.step_total, NEW.scan_gave_up)
           IS DISTINCT FROM (
               OLD.state, OLD.scanned, OLD.scan_finished, OLD.scan_tries, OLD.recap_started,
               OLD.recap_tries, OLD.step_done, OLD.step_total, OLD.scan_gave_up) THEN
            NEW.updated_at := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER session_imports_moved BEFORE UPDATE ON session_imports
        FOR EACH ROW EXECUTE FUNCTION session_imports_moved();

    -- dmbot_import_progress, again (replaces 0154's): the same per-session rows with the counts and
    -- times the shared progress shape needs. NULL unless a DM.
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

SQL = IMPORT_STEPS
