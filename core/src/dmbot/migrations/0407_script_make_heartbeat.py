"""0407_script_make_heartbeat

"Make the script" judges a running press by the bot's heartbeat, and shows a script that DMbot is
making by itself as running (#3899, follow-ups from the bot half's review).
"""

from __future__ import annotations

# Two changes to the doors 0401 made, both by replacing the function whole (their rights stay, and
# the fixed search_path is set again below). No row is changed.
#
# 1. A press is "lost" after 30 minutes without moving. 0401 counted from `created_at`, so a long
#    script that was still running was shown as lost and could be pressed a second time. The bot
#    stamps `claimed_at` (milliseconds) as it goes, so the last sign of life is the later of
#    `created_at` and `claimed_at / 1000` (a waiting press has no `claimed_at`: 0).
# 2. The offer says `auto` while the session's own script run (`theatre_runs`: the automatic
#    script, the Discord button) is running and has moved in the last 30 minutes, so the page shows
#    "making it" instead of offering a second press, and a press made anyway (a stale tab) is
#    refused as `busy` and queues nothing. The run's row holds only ids and counts.
_LAST = "GREATEST(m.created_at, m.claimed_at / 1000)"

SQL = (
    r"""
    CREATE OR REPLACE FUNCTION dmbot_web_script_make_state(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
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
                          AND """
    + _LAST
    + r""" >= now_s - 1800
                        ORDER BY m.created_at DESC LIMIT 1),
            'auto', EXISTS (SELECT 1 FROM theatre_runs r
                            WHERE r.guild_id = g AND r.session_id = sid
                              AND r.state = 'running' AND r.moved_at >= now_s - 1800));
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_script_make_add(g BIGINT, c TEXT, sid TEXT) RETURNS JSONB
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
              AND m.state IN ('waiting', 'working') AND """
    + _LAST
    + r""" < now_s - 1800;
        IF EXISTS (SELECT 1 FROM theatre_lines l WHERE l.guild_id = g AND l.session_id = sid) THEN
            RETURN jsonb_build_object('status', 'already');
        END IF;
        SELECT m.id INTO running FROM script_makes m
            WHERE m.guild_id = g AND m.session_id = sid AND m.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        -- DMbot is making it by itself (the automatic script, Discord's button): never a second run
        IF EXISTS (SELECT 1 FROM theatre_runs r
                   WHERE r.guild_id = g AND r.session_id = sid
                     AND r.state = 'running' AND r.moved_at >= now_s - 1800) THEN
            RETURN jsonb_build_object('status', 'busy');
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

    -- (replacing a function drops its fixed search_path: put it back, as 0401 set it)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_script_make_state(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_script_make_add(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
