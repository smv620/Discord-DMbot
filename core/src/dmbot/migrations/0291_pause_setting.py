"""0291_pause_setting

Pause or unpause a campaign from the website (#2985).
"""

from __future__ import annotations

PAUSE_SETTING = r"""
    -- A sixth choice on the website's Settings queue (#2985): pause or unpause the campaign.
    -- `setting` also accepts 'pause'; `value` stays empty and `turn_on` says pause (true) or
    -- unpause (false), so `turn_on` is now set exactly for the three settings that carry a yes or
    -- no of their own (an optional rule, a game master, a pause). No row changes: no backfill
    -- policy is needed.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_turn_on_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm',
                               'pause')),
        ADD CONSTRAINT setting_actions_turn_on_check
            CHECK ((setting IN ('optional_rule', 'co_dm', 'pause')) = (turn_on IS NOT NULL));

    -- Queue one pause or unpause. Only the campaign's OWNER may (Discord's rule: it is their
    -- plan's room that changes), so a game master who is not the owner gets 'not_owner' and the
    -- page can say so at once; anyone who is not a DM of the campaign gets NULL, as for every
    -- other door. Quick refusals only: the bot checks again when it applies the change (the owner
    -- still, and that an unpause has a free place on the plan: `set_paused`). A second press with
    -- the same choice already waiting gets that one back. 'ok' with the id, 'bad_value' without a
    -- yes or no, 'not_owner', 'too_soon' (ten open at once); NULL for anyone not a DM.
    CREATE FUNCTION dmbot_web_pause_add(g BIGINT, c TEXT, pause_ BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        owner_id BIGINT;
        have TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF pause_ IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        SELECT k.owner_user_id INTO owner_id
            FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF owner_id IS NULL OR owner_id <> dmbot_current_user() THEN
            RETURN jsonb_build_object('status', 'not_owner');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        SELECT a.id INTO have FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.setting = 'pause'
              AND a.user_id = dmbot_current_user()
              AND a.turn_on = pause_ AND a.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, turn_on,
                                     created_at)
            VALUES (g, c, qid, dmbot_current_user(), 'pause', '', pause_, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', qid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_pause_add(BIGINT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_pause_add(BIGINT, TEXT, BOOLEAN) FROM PUBLIC;
"""

SQL = PAUSE_SETTING

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_pause_add", "dmbot_web_pause_add(BIGINT, TEXT, BOOLEAN)"),
)
