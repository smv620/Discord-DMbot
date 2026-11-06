"""0218_web_delete_notices

The bot finishes a website delete (#2554).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_DELETE_NOTICES = (
    """
    -- What the bot needs to finish a campaign the website deleted (#2554, part 1b): tidy the
    -- Discord channels the way Discord's own delete does, and tell the other game masters. The
    -- website cannot do either, and the campaign's row (with its channel ids) is gone when the bot
    -- hears of it, so the delete door leaves this note first. It holds the campaign's name, who
    -- deleted it, the other game masters' IDs and the channel IDs: nothing of the story. The bot
    -- takes it (deleting the row) within seconds; one it never took is swept after a week. Isolated
    -- per server, never read by the website's role, and left out of backups: it is not campaign
    -- data, and has no key to the campaign (which no longer exists).
    CREATE TABLE web_delete_notices (
        guild_id             BIGINT NOT NULL,
        id                   TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        gone_id              TEXT NOT NULL,
        campaign_name        TEXT NOT NULL CHECK (char_length(campaign_name) <= 200),
        by_user_id           BIGINT NOT NULL CHECK (by_user_id > 0),
        other_dms            BIGINT[] NOT NULL DEFAULT '{}',
        dm_screen_channel_id    BIGINT,
        transcript_channel_id   BIGINT,
        gametime_channel_id     BIGINT,
        dmbchat_channel_id      BIGINT,
        dm_bot_channel_id       BIGINT,
        rules_channel_id        BIGINT,
        npcs_channel_id         BIGINT,
        created_at           BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_delete_notices_waiting ON web_delete_notices (guild_id, created_at);
    """
    + _isolate("web_delete_notices")
    + r"""
    -- The website's delete door again (replaces 0216's): the same checks and the same delete, and
    -- first the note above. The answer is unchanged.
    CREATE OR REPLACE FUNCTION dmbot_web_delete_campaign(g BIGINT, c TEXT, typed TEXT) RETURNS JSONB
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
            IF NOT is_dm OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            IF camp.owner_user_id IS NOT NULL THEN
                RETURN jsonb_build_object('status', 'not_the_owner');
            END IF;
        END IF;
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
        INSERT INTO web_delete_notices (
            guild_id, id, gone_id, campaign_name, by_user_id, other_dms,
            dm_screen_channel_id, transcript_channel_id, gametime_channel_id,
            dmbchat_channel_id, dm_bot_channel_id, rules_channel_id, npcs_channel_id, created_at)
        SELECT g, replace(gen_random_uuid()::TEXT, '-', ''), c, left(camp.name, 200), me,
               COALESCE((SELECT array_agg(d.user_id ORDER BY d.user_id) FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id <> me), '{}'),
               camp.dm_screen_channel_id, camp.transcript_channel_id, camp.gametime_channel_id,
               camp.dmbchat_channel_id, camp.dm_bot_channel_id, camp.rules_channel_id,
               camp.npcs_channel_id, floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        -- The server stays on the routing list until the bot has taken the note, so it is looked
        -- at even if this was its last campaign.
        INSERT INTO campaign_guilds (guild_id) VALUES (g) ON CONFLICT DO NOTHING;
        DELETE FROM campaigns WHERE guild_id = g AND id = c;
        RETURN jsonb_build_object('status', 'deleted', 'channels', had_channels);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_delete_campaign(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)

SQL = WEB_DELETE_NOTICES

ISOLATED_TABLES: tuple[str, ...] = ("web_delete_notices",)
