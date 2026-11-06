"""0149_audio_again_posted

Make it again for a posted recap (#2183).
"""

from __future__ import annotations

RECAP_AUDIO_AGAIN_POSTED = """
    -- "Make it again" for a POSTED recap whose narration no longer matches its text, for example
    -- after a spelling fix (#2183, #2177). The same door as before (#1418, #2083) with one case
    -- more: a recap already posted, whose audio is made (ready) from other words than the text now
    -- (audio_text_hash differs). Everything else is as before: only a DM of the campaign, a text
    -- the DM approved with a choice that makes audio, once every 10 minutes (the database's own
    -- clock). The plan and the month's allowance are checked by the API and again by the audio
    -- step before any paid call. The Discord message already posted keeps its old attachment.
    -- A posted recap whose remake failed may be tried again: a failed making keeps the hash of the
    -- last audio that was ready, which is still not the text's hash now.
    -- A new making starts its own credit count: the cap is for one making, not for all of them.
    CREATE OR REPLACE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        now_hash TEXT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' THEN
            RETURN 'not_allowed';
        END IF;
        now_hash := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        IF job.status = 'posted' THEN
            -- Only audio that was made and no longer matches the text.
            IF job.audio NOT IN ('ready', 'failed') OR job.audio_text_hash = now_hash THEN
                RETURN 'not_allowed';
            END IF;
        ELSIF job.status NOT IN ('draft', 'posting') OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_choice NOT IN ('voice', 'music', 'both') OR job.approved_hash <> now_hash THEN
            RETURN 'needs_approval';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s,
                              audio_credits = CASE WHEN job.audio = 'ready'
                                                   THEN 0 ELSE audio_credits END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    -- Before this, a recap posted while its audio was still queued was left alone: posted, audio
    -- 'queued', nothing ever made. The audio step now takes posted recaps, so those would be
    -- narrated after this deploy with nobody asking, mostly charged to past months. They are
    -- marked failed instead; the DM may ask again from the recap page. Migrations run with no
    -- server set, so the table is opened to this one UPDATE and closed again.
    CREATE POLICY migrate_backfill ON recap_jobs FOR ALL USING (true) WITH CHECK (true);
    UPDATE recap_jobs SET audio = 'failed',
        audio_error = 'The audio wasn''t made before this recap was posted. '
                      'You can make it now for this page.'
        WHERE status = 'posted' AND audio = 'queued';
    DROP POLICY migrate_backfill ON recap_jobs;
"""

SQL = RECAP_AUDIO_AGAIN_POSTED
