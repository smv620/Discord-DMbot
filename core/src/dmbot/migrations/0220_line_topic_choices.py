"""0220_line_topic_choices

A DM puts a left-out stretch back (#2567).
"""

from __future__ import annotations

TOPIC_CHOICES = """
    -- Who put a line back (or left it out again) after the session, and what the off-topic filter
    -- turned back into game talk (#2567, docs/PLAN.md, "Off-topic talk"). Three columns on a
    -- line, all defaulted, so no row changes and no backfill policy is needed:
    --   topic_by / topic_set_at: the DM's choice, final: the filter never flips such a line again;
    --   leaned: the filter's own "when unsure, keep it" pass turned this line back to game;
    --   reread: a put-back line the names and story-memory readers have not read yet.
    -- A line's words are not touched. The raw transcript is unchanged.
    ALTER TABLE transcript_lines
        ADD COLUMN topic_by     BIGINT,
        ADD COLUMN topic_set_at BIGINT,
        ADD COLUMN leaned       BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN reread       BOOLEAN NOT NULL DEFAULT FALSE;

    -- dmbot_web_transcript_stretch: the as-heard words of one person's stretch that was left out,
    -- put back or turned back by the filter, at most 200 lines, for whoever may read this session
    -- (anyone in the server, as for the lines). Only that person's spoken lines between the two
    -- start times, and only ones the filter or a DM touched. NULL when the caller is not in the
    -- server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_stretch(
        g BIGINT, c TEXT, sid TEXT, who BIGINT, from_ms BIGINT, to_ms BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('lines', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('startedMs', x.started_ms, 'heard', x.heard)
                             ORDER BY x.started_ms, x.id)
            FROM (SELECT l.id, l.started_ms, l.heard FROM transcript_lines l
                  WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                    AND l.sidebar IS NULL AND NOT l.typed
                    AND l.started_ms BETWEEN from_ms AND to_ms
                    AND (l.topic = 'off_topic' OR l.leaned OR l.topic_by IS NOT NULL)
                  ORDER BY l.started_ms, l.id LIMIT 200) x),
            '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_choices: how the filter treated a session's spoken lines, and the
    -- stretches a DM decided, for whoever may read the session. Counts only, and each decided
    -- stretch as who, from, to and how many lines (a batch is one person's lines set at one moment
    -- in one direction). NULL when the caller is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_choices(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'kept', (SELECT count(*) FROM transcript_lines l
                     WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                       AND NOT l.typed AND l.topic <> 'off_topic' AND NOT l.leaned
                       AND l.topic_by IS NULL),
            'leanedBack', (SELECT count(*) FROM transcript_lines l
                           WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                             AND NOT l.typed AND l.leaned AND l.topic_by IS NULL),
            'leftOut', (SELECT count(*) FROM transcript_lines l
                        WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                          AND NOT l.typed AND l.topic = 'off_topic'),
            'putBack', (SELECT count(*) FROM transcript_lines l
                        WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                          AND NOT l.typed AND l.topic <> 'off_topic' AND l.topic_by IS NOT NULL),
            'choices', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'speaker', x.user_id::TEXT, 'fromMs', x.first_ms, 'toMs', x.last_ms,
                           'lines', x.n, 'putBack', x.put_back) ORDER BY x.first_ms)
                FROM (SELECT l.user_id, l.topic_set_at, (l.topic <> 'off_topic') AS put_back,
                             min(l.started_ms) AS first_ms, max(l.started_ms) AS last_ms,
                             count(*) AS n
                      FROM transcript_lines l
                      WHERE l.guild_id = g AND l.session_id = sid AND l.sidebar IS NULL
                        AND NOT l.typed AND l.topic_by IS NOT NULL
                      GROUP BY l.user_id, l.topic_set_at, (l.topic <> 'off_topic')
                      ORDER BY min(l.started_ms) LIMIT 200) x),
                '[]'::jsonb));
    END
    $fn$;

    -- dmbot_web_transcript_choose: a DM of the campaign puts one person's stretch back (the lines
    -- count as game talk, so the cleaned file and its downloads include them and the readers take
    -- them up) or leaves it out again. Only that person's spoken lines between the two start
    -- times; "put back" changes lines that are left out, "leave out" only lines a DM put back,
    -- never a plain game line or one the filter itself kept. Who and when are kept. Answers the
    -- number of lines changed, 'not_allowed' for anyone who is not a DM of the campaign, NULL
    -- when the caller is not in the server or there is no such session.
    CREATE FUNCTION dmbot_web_transcript_choose(
        g BIGINT, c TEXT, sid TEXT, who BIGINT, from_ms BIGINT, to_ms BIGINT, back BOOLEAN)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        n INT;
    BEGIN
        IF me IS NULL
           OR dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = me) THEN
            RETURN jsonb_build_object('status', 'not_allowed');
        END IF;
        IF back THEN
            UPDATE transcript_lines l SET topic = 'game', topic_by = me, topic_set_at = now_s,
                   leaned = FALSE, reread = TRUE
                WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                  AND l.sidebar IS NULL AND NOT l.typed
                  AND l.started_ms BETWEEN from_ms AND to_ms AND l.topic = 'off_topic';
        ELSE
            UPDATE transcript_lines l SET topic = 'off_topic', topic_by = me, topic_set_at = now_s,
                   reread = FALSE
                WHERE l.guild_id = g AND l.session_id = sid AND l.user_id = who
                  AND l.sidebar IS NULL AND NOT l.typed
                  AND l.started_ms BETWEEN from_ms AND to_ms AND l.topic = 'game'
                  AND l.topic_by IS NOT NULL;
        END IF;
        GET DIAGNOSTICS n = ROW_COUNT;
        RETURN jsonb_build_object('status', 'ok', 'changed', n);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_stretch'
            '(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_choices(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_choose'
            '(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_stretch(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_choices(BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_choose
        (BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN) FROM PUBLIC;
"""

SQL = TOPIC_CHOICES

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # What was left out of a session, and a DM putting it back (#2567).
    (
        "dmbot_web_transcript_stretch",
        "dmbot_web_transcript_stretch(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT)",
    ),
    ("dmbot_web_transcript_choices", "dmbot_web_transcript_choices(BIGINT, TEXT, TEXT)"),
    (
        "dmbot_web_transcript_choose",
        "dmbot_web_transcript_choose(BIGINT, TEXT, TEXT, BIGINT, BIGINT, BIGINT, BOOLEAN)",
    ),
)
