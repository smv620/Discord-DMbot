"""0272_web_line_deletions

Delete my transcripts on the website (#2781).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_LINE_DELETIONS = (
    """
    -- A person's own "Delete my transcripts" asked for on the website (#2769), waiting for the bot.
    -- The website's role cannot touch the transcript tables, and the erasure has to hold each
    -- running session's save lock and drop what the person has said and not yet saved, so the bot
    -- does it with the same call as Discord's 🗑 button (`delete_my_lines`). One row is one request:
    -- waiting, then deleting, then done (with the counts it removed) or failed (nothing deleted).
    -- Only the person's id, a server, a stage and two counts: no words. At most one open row for a
    -- person in a server, so a second press while it works gets the same one. Rows go a day after
    -- the request. No campaign: a person's lines belong to the server, so it is isolated by server
    -- only and has no foreign key to move with a campaign.
    CREATE TABLE web_line_deletions (
        guild_id   BIGINT NOT NULL,
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        state      TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage      TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'deleting', 'done', 'failed')),
        error      TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        lines      INT NOT NULL DEFAULT 0,
        sessions   INT NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        claimed_at BIGINT NOT NULL DEFAULT 0,
        updated_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page (which reads the stage) and the counts
        -- (which come with `done`) can never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'deleting'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE UNIQUE INDEX web_line_deletions_one_open ON web_line_deletions (guild_id, user_id)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_line_deletions")
    + r"""
    -- How many lines the signed-in person has in a server, in how many sessions (the numbers the
    -- warning shows, the same count as `TranscriptStore.person_counts`), and the deletion of
    -- theirs that is open, if any. Only their own lines, only in a server on their session's list;
    -- NULL otherwise. Counts only: nothing of the words leaves.
    CREATE FUNCTION dmbot_web_my_lines(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        n_lines BIGINT;
        n_sessions BIGINT;
        open_id TEXT;
        open_state TEXT;
    BEGIN
        IF me IS NULL OR g IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT count(*), count(DISTINCT l.session_id) INTO n_lines, n_sessions
            FROM transcript_lines l WHERE l.guild_id = g AND l.user_id = me;
        SELECT d.id, d.state INTO open_id, open_state FROM web_line_deletions d
            WHERE d.guild_id = g AND d.user_id = me AND d.state IN ('waiting', 'working')
              AND d.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        RETURN jsonb_build_object('lines', n_lines, 'sessions', n_sessions,
            'open', CASE WHEN open_id IS NULL THEN NULL
                         ELSE jsonb_build_object('id', open_id, 'state', open_state) END);
    END
    $fn$;

    -- Ask for the deletion. The same guard as the read. A person with one already open gets that
    -- one back (a second press is safe); with no lines there is nothing to delete
    -- ('nothing_to_delete'); at most 10 requests an hour ('too_soon'). 'ok' with the id otherwise.
    CREATE FUNCTION dmbot_web_my_lines_delete(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL OR g IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('web_line_delete:' || g::TEXT || me, 0));
        DELETE FROM web_line_deletions d WHERE d.guild_id = g AND d.created_at < now_s - 86400;
        -- A request the bot took and then went quiet on for ten minutes (it stopped mid-way) goes
        -- back to waiting, so the next press is not stuck behind it for a day. The bot's own retake
        -- does the same; this only covers a bot that is away.
        UPDATE web_line_deletions d SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE d.guild_id = g AND d.user_id = me AND d.state = 'working'
              AND d.updated_at < now_s - 600;
        SELECT d.id INTO have FROM web_line_deletions d
            WHERE d.guild_id = g AND d.user_id = me AND d.state IN ('waiting', 'working');
        IF FOUND THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM transcript_lines l
                       WHERE l.guild_id = g AND l.user_id = me) THEN
            RETURN jsonb_build_object('status', 'nothing_to_delete');
        END IF;
        IF (SELECT count(*) FROM web_line_deletions d WHERE d.guild_id = g AND d.user_id = me
            AND d.created_at >= now_s - 3600) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_line_deletions (guild_id, id, user_id, created_at, updated_at)
            VALUES (g, rid, me, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where the person's own deletion stands: the stage and times for the shared progress shape,
    -- the plain error word, and (once done) how many lines in how many sessions went. Nobody
    -- else's row; NULL when it is not theirs or is more than a day old.
    CREATE FUNCTION dmbot_web_my_lines_read(g BIGINT, did TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r RECORD;
    BEGIN
        IF dmbot_current_user() IS NULL OR g IS NULL
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT d.state, d.stage, d.error, d.lines, d.sessions, d.created_at, d.updated_at INTO r
            FROM web_line_deletions d
            WHERE d.guild_id = g AND d.id = did AND d.user_id = dmbot_current_user()
              AND d.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'lines', r.lines, 'sessions', r.sessions, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_my_lines(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_my_lines_delete(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_my_lines_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_my_lines(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_my_lines_delete(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_my_lines_read(BIGINT, TEXT) FROM PUBLIC;
    """
)

SQL = WEB_LINE_DELETIONS

ISOLATED_TABLES: tuple[str, ...] = ("web_line_deletions",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_my_lines", "dmbot_web_my_lines(BIGINT)"),
    ("dmbot_web_my_lines_delete", "dmbot_web_my_lines_delete(BIGINT)"),
    ("dmbot_web_my_lines_read", "dmbot_web_my_lines_read(BIGINT, TEXT)"),
)
