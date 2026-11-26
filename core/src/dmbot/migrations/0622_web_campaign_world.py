"""0622_web_campaign_world

The website's Settings page shows the campaign's world (#5238, the website half of #5231).
"""

from __future__ import annotations

# `dmbot_web_campaign_world(g, c)` gives a DM of the campaign (NULL for anyone else) the campaign's
# setting as Discord's settings card reads it (`MemoryStore.setting`): the one confirmed place
# marked as the setting, its id and name, and whether it is only DMbot's guess from the adventure
# so far (`setting_from = 'read'`, not yet kept). `{}` when no world is set. Read-only; a place's
# name is the one thing sent, and it is public to the table.
SQL = r"""
    CREATE FUNCTION dmbot_web_campaign_world(g BIGINT, c TEXT) RETURNS JSONB
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
            SELECT jsonb_build_object('id', e.id, 'name', e.name,
                                      'guessed', e.setting_from IS NOT DISTINCT FROM 'read')
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.setting
              AND e.status = 'confirmed'
            ORDER BY e.id LIMIT 1), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_campaign_world(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_campaign_world(BIGINT, TEXT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_campaign_world", "dmbot_web_campaign_world(BIGINT, TEXT)"),
)
