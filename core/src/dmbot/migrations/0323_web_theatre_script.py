"""0323_web_theatre_script

The website's door for a session's theatrical script, for the campaign's game masters (#3244).
"""

from __future__ import annotations

WEB_THEATRE_SCRIPT = r"""
    -- A session's script (`theatre_lines`, 0317) for the game master's download. It is a working
    -- copy of the session made from the cleaned transcript, so it is the DM's alone: NULL for
    -- anyone who is not a DM of this campaign (and for a stranger, and with no server set), as
    -- the other DM-only doors answer. 'none' when no script has been made for the session. Words
    -- and who says them only: the lines it was made from and the speakers it lists stay in the
    -- database. Reads no row it does not name: the session must be this campaign's own.
    CREATE FUNCTION dmbot_web_theatre_script(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM transcript_sessions t
                       WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN jsonb_build_object('status', 'none');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM theatre_lines l WHERE l.guild_id = g AND l.session_id = sid)
        THEN
            RETURN jsonb_build_object('status', 'none');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'madeAt', (SELECT max(l.made_at) FROM theatre_lines l
                       WHERE l.guild_id = g AND l.session_id = sid),
            'lines', (SELECT jsonb_agg(jsonb_build_object('user', l.user_id::TEXT, 'text', l.text)
                                       ORDER BY l.position)
                      FROM theatre_lines l WHERE l.guild_id = g AND l.session_id = sid));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_theatre_script(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_theatre_script(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

SQL = WEB_THEATRE_SCRIPT

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A session's script, for its campaign's game masters (#3244).
    ("dmbot_web_theatre_script", "dmbot_web_theatre_script(BIGINT, TEXT, TEXT)"),
)
