"""0254_optional_rule_setting

A rule switched on the website is queued for the bot (#2753).
"""

from __future__ import annotations

OPTIONAL_RULE_SETTING = r"""
    -- A fourth choice on the website's Settings queue (#2753): an optional rule switched on or off.
    -- The queue already carries the three settings (0197); here `setting` also accepts
    -- 'optional_rule', `value` may be the rule's id (the longest is 18 characters), and a new
    -- `turn_on` column says which way. `turn_on` is set exactly for an optional rule and is NULL
    -- for every other setting (so it never shares a meaning with `flag`, the DM's yes to a shorter
    -- transcript limit). No row changes: no backfill policy is needed.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_value_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule')),
        ADD CONSTRAINT setting_actions_value_check CHECK (char_length(value) <= 40),
        ADD COLUMN turn_on BOOLEAN,
        ADD CONSTRAINT setting_actions_turn_on_check
            CHECK ((setting = 'optional_rule') = (turn_on IS NOT NULL));

    -- Queue one optional-rule choice. Same guard as `dmbot_web_setting_add`: the caller is a DM of
    -- the campaign in a server on the session's list, and the campaign's queue is not full. The
    -- API has already checked the id against the catalog (a rule list that lives in Python); the
    -- bot checks again, with the campaign's main rules, when it applies it. 'ok' with the id,
    -- 'bad_value' for a malformed id or no on/off, 'too_soon'; NULL for anyone not a DM.
    CREATE FUNCTION dmbot_web_optional_rule_add(g BIGINT, c TEXT, rid TEXT, on_ BOOLEAN)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF rid IS NULL OR rid !~ '^[a-z0-9-]{1,40}$' OR on_ IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, turn_on,
                                     created_at)
            VALUES (g, c, qid, dmbot_current_user(), 'optional_rule', rid, on_, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', qid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SQL = OPTIONAL_RULE_SETTING

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_optional_rule_add", "dmbot_web_optional_rule_add(BIGINT, TEXT, TEXT, BOOLEAN)"),
)
