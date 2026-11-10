"""0417_upload_undos

The website's "Undo this upload" (#3879): a press is queued for the bot, which takes the upload's
changes back with `undo_upload` (the Discord button's own code) and writes the answer here.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The page never changes a campaign's story itself: a press is a row the bot answers, like the
# trait answers (0411) and Make the script (0401). The row holds ids and the outcome only: never a
# name, a label or a change number. It points at the notice (`notice_id`), which the bot finds
# again; the page names it by its upload (kind and id), as the read door (0404) does. One open undo
# for a notice at a time (a partial unique index), so a second press or tab is given the first one's
# id. `claimed_at` is the bot's own stamp in milliseconds, moved as it works (the heartbeat, as
# 0407 reads it); `created_at` is in seconds. A press waiting or working for 30 minutes without
# moving is taken as lost, and rows go after a day. The door checks the caller is a DM of the
# campaign and that the notice can still be undone (not taken back, changed something, under 7
# days: the same test as `UploadNotice.can_undo`); the bot checks all of it again when applying.
SQL = (
    r"""
    CREATE TABLE upload_undos (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        notice_id   TEXT NOT NULL CHECK (notice_id ~ '^[0-9a-f]{32}$'),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        undone      INT NOT NULL DEFAULT 0 CHECK (undone >= 0),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        tries       INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX upload_undos_claim ON upload_undos (guild_id, state, created_at);
    CREATE UNIQUE INDEX upload_undos_one_open
        ON upload_undos (guild_id, campaign_id, notice_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("upload_undos")
    + r"""
    -- Queue one undo (NULL unless the caller is a DM of the campaign). 'not_found' when the upload
    -- has no notice; 'undone' when it was already taken back or changed nothing; 'too_late' past 7
    -- days; the id of the undo already going when there is one; 'too_soon' past 20 presses a minute
    -- for the campaign.
    CREATE FUNCTION dmbot_web_upload_undo_add(g BIGINT, c TEXT, kind TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        n upload_notices%ROWTYPE;
        running TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF kind NOT IN ('lore', 'import') THEN
            RETURN jsonb_build_object('status', 'bad_value');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('upload_undo:' || g::TEXT || ':' || c, 0));
        DELETE FROM upload_undos u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at < now_s - 86400;
        UPDATE upload_undos u SET state = 'failed', error = 'lost'
            WHERE u.guild_id = g AND u.campaign_id = c AND u.state IN ('waiting', 'working')
              AND GREATEST(u.created_at, u.claimed_at / 1000) < now_s - 1800;
        SELECT x.* INTO n FROM upload_notices x
            WHERE x.guild_id = g AND x.campaign_id = c
              AND x.source_kind = kind AND x.source_id = sid
            ORDER BY x.created_at DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT u.id INTO running FROM upload_undos u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.notice_id = n.id
              AND u.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        IF n.undone_at IS NOT NULL OR cardinality(n.changes) = 0 THEN
            RETURN jsonb_build_object('status', 'undone');
        END IF;
        IF now_s - n.created_at >= 604800 THEN
            RETURN jsonb_build_object('status', 'too_late');
        END IF;
        IF (SELECT count(*) FROM upload_undos u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.created_at >= now_s - 60) >= 20 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO upload_undos (guild_id, campaign_id, id, user_id, notice_id, created_at)
            VALUES (g, c, rid, dmbot_current_user(), n.id, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one undo stands, for any DM of the campaign; NULL when there is none such.
    CREATE FUNCTION dmbot_web_upload_undo_read(g BIGINT, c TEXT, uid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r upload_undos%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT u.* INTO r FROM upload_undos u
            WHERE u.guild_id = g AND u.campaign_id = c AND u.id = uid;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'undone', r.undone,
            'createdAt', r.created_at, 'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_upload_undo_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_upload_undo_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_upload_undo_add(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_upload_undo_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("upload_undos",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_upload_undo_add", "dmbot_web_upload_undo_add(BIGINT, TEXT, TEXT, TEXT)"),
    ("dmbot_web_upload_undo_read", "dmbot_web_upload_undo_read(BIGINT, TEXT, TEXT)"),
)
