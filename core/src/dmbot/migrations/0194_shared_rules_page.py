"""0194_shared_rules_page

The Shared rules page (#2460).
"""

from __future__ import annotations

SHARED_RULES_PAGE = """
    -- The Shared rules page on My Account (docs/PLAN.md, "Where everything is on the website";
    -- #2460 part 2): for one server, each campaign the signed-in person is a DM of that publishes a
    -- house-rules set or homebrew pack, or follows one, in one read. Only the DMs of a campaign see
    -- its sharing (the same people who can change it in Discord); a player gets nothing. It says
    -- whether a share code is on, never the code itself: the code is the way in to the set, so it
    -- is read in Discord. NULL when the caller is not in the server, like the other web doors.
    CREATE FUNCTION dmbot_web_shared_rules(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'id', k.id, 'name', k.name,
                'publishes', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'kind', s.kind, 'name', s.name, 'shared', s.status = 'shared',
                        'version', COALESCE((SELECT max(v.version) FROM rule_set_versions v
                                             WHERE v.guild_id = g AND v.campaign_id = k.id
                                               AND v.set_id = s.id), 1),
                        'hasCode', EXISTS (SELECT 1 FROM rule_set_codes c
                                           WHERE c.guild_id = g AND c.campaign_id = k.id
                                             AND c.set_id = s.id AND c.active))
                        ORDER BY s.kind, lower(s.name), s.id)
                    FROM rule_sets s WHERE s.guild_id = g AND s.campaign_id = k.id), '[]'::jsonb),
                'follows', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'kind', f.kind, 'name', f.set_name, 'publisher', f.publisher,
                        'version', f.version, 'updating', f.updating)
                        ORDER BY f.kind, f.position, lower(f.set_name), f.set_id)
                    FROM rule_set_follows f WHERE f.guild_id = g AND f.campaign_id = k.id),
                    '[]'::jsonb)
            ) ORDER BY lower(k.name), k.id)
            FROM campaigns k
            JOIN campaign_dms d
              ON d.guild_id = k.guild_id AND d.campaign_id = k.id AND d.user_id = me
            WHERE k.guild_id = g
              AND (EXISTS (SELECT 1 FROM rule_sets s WHERE s.guild_id = g AND s.campaign_id = k.id)
                   OR EXISTS (SELECT 1 FROM rule_set_follows f
                              WHERE f.guild_id = g AND f.campaign_id = k.id))), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_shared_rules(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_shared_rules(BIGINT) FROM PUBLIC;
"""

SQL = SHARED_RULES_PAGE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The Shared rules page (#2460).
    ("dmbot_web_shared_rules", "dmbot_web_shared_rules(BIGINT)"),
)
