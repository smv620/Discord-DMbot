"""0377_dm_standings_door

A DM-only door to the DM's own Reputation for each pair, so the website's lines show it (#3642).
"""

from __future__ import annotations

# On a `knows` line the DM's own Reputation for the pair (the step they set with 🤝, an open
# impression with source 'dm') wins over the reader's word, on the DM's screens only (#3642). The
# website's role cannot read `memory_impressions`, so this door hands a DM of the campaign every
# open impression the DM set, as (viewer, subject, score), and the API turns each into a word with
# the same function the Discord card uses (`standing.dm_line_word`). Anyone else, a member who is
# not a DM included, gets NULL: how a name feels about another is the DM's alone (#3610). Ids and
# scores only, no names. Nothing is written.
DM_STANDINGS = r"""
    CREATE FUNCTION dmbot_web_dm_standings(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_array(i.viewer_id, i.subject_id, i.score)
                             ORDER BY i.viewer_id, i.subject_id)
            FROM memory_impressions i
            WHERE i.guild_id = g AND i.campaign_id = c
              AND i.closed_at IS NULL AND i.source = 'dm'), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_dm_standings(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_dm_standings(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = DM_STANDINGS

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_dm_standings", "dmbot_web_dm_standings(BIGINT, TEXT)"),
)
