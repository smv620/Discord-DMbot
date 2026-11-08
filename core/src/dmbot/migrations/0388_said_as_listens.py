"""0388_said_as_listens

"🎤 Say it" on the website's Names page (#2316): a clip of the DM saying a name, turned into a
respelling by the bot.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The clip is the DM's own voice, held only while it is turned into letters: the bot writes the
# result and clears the clip in the same statement, a failure clears it too, and a row goes within
# an hour (the door and the bot both sweep). The website's container does no listening (the model
# runs in a worker the bot starts), so a press queues a row and the page polls it. Nothing here is
# logged, kept in a transcript or put in a campaign backup (the clip does sit in the database for
# the minute or two it takes, and its dead row until the database tidies up). The door checks the
# caller is a DM of the campaign and holds the campaign to 6 clips a minute and 3 waiting: a clip
# costs the bot some seconds of one processor.
SQL = (
    r"""
    CREATE TABLE said_as_listens (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        container   TEXT NOT NULL CHECK (container IN ('webm', 'ogg', 'wav')),
        clip        BYTEA CHECK (clip IS NULL OR octet_length(clip) <= 1048576),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        respelling  TEXT NOT NULL DEFAULT '' CHECK (char_length(respelling) <= 80),
        ipa         TEXT NOT NULL DEFAULT '' CHECK (char_length(ipa) <= 400),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        tries       INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        CHECK (state NOT IN ('done', 'failed') OR clip IS NULL)
    );
    CREATE INDEX said_as_listens_claim ON said_as_listens (guild_id, state, created_at);
    """
    + _isolate("said_as_listens")
    + r"""
    -- Queue one clip. NULL unless the caller is a DM of the campaign (in a server on the
    -- session's list). Answers 'ok' with the id, 'bad_clip' (empty, over 1 MB or a container we
    -- do not read) or 'too_soon'.
    CREATE FUNCTION dmbot_web_listen_add(g BIGINT, c TEXT, cont TEXT, data BYTEA) RETURNS JSONB
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
        IF cont IS NULL OR cont NOT IN ('webm', 'ogg', 'wav') OR data IS NULL
           OR octet_length(data) NOT BETWEEN 1 AND 1048576 THEN
            RETURN jsonb_build_object('status', 'bad_clip');
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('said_as_listen:' || g::TEXT || ':' || c, 0));
        DELETE FROM said_as_listens l
            WHERE l.guild_id = g AND l.campaign_id = c AND l.created_at < now_s - 3600;
        IF (SELECT count(*) FROM said_as_listens l
            WHERE l.guild_id = g AND l.campaign_id = c AND l.created_at >= now_s - 60) >= 6
           OR (SELECT count(*) FROM said_as_listens l
               WHERE l.guild_id = g AND l.campaign_id = c
                 AND l.state IN ('waiting', 'working')) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO said_as_listens (guild_id, campaign_id, id, user_id, container, clip,
                                     created_at)
            VALUES (g, c, rid, dmbot_current_user(), cont, data, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one clip stands, for the DM who made it (nobody else's); NULL when there is no such
    -- clip of the caller's. Never returns the clip.
    CREATE FUNCTION dmbot_web_listen_read(g BIGINT, c TEXT, lid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r said_as_listens%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT l.* INTO r FROM said_as_listens l
            WHERE l.guild_id = g AND l.campaign_id = c AND l.id = lid
              AND l.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'respelling', r.respelling, 'ipa', r.ipa,
            'createdAt', r.created_at, 'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_listen_add(BIGINT, TEXT, TEXT, BYTEA)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_listen_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_listen_add(BIGINT, TEXT, TEXT, BYTEA) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_listen_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("said_as_listens",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_listen_add", "dmbot_web_listen_add(BIGINT, TEXT, TEXT, BYTEA)"),
    ("dmbot_web_listen_read", "dmbot_web_listen_read(BIGINT, TEXT, TEXT)"),
)
