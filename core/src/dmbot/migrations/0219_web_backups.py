"""0219_web_backups

The copy of a campaign the website downloads (#2554).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_BACKUPS = (
    """
    -- The copy of a campaign the website downloads (#2554, part 1b). The website's role cannot read
    -- the tables a backup holds, so it asks for a copy here and the bot builds it the way
    -- Discord's Download does (same sections, same packing), from the database alone, so a server
    -- DMbot cannot reach works too. One row is one request: waiting, then the bot reads and packs
    -- (`stage`, with `updated_at` for the shared progress shape of #2564), then the file waits in
    -- `file` (at most 25 MB, what a restore takes) for the person who asked. The row goes when the
    -- file is fetched, or 15 minutes after the request, whichever is first; the bot sweeps the
    -- rest. Only the person who asked can read or fetch it. A copy holds the campaign's secrets,
    -- like any backup, so it is never logged and never kept beyond that; the row is gone with its
    -- campaign, isolated per server, and not itself in a backup.
    CREATE TABLE web_backups (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550): a campaign that changes
        -- server or id carries its waiting copies with it.
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'packing', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        file        BYTEA,
        file_name   TEXT NOT NULL DEFAULT '' CHECK (char_length(file_name) <= 200),
        size        INT NOT NULL DEFAULT 0,
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_backups_open ON web_backups (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_backups")
    + r"""
    -- Ask for a copy. The owner may in any server (the server need not be on the session's list,
    -- as for the delete); a game master of the campaign may in a server the session can reach.
    -- Anyone else gets NULL. The plan (copies are for a paid plan, none for Try It) is judged by
    -- the bot, which owns the plan check, and it says so in the row. A person who already has a
    -- copy on the way gets that one back; at most 5 requests in 15 minutes (a fetched copy
    -- keeps its row, without the file, until then, so fetching does not reset the count).
    CREATE FUNCTION dmbot_web_backup_add(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        camp campaigns%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL OR g IS NULL OR c IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        SELECT * INTO camp FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF camp.owner_user_id IS DISTINCT FROM me THEN
            IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                           WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me)
               OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
                RETURN NULL;
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('web_backup:' || g::TEXT || c || me, 0));
        DELETE FROM web_backups b WHERE b.guild_id = g AND b.created_at < now_s - 900;
        SELECT b.id INTO have FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.user_id = me
              AND (b.state IN ('waiting', 'working') OR (b.state = 'done' AND b.file IS NOT NULL))
            ORDER BY b.created_at DESC LIMIT 1;
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_backups b WHERE b.guild_id = g AND b.campaign_id = c
            AND b.user_id = me) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_backups (guild_id, campaign_id, id, user_id, created_at, updated_at)
            VALUES (g, c, rid, me, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one copy stands, for the person who asked (nobody else): the stage and times for the
    -- shared progress shape, the plain error word, and the file's name and size once it is ready.
    CREATE FUNCTION dmbot_web_backup_read(g BIGINT, c TEXT, bid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- Named columns, so a poll never reads the file itself.
        SELECT b.state, b.stage, b.error, b.file_name, b.size, b.created_at, b.updated_at,
               (b.file IS NOT NULL) AS has_file INTO r
            FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user()
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF r.state = 'done' AND NOT r.has_file THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'fileName', r.file_name, 'size', r.size, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;

    -- The file, once, for the person who asked: it is emptied from the row as it is read. NULL when
    -- it is not theirs, not ready or already taken.
    CREATE FUNCTION dmbot_web_backup_take(g BIGINT, c TEXT, bid TEXT) RETURNS BYTEA
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out BYTEA;
    BEGIN
        IF dmbot_current_user() IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        -- The file leaves the row as it is read; the row stays (empty) for the request count.
        SELECT b.file INTO out FROM web_backups b
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid
              AND b.user_id = dmbot_current_user() AND b.state = 'done' AND b.file IS NOT NULL
              AND b.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 900
            FOR UPDATE;
        IF out IS NULL THEN
            RETURN NULL;
        END IF;
        UPDATE web_backups b SET file = NULL, size = 0
            WHERE b.guild_id = g AND b.campaign_id = c AND b.id = bid;
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_backup_take(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_backup_add(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_backup_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_backup_take(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

SQL = WEB_BACKUPS

ISOLATED_TABLES: tuple[str, ...] = ("web_backups",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The copy of a campaign the bot builds and the website downloads (#2554, part 1b).
    ("dmbot_web_backup_add", "dmbot_web_backup_add(BIGINT, TEXT)"),
    ("dmbot_web_backup_read", "dmbot_web_backup_read(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_backup_take", "dmbot_web_backup_take(BIGINT, TEXT, TEXT)"),
)
