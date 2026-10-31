"""0300_rulesets_setting

The main rules and the "if missing" rules chosen on the website (#2985).
"""

from __future__ import annotations

RULESETS_SETTING = r"""
    -- A ninth choice on the website's Settings queue (#2985): the campaign's main rules and what
    -- to use when they do not cover something (Discord's "Main rules" and "If missing"). `setting`
    -- also accepts 'rulesets'; `value` is "<main>/<fallback>", for example "2024/2014" or
    -- "2024/none" (none = only the main rules). Both words must be ones Discord offers and the
    -- fallback may not be the main rules themselves. The words are listed here as well as in
    -- `campaigns/models.py` (RULESETS, FALLBACK_NONE); a test keeps the two in step, and the bot
    -- checks again with `check_rulesets`. No row changes: no backfill policy.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm',
                               'pause', 'story_mode', 'dndbeyond_invite', 'rulesets'));

    -- The same door as before (0296), with the new setting.
    CREATE OR REPLACE FUNCTION dmbot_web_setting_add(
        g BIGINT, c TEXT, st TEXT, val TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF st IS NULL OR st NOT IN ('visibility', 'level', 'retention', 'story_mode',
                                    'dndbeyond_invite', 'rulesets') THEN
            RETURN jsonb_build_object('status', 'bad_setting');
        END IF;
        IF val IS NULL
           OR (st = 'visibility' AND val NOT IN ('private', 'peek', 'open'))
           OR (st = 'level' AND val NOT IN ('quiet', 'normal'))
           OR (st = 'retention' AND val NOT IN ('forever', '365', '90', '30'))
           OR (st = 'story_mode' AND val NOT IN ('off', 'watch', 'guide'))
           OR (st = 'dndbeyond_invite' AND val <> ''
               AND val !~ '^https://(www[.])?dndbeyond[.]com/campaigns/join/[A-Za-z0-9]{4,64}$'
              )
           OR (st = 'rulesets'
               AND (val !~ '^(2024|2014)/(2024|2014|none)$'
                    OR split_part(val, '/', 1) = split_part(val, '/', 2))) THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        IF st = 'story_mode' AND val <> 'off' AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, flag,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), st, val, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    -- CREATE OR REPLACE drops the pinned search_path and the grant rule, so both are set again.
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SQL = RULESETS_SETTING
