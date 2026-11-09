"""0404_upload_notice_read

The website reads the notice one upload or import left (#3879): how many names and connections it
added, until when Undo works, and whether it was taken back.
"""

from __future__ import annotations

# One read door, no new table. It finds the notice by (campaign, source_kind, source_id), the
# columns 0399 added, never by "the latest after", and answers NULL unless the caller is a DM of the
# campaign (a game master is a DM of it here too). It sends counts, the upload's label as the DM
# saw it, and times: never the change numbers and never a name. A notice with no source (written
# before 0399, or from Discord) cannot be asked for, so the website shows none. `undoUntil` is
# 7 days after the upload, the store's own limit, and is NULL once the upload was taken back or
# changed nothing; `undone` says it was taken back. The count of how many changes an Undo took back
# is not stored, so the door cannot say it: the page shows its own answer for a press it made.
SQL = r"""
    CREATE FUNCTION dmbot_web_upload_notice(g BIGINT, c TEXT, kind TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        n upload_notices%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT u.* INTO n FROM upload_notices u
            WHERE u.guild_id = g AND u.campaign_id = c
              AND u.source_kind = kind AND u.source_id = sid
            ORDER BY u.created_at DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('state', 'none', 'now', now_s);
        END IF;
        RETURN jsonb_build_object(
            'state', 'done',
            'label', n.label,
            'counts', n.counts,
            'createdAt', n.created_at,
            'undone', n.undone_at IS NOT NULL,
            'undoUntil', CASE WHEN n.undone_at IS NULL AND cardinality(n.changes) > 0
                                   AND now_s - n.created_at < 604800
                              THEN n.created_at + 604800 END,
            'now', now_s);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_upload_notice(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_upload_notice(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_upload_notice", "dmbot_web_upload_notice(BIGINT, TEXT, TEXT, TEXT)"),
)
