"""0321_web_picture_actions

The website's doors that queue a picture action for the bot and read its answer (#3274).
"""

from __future__ import annotations

WEB_PICTURE_ACTIONS = r"""
    -- A picture's Make private / Make public / Permanent / Delete, chosen on the website's Pictures
    -- page. The website has no right on the picture tables, so this door only checks who is signed
    -- in and queues the choice in `picture_actions` (0320); the bot applies it through its store,
    -- which holds the per-campaign lock, the 80% permanent limit and the file deletion, and checks
    -- again from the database who may do it. Who may ask: the poster or a game master for private,
    -- public and delete; a game master only for permanent (owner, 2026-11-02: only the campaign's
    -- DMs set or clear it, on the website). NULL for anyone not in the campaign's server. A picture
    -- someone may not touch answers 'not_allowed', one that is not there 'not_found': a member can
    -- tell a code exists, which the 256-bit random codes make no use.
    CREATE FUNCTION dmbot_web_picture_act(g BIGINT, c TEXT, link TEXT, act TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        p campaign_pictures%ROWTYPE;
        is_dm BOOLEAN;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns k WHERE k.guild_id = g AND k.id = c) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL
           OR act NOT IN ('public', 'private', 'permanent', 'unpermanent', 'delete') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        SELECT * INTO p FROM campaign_pictures
            WHERE guild_id = g AND campaign_id = c AND code = link AND deleted_at IS NULL;
        IF p.id IS NULL THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF act IN ('permanent', 'unpermanent') THEN
            IF NOT is_dm THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
        ELSIF NOT is_dm AND p.poster_id IS DISTINCT FROM dmbot_current_user() THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('picture_action:' || g::TEXT || c, 0));
        DELETE FROM picture_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        -- (a stuck row older than an hour is dropped here even if the bot never answered it)
        IF (SELECT count(*) FROM picture_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 20
           OR (SELECT count(*) FROM picture_actions a
               WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
                 AND a.state IN ('waiting', 'working')) >= 5
        THEN
            RETURN jsonb_build_object('status', 'too_soon');  -- (one person cannot fill the queue)
        END IF;
        INSERT INTO picture_actions (guild_id, campaign_id, id, user_id, picture_id, action,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), p.id, act, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the person who made it (nobody else's): 'waiting', 'working',
    -- 'done', 'refused' or 'failed', and the `note` to show (the 80% refusal words included). NULL
    -- when there is no such choice of the caller's in this campaign.
    CREATE FUNCTION dmbot_web_picture_action(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r picture_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM picture_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF r.id IS NULL THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'note', r.note, 'action', r.action);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_picture_act(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_picture_action(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_picture_act(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_picture_action(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

SQL = WEB_PICTURE_ACTIONS

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # A picture's Make private/public, Permanent and Delete, queued for the bot (#3274).
    ("dmbot_web_picture_act", "dmbot_web_picture_act(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_picture_action", "dmbot_web_picture_action(BIGINT, TEXT, TEXT)"),
)
