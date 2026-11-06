"""0216_web_owned_campaigns

The owner's campaigns, any server (#2554).
"""

from __future__ import annotations

WEB_OWNED_CAMPAIGNS = """
    -- The website's two doors for a campaign's owner, in any server (#2554, docs/PLAN.md,
    -- "Everything on the website except picking Discord channels"). The website's role sees a
    -- server only when it is in the session's own list (`web_session_servers`, not widened here):
    -- an owner whose server has gone, or whose bot was removed, could not see or delete their own
    -- campaign. These doors are keyed by the signed-in account and return only list fields, or do
    -- the one delete. New functions only: no rows change, so no row-level-security policy is
    -- needed.

    -- The signed-in account's own campaigns in every server DMbot knows. `owner_campaigns` has the
    -- ids but deliberately no server, so each server of the routing table is looked in, with
    -- that server's scope, for those ids only.
    CREATE FUNCTION dmbot_web_owned_campaigns() RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ids TEXT[];
        g BIGINT;
        found JSONB;
        found_all JSONB := '[]'::jsonb;
        saved TEXT := coalesce(current_setting('dmbot.guild_id', true), '');
    BEGIN
        IF me IS NULL THEN
            RETURN found_all;
        END IF;
        SELECT coalesce(array_agg(campaign_id), '{}') INTO ids
            FROM owner_campaigns WHERE owner_user_id = me;
        IF cardinality(ids) = 0 THEN
            RETURN found_all;
        END IF;
        FOR g IN SELECT guild_id FROM campaign_guilds ORDER BY guild_id LOOP
            PERFORM set_config('dmbot.guild_id', g::text, true);
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'id', c.id, 'guildId', c.guild_id::text, 'name', c.name,
                       'lastPlayedAt', c.last_played_at) ORDER BY c.name), '[]'::jsonb)
                INTO found
                FROM campaigns c
                WHERE c.guild_id = g AND c.id = ANY (ids) AND c.owner_user_id = me;
            found_all := found_all || found;
        END LOOP;
        PERFORM set_config('dmbot.guild_id', saved, true);
        RETURN found_all;
    END
    $fn$;

    -- Delete a campaign from the website: the same rule as Discord's "Delete this campaign"
    -- (#1608, #2556). The owner may, in any server; with no owner, any game master who can reach
    -- the server may. The typed name must match (case and spacing ignored), and not while a
    -- session runs. The answer says which of these stopped it; 'not_found' for a campaign that is
    -- not this person's to know about. The caller connects scoped to the campaign's server, like
    -- every door.
    CREATE FUNCTION dmbot_web_delete_campaign(g BIGINT, c TEXT, typed TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        camp campaigns%ROWTYPE;
        is_dm BOOLEAN;
        had_channels BOOLEAN;
    BEGIN
        IF me IS NULL OR g IS NULL OR c IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) INTO is_dm;
        IF camp.owner_user_id IS DISTINCT FROM me THEN
            -- Not the owner. Someone who is not a game master of it, or cannot reach its server,
            -- is told nothing about it; a game master is told it is not theirs to delete.
            IF NOT is_dm OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            IF camp.owner_user_id IS NOT NULL THEN
                RETURN jsonb_build_object('status', 'not_the_owner');
            END IF;
        END IF;
        -- The typed name arrives as its `name_key` (Python's, the one Discord's delete uses), so
        -- there is one rule for what counts as the same name.
        IF typed IS DISTINCT FROM camp.name_key THEN
            RETURN jsonb_build_object('status', 'name_mismatch');
        END IF;
        IF EXISTS (SELECT 1 FROM active_sessions a WHERE a.guild_id = g AND a.campaign_id = c) THEN
            RETURN jsonb_build_object('status', 'session_running');
        END IF;
        had_channels := camp.dm_screen_channel_id IS NOT NULL
            OR camp.transcript_channel_id IS NOT NULL OR camp.gametime_channel_id IS NOT NULL
            OR camp.dmbchat_channel_id IS NOT NULL OR camp.dm_bot_channel_id IS NOT NULL
            OR camp.rules_channel_id IS NOT NULL OR camp.npcs_channel_id IS NOT NULL;
        -- Every table of the campaign goes with its row: each one's foreign key cascades, as
        -- for Discord's delete.
        DELETE FROM campaigns WHERE guild_id = g AND id = c;
        RETURN jsonb_build_object('status', 'deleted', 'channels', had_channels);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_owned_campaigns() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

SQL = WEB_OWNED_CAMPAIGNS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The owner's own campaigns in any server, and deleting one from the website (#2554).
    ("dmbot_web_owned_campaigns", "dmbot_web_owned_campaigns()"),
    ("dmbot_web_delete_campaign", "dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)"),
)
