"""0393_players_lines_read

The line for the players of a name still waiting to be checked, read for the website (#3833).
"""

from __future__ import annotations

# A lore upload shared with everyone fills each suggested name's line for the players (#2967), and
# the Discord card shows it as "Players know: ..." (#3838). The website's names to check show the
# same, so the DM sees what confirming the name also confirms. The only door that reads it is
# `dmbot_web_players_lines`: like `dmbot_web_scenes` it gives nothing to anyone who is not a DM of
# the campaign, and only for an entry still waiting (`proposed`) and not merged away. No table
# changes and no row is written; at most 200 ids are read a call.
SQL = r"""
    CREATE FUNCTION dmbot_web_players_lines(g BIGINT, c TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'lines', COALESCE((
            SELECT jsonb_object_agg(s.id, s.players_text)
            FROM (SELECT e.id, e.players_text FROM memory_entities e
                  WHERE e.guild_id = g AND e.campaign_id = c
                    AND e.id = ANY (ids[1:200])
                    AND e.status = 'proposed' AND e.merged_into IS NULL
                    AND e.players_text IS NOT NULL AND btrim(e.players_text) <> ''
                  ORDER BY e.id LIMIT 200) s), '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_players_lines(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_players_lines(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_players_lines", "dmbot_web_players_lines(BIGINT, TEXT, TEXT[])"),
)
