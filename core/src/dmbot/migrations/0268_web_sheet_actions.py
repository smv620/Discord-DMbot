"""0268_web_sheet_actions

A character sheet asked for on the website (#2816).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_SHEET_ACTIONS = (
    r"""
    -- A player's character-sheet choice on the website (#2816; the parity row of #2554), waiting
    -- for the bot: link a D&D Beyond sheet, forget it (the player's own), or a DM's read again of
    -- the campaign's sheets. The website never fetches a link: the bot does, with the same store
    -- calls as Discord's buttons, and checks again who plays the character, that they are still in
    -- the server, and the cooldowns. `url` is a character link of the one shape D&D Beyond uses,
    -- checked by the door before anything is queued. A row lives a day, is gone with its campaign,
    -- and is read back only by the person who made it.
    CREATE TABLE web_sheet_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        entity_id   TEXT NOT NULL DEFAULT '' CHECK (entity_id ~ '^([0-9a-f]{32})?$'),
        action      TEXT NOT NULL CHECK (action IN ('link', 'forget', 'read_again')),
        url         TEXT NOT NULL DEFAULT '' CHECK (char_length(url) <= 80),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page and the answer never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'reading'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE INDEX web_sheet_actions_open ON web_sheet_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_sheet_actions")
    + r"""
    -- What the signed-in person may see of a campaign's character sheets: a DM sees every sheet
    -- (read only), a player only the sheets of the characters they play. The summary is the
    -- allow-listed snapshot's names and numbers, read here field by field. NULL for a server the
    -- person is not in, or someone who is neither a DM nor a player in this campaign.
    CREATE FUNCTION dmbot_web_sheet_read(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        plays BOOLEAN;
        found_rows JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        plays := EXISTS (SELECT 1 FROM memory_entities e
                         WHERE e.guild_id = g AND e.campaign_id = c
                           AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                           AND e.status NOT IN ('merged', 'rejected'));
        IF NOT is_dm AND NOT plays THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'entityId', s.entity_id, 'name', e.name,
            'mine', e.played_by = dmbot_current_user(),
            'source', s.source, 'url', s.url, 'fetchedAt', s.fetched_at,
            'summary', CASE WHEN s.sheet IS NULL THEN NULL ELSE jsonb_build_object(
                'species', s.sheet->'species', 'level', s.sheet->'level',
                'classes', s.sheet->'classes', 'maxHp', s.sheet->'max_hp',
                'ac', s.sheet->'ac') END
        ) ORDER BY lower(e.name), s.entity_id), '[]'::jsonb) INTO found_rows
            FROM character_sheets s JOIN memory_entities e
              ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id AND e.id = s.entity_id
            WHERE s.guild_id = g AND s.campaign_id = c
              AND e.status NOT IN ('merged', 'rejected') AND e.played_by IS NOT NULL
              AND e.type <> 'person'
              -- Only the sheet of whoever plays the character now, never a previous player's.
              AND s.player_id = e.played_by
              AND (is_dm OR e.played_by = dmbot_current_user());
        RETURN jsonb_build_object('isDm', is_dm, 'sheets', found_rows);
    END
    $fn$;

    -- Queue one choice. `act` is link (the player's own character, a D&D Beyond character link
    -- in `link`), forget (the player's own sheet) or read_again (a DM, the campaign's sheets, once
    -- a minute). Answers 'ok' with the id; a second press of the same choice while one is open
    -- gets that one back. 'not_found' for a character or sheet that isn't there, 'not_allowed'
    -- when the caller isn't the one who plays it (or a DM, for read_again), 'bad_link' for a link
    -- of any other shape, 'too_soon' inside the link cooldown (20 seconds a person),
    -- 'read_too_soon' inside the read_again cooldown (a minute a campaign) and 'queue_full' when
    -- the queue is full. A link for another URL than an open one is queued on its own. NULL for
    -- a server the person is
    -- not in.
    CREATE FUNCTION dmbot_web_sheet_action_add(g BIGINT, c TEXT, ent TEXT, act TEXT, lnk TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        is_dm BOOLEAN;
        mine BOOLEAN;
        want TEXT := COALESCE(lnk, '');
        who TEXT := COALESCE(ent, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF act IS NULL OR act NOT IN ('link', 'forget', 'read_again') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF act = 'read_again' THEN
            IF NOT is_dm THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            who := '';
            want := '';
        ELSE
            IF who !~ '^[0-9a-f]{32}$' THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            mine := EXISTS (SELECT 1 FROM memory_entities e
                            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = who
                              AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                              AND e.status NOT IN ('merged', 'rejected'));
            IF NOT mine THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            IF act = 'link'
               AND want !~ '^https://www[.]dndbeyond[.]com/characters/[0-9]{1,12}$' THEN
                RETURN jsonb_build_object('status', 'bad_link');
            END IF;
            IF act = 'forget' THEN
                want := '';
                IF NOT EXISTS (SELECT 1 FROM character_sheets s
                               WHERE s.guild_id = g AND s.campaign_id = c
                                 AND s.entity_id = who AND s.player_id = dmbot_current_user()) THEN
                    RETURN jsonb_build_object('status', 'not_found');
                END IF;
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('sheet_action:' || g::TEXT || c, 0));
        DELETE FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE web_sheet_actions a SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state = 'working'
              AND a.updated_at < now_s - 600;
        SELECT a.id INTO have FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.entity_id = who AND a.action = act AND a.url = want
              AND a.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF act = 'link' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.action = 'link' AND a.created_at > now_s - 20) THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        IF act = 'read_again' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.action = 'read_again'
              AND a.created_at > now_s - 60) THEN
            RETURN jsonb_build_object('status', 'read_too_soon');
        END IF;
        IF (SELECT count(*) FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 20
        THEN
            RETURN jsonb_build_object('status', 'queue_full');
        END IF;
        INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, entity_id, action, url,
                                       created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), who, act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one of the caller's own choices: the state and stage for the shared progress
    -- shape, the plain error word, and the character it was about. NULL when there is no such
    -- choice of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_sheet_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_sheet_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user()
              AND a.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'action', r.action, 'entityId', r.entity_id, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

SQL = WEB_SHEET_ACTIONS

ISOLATED_TABLES: tuple[str, ...] = ("web_sheet_actions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The character sheets page (#2816): what a person may see, and the queue for a link, a forget
    # and a DM's read again.
    ("dmbot_web_sheet_read", "dmbot_web_sheet_read(BIGINT, TEXT)"),
    ("dmbot_web_sheet_action_add", "dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_sheet_action_read", "dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)"),
)
