"""0240_names_fixed_door

The recap page reads it (#2684).
"""

from __future__ import annotations

# The recap page's "Names fixed this session" (#2684): what the name pass did to the recap's
# session, read for a DM of the campaign only (anyone else gets NULL). The counts and each fix come
# from `name_pass_reports` and `name_pass_fixes`; the spellings as heard are read from
# `transcript_respells` while a fix stands (a fix that was undone has none) and are never copied.
# They are words from a transcript, for the campaign's DM and no one else: the API never logs them.
# A new function: no row changes.
NAMES_FIXED_DOOR = r"""
    CREATE FUNCTION dmbot_recap_names_fixed(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE sid TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT s.id INTO sid FROM transcript_sessions s
         WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
         ORDER BY s.id LIMIT 1;
        RETURN jsonb_build_object(
            'fixed', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', f.batch::text, 'name', f.name, 'times', f.times,
                    'forms', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object('heard', x.form, 'times', x.n)
                                         ORDER BY x.n DESC, x.form)
                        FROM (SELECT form, count(*) AS n
                              FROM transcript_respells r, unnest(r.was) AS form
                              WHERE r.guild_id = g AND r.campaign_id = c
                                AND r.session_id = f.session_id AND r.batch = f.batch
                              GROUP BY form ORDER BY count(*) DESC, form LIMIT 12) x
                    ), '[]'::jsonb),
                    'undone', f.undone_at IS NOT NULL)
                    ORDER BY f.times DESC, f.name, f.batch)
                FROM name_pass_fixes f
                WHERE f.guild_id = g AND f.campaign_id = c AND f.session_id = sid), '[]'::jsonb),
            'unsure', COALESCE((SELECT p.unsure FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0),
            'new', COALESCE((SELECT p.new_names FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0),
            'guarded', COALESCE((SELECT p.guarded FROM name_pass_reports p
                WHERE p.guild_id = g AND p.campaign_id = c AND p.session_id = sid), 0));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""

SQL = NAMES_FIXED_DOOR

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_names_fixed", "dmbot_recap_names_fixed(BIGINT, TEXT, BIGINT)"),  # (#2684)
)
