"""0252_optional_rules_read

The Settings tab reads the choices (#2753).
"""

from __future__ import annotations

OPTIONAL_RULES_READ = r"""
    -- A campaign's optional-rule choices for the website's Settings tab (#2753): the main rules it
    -- plays by, the campaign-wide default, and the choices the DM made rule by rule. Read only, DM
    -- of the campaign only (as the other Settings reads), and nothing else leaves it; the API joins
    -- it with the rules catalog. NULL for anyone else.
    CREATE FUNCTION dmbot_web_optional_rules(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        camp campaigns%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'target', camp.target_ruleset,
            'default', camp.optional_rules_default,
            'overrides', COALESCE((
                SELECT jsonb_object_agg(o.rule_id, o.enabled)
                FROM campaign_optional_rules o
                WHERE o.guild_id = g AND o.campaign_id = c), '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_optional_rules(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_optional_rules(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = OPTIONAL_RULES_READ

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_optional_rules", "dmbot_web_optional_rules(BIGINT, TEXT)"),
)
