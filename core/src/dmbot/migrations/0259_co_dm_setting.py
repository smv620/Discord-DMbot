"""0259_co_dm_setting

A game master added or removed is queued (#2768).
"""

from __future__ import annotations

GAME_MASTER_SETTING = r"""
    -- A fifth choice on the website's Settings queue (#2768): a game master added or removed.
    -- `setting` also accepts 'co_dm'; `value` is the person's id (digits, at most 19) and `turn_on`
    -- says add (true) or remove (false), so `turn_on` is now set exactly for the two settings that
    -- carry a yes or no of their own (an optional rule, a game master) and stays NULL for the rest.
    -- No row changes: no backfill policy is needed.
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_setting_check;
    ALTER TABLE setting_actions DROP CONSTRAINT IF EXISTS setting_actions_turn_on_check;
    ALTER TABLE setting_actions
        ADD CONSTRAINT setting_actions_setting_check
            CHECK (setting IN ('visibility', 'level', 'retention', 'optional_rule', 'co_dm')),
        ADD CONSTRAINT setting_actions_turn_on_check
            CHECK ((setting IN ('optional_rule', 'co_dm')) = (turn_on IS NOT NULL));

    -- Queue one game master change. Same guard as `dmbot_web_setting_add`: the caller is a DM of
    -- the campaign in a server on the session's list, and the campaign's queue is not full. Quick
    -- refusals only, so the page answers at once; the bot checks every rule again when it applies
    -- the change (who may, the limit, the plan holder, the last game master, a bot, a person who
    -- has left). An add is for someone who agreed to be recorded in this server, the people the
    -- candidates door lists: a made-up id is 'unknown_person', so no free-typed account is queued.
    -- 'ok' with the id, 'bad_value' for a malformed id or no yes/no, 'plan_holder' for removing
    -- the one whose plan runs the campaign, 'too_many' for an add at the limit (`max_dms`, the
    -- store's MAX_DMS, passed in so the number lives in one place), 'unknown_person', 'too_soon';
    -- NULL for anyone not a DM.
    CREATE FUNCTION dmbot_web_co_dm_add(
        g BIGINT, c TEXT, uid TEXT, on_ BOOLEAN, terms INT, max_dms INT
    )
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        qid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        who BIGINT;
        owner_id BIGINT;
        have INT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        -- The lock first, so two quick adds cannot both pass the limit check below.
        PERFORM pg_advisory_xact_lock(hashtextextended('setting_action:' || g::TEXT || c, 0));
        IF uid IS NULL OR uid !~ '^[1-9][0-9]{0,18}$' OR on_ IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        who := uid::BIGINT;
        SELECT k.owner_user_id INTO owner_id
            FROM campaigns k WHERE k.guild_id = g AND k.id = c;
        IF NOT on_ AND who = owner_id THEN
            RETURN jsonb_build_object('status', 'plan_holder');
        END IF;
        IF on_ THEN
            SELECT count(*) INTO have FROM campaign_dms d
                WHERE d.guild_id = g AND d.campaign_id = c;
            IF have >= max_dms AND NOT EXISTS (SELECT 1 FROM campaign_dms d
                                         WHERE d.guild_id = g AND d.campaign_id = c
                                           AND d.user_id = who) THEN
                RETURN jsonb_build_object('status', 'too_many');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM consent x WHERE x.guild_id = g AND x.user_id = who
                           AND x.terms_version = terms) THEN
                RETURN jsonb_build_object('status', 'unknown_person');
            END IF;
        END IF;
        DELETE FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, turn_on,
                                     created_at)
            VALUES (g, c, qid, dmbot_current_user(), 'co_dm', uid, on_, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', qid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT) FROM PUBLIC;
"""

SQL = GAME_MASTER_SETTING

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A campaign's game masters, and the people who can be offered as one more (#2768).
    ("dmbot_web_co_dm_add", "dmbot_web_co_dm_add(BIGINT, TEXT, TEXT, BOOLEAN, INT, INT)"),
)
