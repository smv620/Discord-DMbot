"""0641_web_adventure_gives

Giving a Shared story adventure file from the website (#3694, slice D).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# A file the DM sent from the website, waiting for the bot (the lore PDF's route, #4762): the
# website puts it in the staging bucket under a random key and queues a row here; the bot reads it
# with the same time-limited reader as Discord's 📖 and keeps it with the same call
# (`AdventureStore.add`, which checks again that the person is a DM, that the Shared story switch
# is on and that a DM confirmed the right to use it). The row holds no word of the book: the
# object's key, the file's name (the DM's own word for their file), the size, and a stage in the
# shared progress shape (waiting, reading, saving, done, failed) with the counts when it is done.
# The bot blanks the key when it ends and deletes the object. One open file a campaign; rows live a
# day, go with their campaign and with the person who asked (0642) and are left out of backups.
SQL = (
    r"""
    CREATE TABLE web_adventure_gives (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        object_key  TEXT NOT NULL DEFAULT '' CHECK (object_key ~ '^(a/[0-9a-f]{32}\.[a-z]{1,4})?$'),
        file_name   TEXT NOT NULL CHECK (char_length(file_name) BETWEEN 1 AND 120),
        size_bytes  BIGINT NOT NULL CHECK (size_bytes > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'saving', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        characters  INT NOT NULL DEFAULT 0 CHECK (characters >= 0),
        chunks      INT NOT NULL DEFAULT 0 CHECK (chunks >= 0),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state = 'failed') = (error <> '')),
        CHECK (state IN ('waiting', 'working') OR object_key = ''),
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'reading'),
                                  ('working', 'saving'), ('done', 'done'),
                                  ('failed', 'failed'), ('failed', 'reading'),
                                  ('failed', 'saving'), ('failed', 'waiting')))
    );
    CREATE INDEX web_adventure_gives_open ON web_adventure_gives (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_adventure_gives_one_open ON web_adventure_gives (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_adventure_gives")
    + r"""
    -- Queue a file already put in the staging bucket under `key` (the website's own random key).
    -- A DM of the campaign in a server on the caller's list only (NULL for anyone else). Refuses
    -- with: 'not_on' (the Shared story switch is Off, or no DM has confirmed the right to use the
    -- material here: the page tells the DM to turn it on first), 'too_many' (the campaign holds
    -- its twenty files), 'busy' (a file is already waiting or being read: its id comes back),
    -- 'too_soon' (ten in an hour, a flood guard). 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_adventure_give(
        g BIGINT, c TEXT, okey TEXT, fname TEXT, bytes BIGINT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('adventure_give:' || g::TEXT || c, 0));
        DELETE FROM web_adventure_gives r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A claim the bot took and went quiet on for ten minutes goes back to waiting.
        UPDATE web_adventure_gives r SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_adventure_gives r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'busy', 'id', have);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns k
                       WHERE k.guild_id = g AND k.id = c AND k.story_mode <> 'off')
           OR NOT EXISTS (SELECT 1 FROM shared_confirmations s
                          WHERE s.guild_id = g AND s.campaign_id = c
                            AND s.purpose = 'shared_story' AND NOT s.restored) THEN
            RETURN jsonb_build_object('status', 'not_on');
        END IF;
        IF (SELECT count(*) FROM adventure_documents a
            WHERE a.guild_id = g AND a.campaign_id = c) >= 20 THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        IF (SELECT count(*) FROM web_adventure_gives r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_adventure_gives (guild_id, campaign_id, id, user_id, object_key, file_name,
                                         size_bytes, created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), okey, fname, bytes, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- How one file is going, for the DM of the campaign: the stage, the counts when done, the
    -- error word when it failed, and when it began and last moved (the page's progress, #2564).
    -- Never the object key. NULL for anyone else; 'missing' for a row that is gone.
    CREATE FUNCTION dmbot_web_adventure_give_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_adventure_gives%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_adventure_gives q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'state', r.state, 'stage', r.stage,
            'error', r.error, 'name', r.file_name, 'sizeBytes', r.size_bytes,
            'characters', r.characters, 'chunks', r.chunks, 'createdAt', r.created_at,
            'updatedAt', r.updated_at,
            'serverNow', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_give(BIGINT, TEXT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_adventure_give_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_give(BIGINT, TEXT, TEXT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_adventure_give_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_adventure_gives",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_adventure_give", "dmbot_web_adventure_give(BIGINT, TEXT, TEXT, TEXT, BIGINT)"),
    ("dmbot_web_adventure_give_read", "dmbot_web_adventure_give_read(BIGINT, TEXT, TEXT)"),
)
