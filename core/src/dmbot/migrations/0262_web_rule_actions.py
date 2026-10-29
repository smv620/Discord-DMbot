"""0262_web_rule_actions

Sharing asked for on the website (#2783).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_RULE_ACTIONS = (
    r"""
    -- What a DM chose on the website's House rules page about sharing (#2783), waiting for the
    -- bot: publish the campaign's rules as a set, stop sharing, look at a set by its share code,
    -- follow a set shared in the server or by code, stop following. The website has no right on
    -- the rule tables, so its door only checks the signed-in DM and queues the choice here; the
    -- bot applies it with the same store calls as Discord's buttons (the plan, the caps, the
    -- guess limit and who asked are checked again then). Share codes are never made, turned off
    -- or shown from here: they stay in Discord (#2460). `arg` is the set's name (1 to 60
    -- characters, `publish`), a set's id (`follow_here`) or a share code (`look_code`,
    -- `follow_code`), checked for its shape and nothing else. `note` is what the DM sees after,
    -- in Discord's own words (for `look_code`: the set's name, publisher and how many rules would
    -- be copied, never a rule's words). A row lives a day at most, is gone with its campaign,
    -- left out of backups, isolated per server and read back only by the DM who made it.
    CREATE TABLE web_rule_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL CHECK (action IN ('publish', 'stop_sharing', 'look_code',
                                                    'follow_here', 'follow_code', 'unfollow')),
        arg         TEXT NOT NULL DEFAULT '' CHECK (char_length(arg) <= 64),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'sharing', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        note        TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 1000),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page (which reads the stage) and the
        -- answer (which comes with `done` or `failed`) can never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'sharing'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE INDEX web_rule_actions_open ON web_rule_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_rule_actions")
    + r"""
    -- Queue one choice. The door checks what it can see: the caller is a DM of the campaign, the
    -- action is one Discord offers, its argument has the right shape, and the campaign's queue is
    -- not full. A second press of the same action and argument while one is open gets that one
    -- back. Answers 'ok' with the id, or 'bad_action', 'bad_arg' or 'too_soon'; NULL for anyone
    -- who is not a DM of the campaign.
    CREATE FUNCTION dmbot_web_rule_action_add(g BIGINT, c TEXT, act TEXT, a TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        want TEXT := COALESCE(a, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('publish', 'stop_sharing', 'look_code', 'follow_here',
                                      'follow_code', 'unfollow') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF (act IN ('stop_sharing', 'unfollow') AND want <> '')
           OR (act = 'publish' AND char_length(btrim(want)) NOT BETWEEN 1 AND 60)
           OR (act IN ('look_code', 'follow_code') AND want !~ '^[A-Z2-7]{10}$')
           OR (act = 'follow_here' AND want !~ '^[0-9a-f]{32}$') THEN
            RETURN jsonb_build_object('status', 'bad_arg');
        END IF;
        IF act = 'publish' THEN
            want := btrim(want);  -- the name is stored as it was checked, and as the store keeps it
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('rule_action:' || g::TEXT || c, 0));
        DELETE FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting, so
        -- it is not stuck behind itself for a day; the bot's own retake does the same. (The page
        -- calls it stalled from five minutes, `progress.STALL_AFTER_S`, so the DM hears of it
        -- before it is put back.) The share codes in `arg` are blanked by the bot as it finishes
        -- a row (#2793); the row itself stays a day.
        UPDATE web_rule_actions r SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.user_id = dmbot_current_user()
              AND r.action = act AND r.arg = want AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_rule_actions (guild_id, campaign_id, id, user_id, action, arg,
                                      created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one choice, for the DM who made it (nobody else's): the state, the stage and
    -- times for the shared progress shape, the plain error word, and the note to show. NULL when
    -- there is no such choice of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_rule_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_rule_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM web_rule_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user()
              AND a.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'action', r.action, 'note', r.note, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_rule_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_RULE_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("web_rule_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rule_action_add", "dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_rule_action_read", "dmbot_web_rule_action_read(BIGINT, TEXT, TEXT)"),
)
