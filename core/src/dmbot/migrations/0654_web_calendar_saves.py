"""0654_web_calendar_saves

The website's save of a campaign's calendar, queued for the bot (#5364, slice 3b).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The website's role has no right on the clock table, so a calendar chosen on the Game calendar
# panel is queued and the bot saves it with the same store call Discord's setting makes
# (`ClockStore.set_calendar` / `set_custom_calendar`, which check again that the person is a DM
# and that the campaign has a game clock, and that every value is one the calendar has). A row
# holds the setting as the page sent it (`kind`, the definition, where Day 1 falls): the DM's own
# words for months and weekdays, so it lives a day at most, is never logged, is isolated per
# server, left out of backups (a request, not content) and goes with its campaign and with the
# person who asked (0655). One save open at a time for a campaign. The website checks the body with
# core's own `calendar_setting.from_body` before queuing it, and the bot again before saving.
SQL = (
    r"""
    CREATE TABLE web_calendar_saves (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        body        JSONB NOT NULL
            CHECK (jsonb_typeof(body) = 'object' AND pg_column_size(body) <= 16000),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        CHECK ((state = 'failed') = (error <> ''))
    );
    CREATE INDEX web_calendar_saves_open ON web_calendar_saves (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_calendar_saves_one_open
        ON web_calendar_saves (guild_id, campaign_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_calendar_saves")
    + r"""
    -- Queue one save, for a DM of the campaign in a server on the caller's list (NULL for anyone
    -- else, a player included). 'busy' while another save of this campaign is open; 'too_big' for
    -- a body over the stored cap. 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_calendar_save(g BIGINT, c TEXT, doc JSONB) RETURNS JSONB
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
        IF doc IS NULL OR jsonb_typeof(doc) <> 'object' THEN
            RETURN jsonb_build_object('status', 'bad_body');
        END IF;
        IF pg_column_size(doc) > 16000 THEN
            RETURN jsonb_build_object('status', 'too_big');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('calendar_save:' || g::TEXT || c, 0));
        DELETE FROM web_calendar_saves r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A claim the bot took and went quiet on for ten minutes goes back to waiting (saving
        -- the same calendar twice does no harm).
        UPDATE web_calendar_saves r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        IF EXISTS (SELECT 1 FROM web_calendar_saves r
                   WHERE r.guild_id = g AND r.campaign_id = c
                     AND r.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        INSERT INTO web_calendar_saves (guild_id, campaign_id, id, user_id, body, created_at,
                                        updated_at)
            VALUES (g, c, rid, dmbot_current_user(), doc, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- How one save went, for the DM who asked: its state and error word, and when it was asked and
    -- last moved (the wait panel's "how long" and "last moved"). NULL for anyone else, 'missing'
    -- for a row that is gone. The body is not returned: the page already holds what it sent.
    CREATE FUNCTION dmbot_web_calendar_save_read(g BIGINT, c TEXT, rid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_calendar_saves%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_calendar_saves q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = rid;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('missing', true);
        END IF;
        RETURN jsonb_build_object('id', r.id, 'state', r.state, 'error', r.error,
                                  'createdAt', r.created_at, 'updatedAt', r.updated_at,
                                  'serverNow',
                                  floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_calendar_save(BIGINT, TEXT, JSONB)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_calendar_save_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_calendar_save(BIGINT, TEXT, JSONB) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_calendar_save_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("web_calendar_saves",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_calendar_save", "dmbot_web_calendar_save(BIGINT, TEXT, JSONB)"),
    ("dmbot_web_calendar_save_read", "dmbot_web_calendar_save_read(BIGINT, TEXT, TEXT)"),
)
