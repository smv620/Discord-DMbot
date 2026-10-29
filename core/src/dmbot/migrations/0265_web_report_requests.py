"""0265_web_report_requests

Make the report again, on the website (#2661).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

WEB_REPORT_REQUESTS = (
    r"""
    -- A DM's "make the report again" for one session, asked for on the website (#2661), waiting
    -- for the bot. The website's role cannot touch the memory tables or call the extractor, so its
    -- door only checks the signed-in DM and queues the request here; the bot reads the row, runs
    -- the same step that makes the report after a session (`report_make.make`, with its plan check
    -- and call limit), and closes the row. How far the making has got is the report's own run
    -- (`memory_report_runs`), which the page already reads: this row only says "asked for, not
    -- taken yet" and, if the bot gave up, "failed". Counts and times only, no word of any story.
    -- One open request per session; a row lives a day at most, is gone with its campaign, left
    -- out of backups and isolated per server.
    CREATE TABLE web_report_requests (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        session_at  BIGINT NOT NULL CHECK (session_at > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_]{0,40}$'),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX web_report_requests_open ON web_report_requests (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    CREATE UNIQUE INDEX web_report_requests_one_open
        ON web_report_requests (guild_id, campaign_id, session_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_report_requests")
    + r"""
    -- Ask for the report of one session again. The same guard as the report's read: a DM of the
    -- campaign, in a server on the session's list (NULL for anyone else). A request already open
    -- for the session comes back as it is (a second press is safe); a report being made right now
    -- (its run touched in the last fifteen minutes, the store's own limit) is 'busy'; five
    -- requests an hour for a campaign is 'too_soon'. A request the bot took and went quiet on for
    -- ten minutes goes back to waiting. 'ok' with the id otherwise. The page checks that the
    -- session is the campaign's own (the door checks it too).
    CREATE FUNCTION dmbot_web_report_make(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
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
        PERFORM pg_advisory_xact_lock(
            hashtextextended('report_request:' || g::TEXT || ':' || c, 0));
        DELETE FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- (Ten minutes quiet puts a taken request back; the page calls a run stalled from fifteen,
        -- the store's own limit, so the DM hears of it before it is put back.)
        UPDATE web_report_requests r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        -- A request nobody took in half an hour (the bot is off or gone) ends as failed, so the
        -- page says so and the next press is not stuck behind it for a day.
        UPDATE web_report_requests r SET state = 'failed', error = 'not_taken', updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'waiting'
              AND r.created_at < now_s - 1800;
        IF NOT EXISTS (SELECT 1 FROM transcript_sessions t
                       WHERE t.guild_id = g AND t.campaign_id = c AND t.started_at = at_) THEN
            RETURN jsonb_build_object('status', 'no_session');
        END IF;
        SELECT r.id INTO have FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_
              AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF EXISTS (SELECT 1 FROM memory_report_runs m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.session_at = at_
                     AND m.state = 'making' AND m.updated_at >= now_s - 900) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM web_report_requests r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at >= now_s - 3600) >= 5 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_report_requests (guild_id, campaign_id, id, user_id, session_at,
                                         created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), at_, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The newest request for one session (within a day), for a DM of the campaign: where it
    -- stands and when, never who asked. NULL when there is none (or for anyone else).
    CREATE FUNCTION dmbot_web_report_request(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_report_requests%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT q.* INTO r FROM web_report_requests q
            WHERE q.guild_id = g AND q.campaign_id = c AND q.session_at = at_
              AND q.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400
            ORDER BY q.seq DESC LIMIT 1;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error,
            'createdAt', r.created_at, 'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_make(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_request(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report_make(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_report_request(BIGINT, TEXT, BIGINT) FROM PUBLIC;
"""
)

SQL = WEB_REPORT_REQUESTS

ISOLATED_TABLES: tuple[str, ...] = ("web_report_requests",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_report_make", "dmbot_web_report_make(BIGINT, TEXT, BIGINT)"),  # (#2661)
    ("dmbot_web_report_request", "dmbot_web_report_request(BIGINT, TEXT, BIGINT)"),
)
