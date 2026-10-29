"""0224_recap_again

A DM makes or redoes any session's recap (#2608).
"""

from __future__ import annotations

RECAP_AGAIN = r"""
    -- A DM makes or redoes the recap of any past session (#2608).
    --   restarted_at  when the DM last started this recap over (Unix seconds); the month's
    --                 allowance counts a restarted recap in the month it was restarted.
    --   previous      the recap that was approved or posted before the DM started over, kept
    --                 until the new one is approved (words, approval and where it was posted).
    --                 Nothing is read from it once the new text is approved or posted.
    ALTER TABLE recap_jobs ADD COLUMN restarted_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN previous JSONB;
    -- The one door that starts a recap over. It is for the bot, which has checked nothing but
    -- that `who` is the person asking; it checks that `who` is a DM of the campaign, that the
    -- session still has its lines, and that nothing is being written for it now. Not granted to
    -- the website: its door (`dmbot_recap_redo`) passes the signed-in person.
    CREATE FUNCTION dmbot_recap_start_over(
        g BIGINT, c TEXT, at_ BIGINT, who BIGINT, replace_ok BOOLEAN
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        keep JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g OR who IS NULL OR who <= 0
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c AND d.user_id = who) THEN
            RETURN 'not_allowed';
        END IF;
        -- The session's words must still exist: retention may have deleted them.
        IF NOT EXISTS (
            SELECT 1 FROM transcript_sessions s
            WHERE s.guild_id = g AND s.campaign_id = c AND s.started_at = at_
              AND EXISTS (SELECT 1 FROM transcript_lines l
                          WHERE l.guild_id = g AND l.session_id = s.id AND l.sidebar IS NULL)
        ) THEN
            RETURN 'no_lines';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, created_at,
                                    updated_at, restarted_at)
                VALUES (g, c, at_, who, now_s, now_s, now_s);
            RETURN 'queued';
        END IF;
        -- Going out, or a worker has it right now: nothing to start over until that ends. A recap
        -- that waits (a held one, or one between tries) is startable: that is the DM's way out.
        IF job.status = 'posting' OR job.leased_to > now_s THEN
            RETURN 'busy';
        END IF;
        IF job.state = 'ready' AND job.status IN ('draft', 'posted') AND NOT replace_ok THEN
            RETURN 'needs_confirm';
        END IF;
        keep := NULL;
        IF job.state = 'ready' AND (job.status = 'posted' OR job.approved_hash <> '') THEN
            keep := jsonb_build_object(
                'title', job.title, 'summary', job.summary, 'original', job.original,
                'housekeeping', to_jsonb(job.housekeeping), 'music', to_jsonb(job.music),
                'game_day', job.game_day, 'status', job.status,
                'approved_by', job.approved_by, 'approved_at', job.approved_at,
                'approved_hash', job.approved_hash, 'audio_choice', job.audio_choice,
                'speakers', to_jsonb(job.speakers), 'grade', job.grade,
                'posted_url', job.posted_url, 'decided_at', job.decided_at);
        ELSIF job.previous IS NOT NULL AND job.approved_hash = '' AND job.status = 'draft' THEN
            keep := job.previous;  -- started over twice before the new one was approved
        END IF;
        UPDATE recap_jobs SET
            state = 'queued', attempt = 0, errors = 0, restarts = 0, next_at = 0, leased_to = 0,
            calls = 0, window_count = 0, window_done = 0, last_error = '', problems = '{}',
            beats = '[]', title = '', summary = '', original = '', housekeeping = '{}',
            music = '{}', game_day = NULL, status = 'draft', tries = 0, speakers = '{}',
            grade = '{}', emailed_at = NULL, decided_at = NULL, to_confirm = '{}',
            names_state = 0, corrections = '{}', approved_by = 0, approved_at = 0,
            approved_hash = '', audio_choice = '', split = NULL, critics = NULL,
            audio = 'off', audio_tries = 0, audio_next_at = 0, audio_text_hash = '',
            audio_credits = 0, audio_error = '', audio_asked_at = 0, posting_since = NULL,
            posting_step = NULL, posted_url = NULL, respelled_at = NULL,
            dm_user_id = who, updated_at = now_s, previous = keep,
            -- (only a recap the DM chose to redo counts again in the month: a failed or held one
            -- was never counted, and its redo is the same recap)
            restarted_at = CASE WHEN job.state = 'ready' THEN now_s ELSE job.restarted_at END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    -- The website's door: the signed-in person, a DM of the campaign, in a server on their list.
    CREATE FUNCTION dmbot_recap_redo(g BIGINT, c TEXT, at_ BIGINT, replace_ok BOOLEAN)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        RETURN dmbot_recap_start_over(g, c, at_, dmbot_current_user(), replace_ok);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_start_over(BIGINT, TEXT, BIGINT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_start_over(BIGINT, TEXT, BIGINT, BIGINT, BOOLEAN)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN) FROM PUBLIC;
"""

SQL = RECAP_AGAIN

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_recap_redo", "dmbot_recap_redo(BIGINT, TEXT, BIGINT, BOOLEAN)"),  # #2608
)
