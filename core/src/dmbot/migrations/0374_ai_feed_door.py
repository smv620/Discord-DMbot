"""0374_ai_feed_door

The DM's read of the AI feed (#3710): the website's live, read-only windows on the lore upload and
import pages.
"""

from __future__ import annotations

SQL = r"""
    -- What DMbot asked the AI, and what came back, for one lore upload or one session import of a
    -- campaign (`ai_feed`, 0373): each call's number, time, service and model in plain words, step,
    -- the first 500 characters of the prompt and of the reply (cut in Python before they were
    -- written), whether it is waiting, done or failed, and why. DM-only: a DM or game master of the
    -- campaign is given anything; anyone else, a member who is not a DM included, gets NULL, and so
    -- does a campaign that is not there. One campaign only: every read is scoped to the guild and
    -- campaign, and the job is named by the page. A lore upload's rows are filed under its own id;
    -- an import made several sessions, whose rows are filed under each session's id, so the
    -- import's id is mapped through `session_imports` to every session it made. Returns the calls
    -- written since `after` (a Unix second; the same second again is returned, the page tells them
    -- apart by job and number) plus every call still waiting, which may have finished since it
    -- was first read, at most 200, oldest first, and `lost`, how many calls were not written.
    -- The bookkeeping row (n = 0) is never a call. Nothing is written.
    CREATE FUNCTION dmbot_web_ai_feed(
        g BIGINT, camp TEXT, which TEXT, job TEXT, after_at BIGINT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        jobs TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = camp) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = camp
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF which = 'lore' THEN
            jobs := ARRAY[job];
        ELSIF which = 'import' THEN
            SELECT COALESCE(array_agg(s.session_id), ARRAY[]::TEXT[]) INTO jobs
            FROM session_imports s
            WHERE s.guild_id = g AND s.campaign_id = camp AND s.import_id = job;
        ELSE
            RETURN jsonb_build_object('status', 'bad_kind');
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'lost', COALESCE((SELECT sum(f.lost) FROM ai_feed f
                              WHERE f.guild_id = g AND f.campaign_id = camp AND f.kind = which
                                AND f.job_id = ANY (jobs) AND f.n = 0), 0),
            'entries', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'job', x.job_id, 'n', x.n, 'at', x.at, 'service', x.service,
                           'model', x.model_label, 'step', x.step, 'prompt', x.prompt,
                           'reply', x.reply, 'state', x.state, 'reason', x.reason)
                       ORDER BY x.at, x.job_id, x.n)
                FROM (
                    SELECT f.* FROM ai_feed f
                    WHERE f.guild_id = g AND f.campaign_id = camp AND f.kind = which
                      AND f.job_id = ANY (jobs) AND f.n > 0
                      AND (f.at >= COALESCE(after_at, 0) OR f.state = 'waiting')
                    ORDER BY f.at, f.job_id, f.n
                    LIMIT 200) x), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_ai_feed(BIGINT, TEXT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_ai_feed(BIGINT, TEXT, TEXT, TEXT, BIGINT) FROM PUBLIC;
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_ai_feed", "dmbot_web_ai_feed(BIGINT, TEXT, TEXT, TEXT, BIGINT)"),
)
