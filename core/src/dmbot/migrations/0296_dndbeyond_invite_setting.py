"""0296_dndbeyond_invite_setting

The D&D Beyond invite link chosen on the website (#2985).
"""

from __future__ import annotations

INVITE_SETTING = r"""
    -- An eighth choice on the website's Settings queue (#2985): the campaign's D&D Beyond invite
    -- link (docs/PLAN.md, "The D&D Beyond campaign invite"), set, changed or removed. `setting`
    -- also accepts 'dndbeyond_invite'; `value` is the link in its standard form, or empty to
    -- remove it. The link may be longer than the other settings' words, so the length limit is
    -- 110 characters for this setting (the standard form is at most 105) and stays 40 for the
    -- rest. The invite is the DM's own join link for their table and goes only to that server's
    -- players on a button; the row is gone after an hour and nothing logs it. No row changes: no
    -- backfill policy.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_value_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm',
                               'pause', 'story_mode', 'dndbeyond_invite')),
        ADD CONSTRAINT setting_actions_value_check
            CHECK (char_length(value) <= CASE WHEN setting = 'dndbeyond_invite' THEN 110
                                              ELSE 40 END);

    -- The same door as before (0292), with the new setting: the link must be the one standard
    -- shape (https, dndbeyond.com, /campaigns/join/<code>; `www.` optional), or empty to remove.
    -- The bot checks it again with the same parser as Discord's form and says why in plain words.
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
                                    'dndbeyond_invite') THEN
            RETURN jsonb_build_object('status', 'bad_setting');
        END IF;
        IF val IS NULL
           OR (st = 'visibility' AND val NOT IN ('private', 'peek', 'open'))
           OR (st = 'level' AND val NOT IN ('quiet', 'normal'))
           OR (st = 'retention' AND val NOT IN ('forever', '365', '90', '30'))
           OR (st = 'story_mode' AND val NOT IN ('off', 'watch', 'guide'))
           OR (st = 'dndbeyond_invite' AND val <> ''
               AND val !~ '^https://(www[.])?dndbeyond[.]com/campaigns/join/[A-Za-z0-9]{4,64}$'
              ) THEN
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

SQL = INVITE_SETTING
