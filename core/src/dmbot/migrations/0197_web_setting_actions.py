"""0197_web_setting_actions

Settings chosen on the website (#2463).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
    _memory_scope,
)

SETTING_ACTIONS = (
    f"""
    -- What a DM chose on the website's Settings page (#2463), waiting for the bot: who can see the
    -- DM screen, how much DMbot says, how long transcripts are kept. The website has no right on
    -- the campaign tables, so its door only checks the signed-in DM and queues the choice here; the
    -- bot applies it through the same calls as Discord's ⚙️ Settings buttons (so the channel
    -- permissions, the notices and the live session follow), after checking again that the sender
    -- is still a DM of the campaign. Applied in the order made (`seq`). `value` is the choice
    -- (private/peek/open, quiet/normal, forever/365/90/30); `flag` is the DM's yes to a shorter
    -- transcript limit, which deletes old sessions, so it is asked for first, as in Discord. `note`
    -- is what the DM sees afterwards (the same words Discord says). A row lives an hour at most,
    -- its words are never logged, and it is gone with its campaign, left out of backups, isolated
    -- per server and read back only by the DM who made it.
    CREATE TABLE setting_actions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        setting     TEXT NOT NULL CHECK (setting IN ('visibility', 'level', 'retention')),
        value       TEXT NOT NULL CHECK (char_length(value) <= 10),
        flag        BOOLEAN NOT NULL DEFAULT false,
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 1000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX setting_actions_open ON setting_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("setting_actions")
    + r"""
    -- Queue one choice. The door checks what it can see: the caller is a DM of the campaign, the
    -- setting and its value are ones Discord offers, and the campaign's queue is not full. The bot
    -- checks the rest when it applies it. Answers 'ok' with the id, or 'bad_setting', 'bad_value'
    -- or 'too_soon'; NULL for anyone who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_setting_add(
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
        IF st IS NULL OR st NOT IN ('visibility', 'level', 'retention') THEN
            RETURN jsonb_build_object('status', 'bad_setting');
        END IF;
        IF val IS NULL
           OR (st = 'visibility' AND val NOT IN ('private', 'peek', 'open'))
           OR (st = 'level' AND val NOT IN ('quiet', 'normal'))
           OR (st = 'retention' AND val NOT IN ('forever', '365', '90', '30')) THEN
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
        INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting, value, flag,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), st, val, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the DM who made it (nobody else's): 'waiting', 'working',
    -- 'done' or 'failed' with its reason word, and the notice to show. NULL when there is no such
    -- choice of the caller's in this campaign.
    CREATE FUNCTION dmbot_web_setting_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r setting_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM setting_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'setting', r.setting,
                                  'value', r.value, 'note', r.note);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_setting_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = SETTING_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("setting_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The Settings page's choices (#2463), queued for the bot the same way.
    ("dmbot_web_setting_add", "dmbot_web_setting_add(BIGINT, TEXT, TEXT, TEXT, BOOLEAN)"),
    ("dmbot_web_setting_read", "dmbot_web_setting_read(BIGINT, TEXT, TEXT)"),
)
