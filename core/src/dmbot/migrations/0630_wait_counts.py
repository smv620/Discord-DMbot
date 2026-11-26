"""0630_wait_counts

A small, fast read of a running upload's or import's counts (#3534, part 3).
"""

from __future__ import annotations

# The page asks every 500 ms while a job runs and its tab is visible (Supervisor's ruling on
# #3534), so this door is as small as a read can be: for a DM of the campaign only (NULL for anyone
# else), one lore upload row by its key, or the session rows of one import by the import's key
# (indexed, a handful of rows), and only the stage and counts out of them. It does none of what
# `dmbot_web_lore_read` does on each call (the sweeps and their lock), joins no other table, and
# returns no word: not a transcript, a name, a document or a question. An import's rows carry their
# file name, which the import's own progress answer already gives the same DM, so the page can pair
# them with that answer's sessions.
SQL = r"""
    CREATE FUNCTION dmbot_web_wait_counts(g BIGINT, c TEXT, what TEXT, uid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        found JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF what = 'lore' THEN
            SELECT jsonb_build_object(
                       'kind', 'lore', 'state', u.state, 'stage', u.stage,
                       'done', u.done, 'total', u.total,
                       'wordsDone', u.words_done, 'callsMade', u.calls_made,
                       'namesFound', u.names_found, 'linksFound', u.links_found,
                       'createdAt', u.created_at, 'updatedAt', u.updated_at,
                       'serverNow', now_s)
                INTO found
                FROM lore_uploads u
                WHERE u.guild_id = g AND u.campaign_id = c AND u.id = uid;
            RETURN found;
        ELSIF what = 'import' THEN
            SELECT jsonb_build_object(
                       'kind', 'import', 'serverNow', now_s,
                       'sessions', COALESCE(jsonb_agg(jsonb_build_object(
                           'file', i.file_name, 'state', i.state,
                           'scanFinished', i.scan_finished,
                           'stepDone', i.step_done, 'stepTotal', i.step_total,
                           'wordsDone', i.words_done, 'callsMade', i.calls_made,
                           'startedAt', i.imported_at, 'updatedAt', i.updated_at)
                           ORDER BY i.imported_at, i.session_id), '[]'::jsonb))
                INTO found
                FROM session_imports i
                WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = uid
                HAVING count(*) > 0;
            RETURN found;
        END IF;
        RETURN NULL;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_wait_counts(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_wait_counts(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_wait_counts", "dmbot_web_wait_counts(BIGINT, TEXT, TEXT, TEXT)"),
)
