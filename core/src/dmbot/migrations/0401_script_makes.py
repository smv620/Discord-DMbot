"""0401_script_makes

"Make the script" from the website (#3899): a session's theatrical script made on a game master's
press, for the sessions that have none (#3901 makes it for the rest by itself).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# A press is a row the bot answers, because the AI key is the bot's, not the web container's
# (#2419): the bot runs the same job as Discord's "Make a script" button (`transcript.theatre_job`)
# and writes the row's state, the part it is on and how many there are, and an error word when it
# stops. The row holds ids, numbers and words of progress only: never a line of the session.
# One run at a time for a session (a partial unique index): a second press while one is going is
# given the running job's id, so a reload or a second tab can never queue a second AI charge. The
# door checks the caller is a DM of the campaign and that the session is the campaign's own; it
# holds the campaign to 10 presses a minute. The bot checks again that the sender still is a DM,
# the plan, the switch and the consent. Rows go after a day. A run that has been waiting or working
# for more than 30 minutes is taken as lost (the bot stopped, or restarted without finishing): the
# door fails it and lets the session be pressed again. `created_at` is in seconds; `claimed_at` is
# the queue's own stamp in milliseconds, as the other waits' (the page's "last moved" divides it).
SQL = (
    r"""
    CREATE TABLE script_makes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        session_id  TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        part        INT NOT NULL DEFAULT 0 CHECK (part >= 0),
        of_parts    INT NOT NULL DEFAULT 0 CHECK (of_parts >= 0),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        tries       INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX script_makes_claim ON script_makes (guild_id, state, created_at);
    CREATE UNIQUE INDEX script_makes_one_running
        ON script_makes (guild_id, session_id) WHERE state IN ('waiting', 'working');
    """
    + _isolate("script_makes")
    + r"""
    -- Where a session's script stands, for a DM of the campaign (NULL for anyone else): whether it
    -- has one, and the id of the press that is making it now (or NULL).
    CREATE FUNCTION dmbot_web_script_make_state(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM transcript_sessions t
                       WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'exists', EXISTS (SELECT 1 FROM theatre_lines l
                              WHERE l.guild_id = g AND l.session_id = sid),
            'running', (SELECT m.id FROM script_makes m
                        WHERE m.guild_id = g AND m.campaign_id = c AND m.session_id = sid
                          AND m.state IN ('waiting', 'working')
                          AND m.created_at
                              >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 1800
                        ORDER BY m.created_at DESC LIMIT 1));
    END
    $fn$;

    -- Queue one press (NULL unless the caller is a DM of the campaign and the session is its own).
    -- 'already' when the session has a script; the id of the run that is going when there is one;
    -- 'too_soon' past 10 presses a minute for the campaign.
    CREATE FUNCTION dmbot_web_script_make_add(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        running TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user())
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        -- one lock for the campaign: the minute's count below cannot be passed by two at once
        PERFORM pg_advisory_xact_lock(hashtextextended('script_make:' || g::TEXT || ':' || c, 0));
        DELETE FROM script_makes m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.created_at < now_s - 86400;
        UPDATE script_makes m SET state = 'failed', error = 'lost'
            WHERE m.guild_id = g AND m.campaign_id = c
              AND m.state IN ('waiting', 'working') AND m.created_at < now_s - 1800;
        IF EXISTS (SELECT 1 FROM theatre_lines l WHERE l.guild_id = g AND l.session_id = sid) THEN
            RETURN jsonb_build_object('status', 'already');
        END IF;
        SELECT m.id INTO running FROM script_makes m
            WHERE m.guild_id = g AND m.session_id = sid AND m.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        IF (SELECT count(*) FROM script_makes m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.created_at >= now_s - 60) >= 10 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO script_makes (guild_id, campaign_id, id, session_id, user_id, created_at)
            VALUES (g, c, rid, sid, dmbot_current_user(), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one press stands, for any DM of the campaign (a reload, or another game master, follows
    -- the same job); NULL when there is none such. Counts and words of progress only.
    CREATE FUNCTION dmbot_web_script_make_read(g BIGINT, c TEXT, mid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r script_makes%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT m.* INTO r FROM script_makes m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.id = mid;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'state', r.state, 'error', r.error, 'part', r.part, 'of', r.of_parts,
            'createdAt', r.created_at, 'claimedAt', r.claimed_at,
            'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_script_make_state(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_script_make_add(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_script_make_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_script_make_state(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_script_make_add(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_script_make_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ("script_makes",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_script_make_state", "dmbot_web_script_make_state(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_script_make_add", "dmbot_web_script_make_add(BIGINT, TEXT, TEXT)"),
    ("dmbot_web_script_make_read", "dmbot_web_script_make_read(BIGINT, TEXT, TEXT)"),
)
